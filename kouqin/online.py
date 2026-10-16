"""
在线曲库：只读访问 Dr-hydra「Delta Force Harmonica」项目的公开曲谱库。

曲谱由玩家上传，版权归各自的原作者 / 上传者所有；本程序只在用户点开某首曲子时
下载那一首，在本机演奏或跟弹，不上传、不改动任何数据。接入已征得曲库作者同意。

公开曲库是一组静态文件（JSONP：``__dfh("key", {...});``），按三层读取::

    index.js           {"v": 版本, "n": 总数, "t": 更新时间, "s": {分片: 条数}}
    catalog/<分片>.js  {"r": [[id, 曲名, 作曲, 上传者, 头像, 标签, 难度, BPM, 时长ms, 音数, 更新时间], ...]}
    score/<id>.js      {"i": id, "t": 曲名, ..., "p": 谱面数据（base64url）}

谱面数据是 DFHS1 格式：raw deflate 压缩的一串 varint——
魔数 "DFHS"、版本、PPQ、移调、速度表、拍号、小节线、音符（起点增量 / 时值 / 音高）。
这里的解码是按格式自己写的（曲库项目的代码没有开源许可证，不照搬）。

只用标准库：urllib 联网，zlib 解压，json 解析。
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .smf import Note
from .tune import Tune

__all__ = [
    "DEFAULT_BASE",
    "SOURCE_NAME",
    "SOURCE_URL",
    "OnlineError",
    "OnlineEntry",
    "Snapshot",
    "OnlineLibrary",
    "decode_dfhs",
    "unwrap_jsonp",
    "monophonic",
    "search",
    "to_tune",
]

#: 曲库公开文件的地址（曲库作者的网页 / 桌面版读的也是这里；地址变了可以在设置里改）
DEFAULT_BASE = "https://656e-endfield-d3gdy9wg4afba9d16-1474357318.tcb.qcloud.la/harmonica"

SOURCE_NAME = "Dr-hydra / Delta Force Harmonica 公开曲库"
SOURCE_URL = "https://github.com/Dr-hydra/Delta-Force-Harmonica"

#: 目录多久刷新一次（秒）。曲库更新不频繁，没必要每次打开都去拉
CATALOG_TTL = 3600

_USER_AGENT = "KouqinStudio/2.5 (+https://github.com/305507001/delta-force-harmonica-tool-plus)"
_JSONP = re.compile(r'^\s*__dfh\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*(.*?)\s*\)\s*;?\s*$', re.S)
_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


class OnlineError(Exception):
    """联网、解析、解码出错时抛出，消息直接给用户看。"""


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #


@dataclass
class OnlineEntry:
    """目录里的一行。"""

    id: str
    title: str
    composer: str = ""
    uploader: str = "匿名玩家"
    tags: List[str] = field(default_factory=list)
    difficulty: int = 0           # 0 = 上传者没标
    bpm: float = 120.0
    duration_ms: float = 0.0
    note_count: int = 0
    updated_at: int = 0           # 毫秒时间戳

    @property
    def duration_text(self) -> str:
        total = max(0, int(round(self.duration_ms / 1000)))
        return f"{total // 60}:{total % 60:02d}"

    @property
    def updated_text(self) -> str:
        if not self.updated_at:
            return ""
        return time.strftime("%Y-%m-%d", time.localtime(self.updated_at / 1000))

    @staticmethod
    def from_row(row: Sequence) -> "OnlineEntry":
        """catalog 里的紧凑行：[id, 曲名, 作曲, 上传者, 头像, 标签, 难度, BPM, 时长, 音数, 更新时间]"""
        def at(index, default=None):
            return row[index] if index < len(row) else default

        tags = at(5) if isinstance(at(5), list) else []
        bpm = _num(at(7))
        return OnlineEntry(
            id=str(at(0) or ""),
            title=str(at(1) or "") or "（无标题）",
            composer=str(at(2) or ""),
            uploader=str(at(3) or "") or "匿名玩家",
            tags=[str(t) for t in tags if str(t)],
            difficulty=int(_num(at(6))),
            bpm=bpm if bpm > 0 else 120.0,
            duration_ms=_num(at(8)),
            note_count=int(_num(at(9))),
            updated_at=int(_num(at(10))),
        )


@dataclass
class Snapshot:
    """解码后的谱面。音符的起点 / 时值都已换算成毫秒（按谱面自己的速度表）。"""

    ppq: int
    transpose: int
    tempos: List[Tuple[float, float, float]]          # (拍, 毫秒, BPM)
    signatures: List[Tuple[float, int, int]]          # (拍, 分子, 分母)
    measures: List[float]                             # 每小节起点（拍）
    notes: List[Note]

    @property
    def bpm(self) -> float:
        return self.tempos[0][2] if self.tempos else 120.0


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _js_round(value: float) -> float:
    """和曲库的网页端一样按 Math.round 取整（.5 一律往大的方向）。"""
    import math
    return math.floor(value + 0.5)


# --------------------------------------------------------------------------- #
# DFHS1 解码
# --------------------------------------------------------------------------- #


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def u8(self) -> int:
        if self.pos >= len(self.data):
            raise OnlineError("谱面数据不完整")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def varint(self) -> int:
        result, shift = 0, 1
        while True:
            byte = self.u8()
            result += (byte & 0x7F) * shift
            if not byte & 0x80:
                return result
            shift *= 128
            if shift > (1 << 49):
                raise OnlineError("谱面数据里的数字大得离谱，可能已损坏")


def _unzigzag(value: int) -> int:
    return value // 2 if value % 2 == 0 else -(value + 1) // 2


def _beat_to_ms(beat: float, tempos: Sequence[Tuple[float, float, float]]) -> float:
    target = max(0.0, beat)
    active = tempos[0] if tempos else (0.0, 0.0, 120.0)
    for tempo in tempos:
        if tempo[0] > target:
            break
        active = tempo
    return active[1] + (target - active[0]) * 60000.0 / active[2]


def decode_dfhs(data: bytes) -> Snapshot:
    """把 DFHS1 谱面数据（压缩后的字节）解成 :class:`Snapshot`。"""
    try:
        raw = zlib.decompress(data, -15)          # raw deflate，没有 zlib 头
    except zlib.error as exc:
        raise OnlineError(f"谱面数据解压失败：{exc}") from exc
    reader = _Reader(raw)
    if bytes(reader.u8() for _ in range(4)) != b"DFHS":
        raise OnlineError("不是 DFHS 谱面")
    version = reader.u8()
    if version != 1:
        raise OnlineError(f"暂不支持第 {version} 版谱面格式，请更新程序")

    ppq = reader.varint()
    if not 24 <= ppq <= 9600:
        raise OnlineError("谱面的 PPQ 不合理")
    transpose = _unzigzag(reader.varint())

    tempos: List[Tuple[float, float, float]] = []
    tick = 0
    clock = 0.0
    count = reader.varint()
    if count > 4096:
        raise OnlineError("谱面里的速度变化太多")
    for index in range(count):
        tick += reader.varint()
        bpm = reader.varint() / 100.0
        beat = tick / ppq
        if index > 0:
            prev_beat, _prev_ms, prev_bpm = tempos[-1]
            clock += (beat - prev_beat) * 60000.0 / prev_bpm
        if bpm <= 0:
            raise OnlineError("谱面里有速度为 0 的段落")
        tempos.append((beat, _js_round(clock), bpm))
    if not tempos:
        tempos.append((0.0, 0.0, 120.0))

    signatures: List[Tuple[float, int, int]] = []
    tick = 0
    count = reader.varint()
    if count > 1024:
        raise OnlineError("谱面里的拍号变化太多")
    for _ in range(count):
        tick += reader.varint()
        signatures.append((tick / ppq, reader.varint(), reader.varint()))
    if not signatures:
        signatures.append((0.0, 4, 4))

    measures: List[float] = []
    tick = 0
    count = reader.varint()
    if count > 100_000:
        raise OnlineError("谱面里的小节太多")
    for _ in range(count):
        tick += reader.varint()
        measures.append(tick / ppq)

    notes: List[Note] = []
    tick = 0
    count = reader.varint()
    if count > 1_000_000:
        raise OnlineError("谱面里的音符太多")
    for _ in range(count):
        tick += reader.varint()
        duration_ticks = reader.varint()
        pitch = reader.u8()
        beat = tick / ppq
        beats = max(1.0 / ppq, duration_ticks / ppq)
        start = _beat_to_ms(beat, tempos)
        end = _beat_to_ms(beat + beats, tempos)
        notes.append(Note(start=_js_round(start), duration=max(1.0, _js_round(end - start)),
                          pitch=pitch))

    return Snapshot(ppq, transpose, tempos, signatures, measures, notes)


def unwrap_jsonp(text: str) -> Tuple[str, object]:
    """``__dfh("key", {...});`` → ``(key, 数据)``"""
    match = _JSONP.match(text)
    if not match:
        raise OnlineError("曲库返回的内容不是预期的格式（地址可能变了）")
    try:
        return match.group(1), json.loads(match.group(2))
    except json.JSONDecodeError as exc:
        raise OnlineError(f"曲库数据解析失败：{exc}") from exc


def _b64url(text: str) -> bytes:
    padded = text.replace("-", "+").replace("_", "/")
    padded += "=" * ((4 - len(padded) % 4) % 4)
    try:
        return base64.b64decode(padded)
    except ValueError as exc:
        raise OnlineError("谱面数据编码不对") from exc


def monophonic(notes: Sequence[Note]) -> List[Note]:
    """
    收成单声部：口琴一次只能出一个音。

    规则和曲库的网页端一致——同一时刻起的几个音（和弦）只留最高的那个；
    前一个音拖到下一个音起音之后的，在下一个音起音处截断；起音位置都不动。
    """
    by_start: Dict[float, Note] = {}
    for note in notes:
        kept = by_start.get(note.start)
        if kept is None or note.pitch > kept.pitch:
            by_start[note.start] = note
    ordered = [by_start[start] for start in sorted(by_start)]
    result: List[Note] = []
    for index, note in enumerate(ordered):
        duration = note.duration
        if index + 1 < len(ordered):
            duration = min(duration, ordered[index + 1].start - note.start)
        result.append(Note(start=note.start, duration=max(1.0, duration), pitch=note.pitch,
                           velocity=note.velocity))
    return result


def search(entries: Sequence[OnlineEntry], query: str) -> List[OnlineEntry]:
    """在曲名、作曲、上传者、标签里找（不分大小写）。"""
    needle = (query or "").strip().lower()
    if not needle:
        return list(entries)
    words = needle.split()
    found: List[OnlineEntry] = []
    for entry in entries:
        haystack = "\n".join([entry.title, entry.composer, entry.uploader, *entry.tags]).lower()
        if all(word in haystack for word in words):
            found.append(entry)
    return found


def to_tune(entry: OnlineEntry, notes: Sequence[Note], transpose: int = 0) -> Tune:
    """
    在线曲谱 → 本地曲库里的「原样音符」曲子。

    音符不量化成格子（格子谱表达不了半拍、升降半音、变速），谱面自带的移调直接算进音高，
    出处（曲库、曲谱 ID、上传者）记在 ``source`` 里，曲库页会标出来。
    """
    baked = [Note(start=n.start, duration=n.duration, pitch=max(0, min(127, n.pitch + transpose)),
                  velocity=n.velocity) for n in notes]
    return Tune(
        name=entry.title,
        bpm=entry.bpm if entry.bpm > 0 else 120.0,
        beats_per_bar=4,
        notes=baked,
        source={
            "kind": "online",
            "library": SOURCE_NAME,
            "id": entry.id,
            "title": entry.title,
            "composer": entry.composer,
            "uploader": entry.uploader,
        },
    )


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #


class OnlineLibrary:
    """
    在线曲库客户端（只读），带本地缓存：

        目录   缓存一小时，之后打开会自动刷新；点「刷新」立刻重新拉
        曲谱   下载过的存在本地，再次演奏不联网；断网时缓存里的照样能用

    所有方法都是阻塞的，界面要放在后台线程里调用。
    """

    def __init__(self, base: str, cache_dir: str, timeout: float = 15.0) -> None:
        self.base = (base or DEFAULT_BASE).strip().rstrip("/")
        self.cache_dir = cache_dir
        self.timeout = timeout
        self._lock = threading.Lock()

    # ---------------- 网络 ----------------

    def _get(self, key: str, freshness: int) -> Optional[object]:
        """取一个公开对象；404 返回 None。``freshness`` 秒内用同一个缓存键，和曲库网页的读法一样。"""
        bucket = int(time.time() * 1000) // (freshness * 1000)
        url = f"{self.base}/{key}.js?t={bucket}"
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise OnlineError(f"曲库请求失败（HTTP {exc.code}）") from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OnlineError(f"连不上在线曲库：{reason}") from exc
        return unwrap_jsonp(text)[1]

    # ---------------- 缓存 ----------------

    def _cache_path(self, *parts: str) -> str:
        return os.path.join(self.cache_dir, *parts)

    def _read_cache(self, path: str) -> Optional[dict]:
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, path: str, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass                                    # 缓存写不进去不影响使用

    # ---------------- 目录 ----------------

    def cached_catalog(self) -> Tuple[List[OnlineEntry], float]:
        """本地缓存的目录和它的抓取时间（没有缓存返回空表和 0）。"""
        data = self._read_cache(self._cache_path("catalog.json"))
        if not data or data.get("base") != self.base:
            return [], 0.0
        entries = []
        for raw in data.get("entries", []):
            try:
                entries.append(OnlineEntry(**raw))
            except TypeError:
                continue
        return entries, float(data.get("fetched", 0.0))

    def catalog(self, force: bool = False) -> List[OnlineEntry]:
        """
        取完整目录（最近更新的排前面）。

        缓存没过期就直接用缓存；要联网时并发拉 16 个分片。
        联网失败但有旧缓存的话，退回旧缓存，不让整个曲库页用不了。
        """
        cached, fetched = self.cached_catalog()
        if cached and not force and time.time() - fetched < CATALOG_TTL:
            return cached
        try:
            entries = self._fetch_catalog()
        except OnlineError:
            if cached:
                return cached
            raise
        self._write_cache(self._cache_path("catalog.json"), {
            "base": self.base, "fetched": time.time(),
            "entries": [asdict(entry) for entry in entries],
        })
        return entries

    def _fetch_catalog(self) -> List[OnlineEntry]:
        index = self._get("index", 60)
        if not isinstance(index, dict):
            raise OnlineError("在线曲库的索引不存在（地址可能变了，可以在设置里改）")
        shards = sorted(k for k, v in (index.get("s") or {}).items() if _num(v) > 0)

        def load(shard: str) -> List[OnlineEntry]:
            page = self._get(f"catalog/{shard}", 60)
            rows = page.get("r", []) if isinstance(page, dict) else []
            return [OnlineEntry.from_row(row) for row in rows if isinstance(row, list)]

        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(load, shards))
        entries = [entry for page in pages for entry in page if _ID.match(entry.id)]
        entries.sort(key=lambda e: (-e.updated_at, e.title))
        return entries

    # ---------------- 曲谱 ----------------

    def score(self, entry_id: str) -> Tuple[OnlineEntry, Snapshot]:
        """下载（或从缓存取）一首曲谱并解码。"""
        if not _ID.match(entry_id or ""):
            raise OnlineError("曲谱 ID 不对")
        path = self._cache_path("scores", f"{entry_id}.json")
        data = self._read_cache(path)
        if not data or not data.get("p"):
            data = self._get(f"score/{entry_id}", 300)
            if not isinstance(data, dict):
                raise OnlineError("这首曲谱不存在或已经下架")
            if not data.get("p"):
                raise OnlineError("这首曲谱缺少谱面数据")
            self._write_cache(path, data)
        snapshot = decode_dfhs(_b64url(str(data["p"])))
        entry = OnlineEntry.from_row([
            data.get("i") or entry_id, data.get("t"), data.get("c"), data.get("u"), data.get("a"),
            data.get("g"), data.get("d"), data.get("b"), data.get("l"), data.get("n"), data.get("m"),
        ])
        return entry, snapshot

    def playable_notes(self, entry_id: str) -> Tuple[OnlineEntry, List[Note], int]:
        """``(目录信息, 收成单声部的音符, 谱面自带的移调)``，直接交给演奏 / 跟弹。"""
        entry, snapshot = self.score(entry_id)
        return entry, monophonic(snapshot.notes), snapshot.transpose

    def is_cached(self, entry_id: str) -> bool:
        return os.path.exists(self._cache_path("scores", f"{entry_id}.json"))
