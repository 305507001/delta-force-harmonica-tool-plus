"""
MIDI 文件的读取与写出。

本模块是自研实现，只依赖 Python 标准库，不引入任何第三方 MIDI 库。
解析路径采用「两遍法」：
    第一遍  把每条音轨的原始事件抽成 (tick, status, payload) 序列；
    第二遍  合并全部轨道的 Tempo 事件构造时间轴，再把 tick 换算成毫秒。
这样即使文件里 Tempo 频繁变化（变速曲），时间也是准确的。

支持：.mid / .midi / .kar / .rmi
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

__all__ = [
    "NOTE_NAMES",
    "DRUM_CHANNEL",
    "Note",
    "Track",
    "Song",
    "MidiError",
    "pitch_name",
    "name_to_pitch",
    "read_midi",
    "parse_midi",
    "write_midi",
]

# --------------------------------------------------------------------------- #
# 常量与音名工具
# --------------------------------------------------------------------------- #

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

#: MIDI 通道号（0 基）为 9 的是打击乐通道
DRUM_CHANNEL = 9

#: 音名 -> 半音偏移
_LETTER_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

#: 默认 Tempo：120 BPM
DEFAULT_TEMPO = 500_000


def pitch_name(pitch: int) -> str:
    """MIDI 音高 -> 科学音名。60 -> ``C4``"""
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def name_to_pitch(text: str) -> Optional[int]:
    """科学音名 -> MIDI 音高。``C4`` -> 60。解析失败返回 None。"""
    s = text.strip()
    if not s:
        return None
    letter = s[0].upper()
    if letter not in _LETTER_SEMITONE:
        return None
    i, accidental = 1, 0
    while i < len(s) and s[i] in "#b♯♭":
        accidental += 1 if s[i] in "#♯" else -1
        i += 1
    try:
        octave = int(s[i:])
    except ValueError:
        return None
    return (octave + 1) * 12 + _LETTER_SEMITONE[letter] + accidental


class MidiError(Exception):
    """MIDI 文件损坏或格式不受支持。"""


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class Note:
    """一个音符。时间单位统一为毫秒。"""

    start: float
    duration: float
    pitch: int
    velocity: int = 80

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def name(self) -> str:
        return pitch_name(self.pitch)

    def copy(self, **kw) -> "Note":
        data = {
            "start": self.start,
            "duration": self.duration,
            "pitch": self.pitch,
            "velocity": self.velocity,
        }
        data.update(kw)
        return Note(**data)


@dataclass
class Track:
    """一条音轨（对应 MIDI 里的一个 MTrk 块）。"""

    index: int
    name: str = ""
    channel: int = 0
    notes: List[Note] = field(default_factory=list)

    @property
    def is_drum(self) -> bool:
        return self.channel == DRUM_CHANNEL

    def span(self) -> Tuple[int, int]:
        """返回 (最低音, 最高音)，无音符时返回 (0, 0)。"""
        if not self.notes:
            return 0, 0
        pitches = [n.pitch for n in self.notes]
        return min(pitches), max(pitches)

    def label(self) -> str:
        return (self.name.strip() or f"音轨 {self.index + 1}") + (" [鼓]" if self.is_drum else "")


@dataclass
class Song:
    """一首曲子 = 多条音轨。"""

    tracks: List[Track] = field(default_factory=list)
    format: int = 1
    division: int = 480

    def length(self) -> float:
        return max((n.end for t in self.tracks for n in t.notes), default=0.0)

    def melody_candidates(self) -> List[Track]:
        """可以作为主旋律的轨道：有音符、非鼓轨。"""
        return [t for t in self.tracks if t.notes and not t.is_drum]

    def pick_melody(self) -> Optional[Track]:
        """
        猜测主旋律轨。

        打分思路（自研启发式）：
          * 单音占比高   —— 旋律通常是一根线，伴奏才会叠和弦
          * 落在口琴音域内的比例高
          * 音符数量适中（太少不像旋律，太多可能是琶音铺底）
          * 音域跨度窄

        返回得分最高的轨道；没有可用轨返回 None。
        """
        best, best_score = None, float("-inf")
        for track in self.melody_candidates():
            notes = sorted(track.notes, key=lambda n: n.start)
            n = len(notes)
            if n == 0:
                continue
            # 单音占比
            stacked = sum(1 for i in range(1, n) if notes[i].start < notes[i - 1].end - 0.5)
            monophony = 1.0 - stacked / n
            # 可奏比例（48=C3 ~ 85=C#6 是口琴音域，这里写成字面量避免循环依赖）
            inrange = sum(1 for x in notes if 48 <= x.pitch <= 85) / n
            lo, hi = track.span()
            span = hi - lo

            score = 0.0
            score += monophony * 3.0
            score += inrange * 2.5
            score += min(n, 400) / 400.0          # 适度偏好音符多的
            score -= max(0, span - 30) * 0.06     # 跨度太大扣分
            if track.channel == DRUM_CHANNEL:
                score -= 99.0
            if score > best_score:
                best, best_score = track, score
        return best


# --------------------------------------------------------------------------- #
# 字节流工具
# --------------------------------------------------------------------------- #


def _read_varlen(buf: bytes, pos: int) -> Tuple[int, int]:
    """读取 MIDI 变长整数，返回 (值, 新位置)。"""
    value = 0
    for _ in range(4):
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            break
    return value, pos


def _write_varlen(value: int) -> bytes:
    """把整数编码成 MIDI 变长整数。"""
    if value < 0:
        raise ValueError("变长整数不能为负")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _strip_riff(data: bytes) -> bytes:
    """RMI 是 RIFF 容器，真正的 MIDI 在 data 块里；其它格式原样返回。"""
    if not data.startswith(b"RIFF"):
        return data
    pos, end = 12, len(data)
    while pos + 8 <= end:
        chunk_id = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if chunk_id == b"data" and body[:4] == b"MThd":
            return body
        pos += 8 + size + (size & 1)
    raise MidiError("RMI 容器里找不到 MIDI 数据块")


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def _scan_track(buf: bytes, pos: int, end: int) -> List[Tuple[int, int, bytes]]:
    """
    扫描一条 MTrk，返回 [(绝对 tick, status, payload), ...]。

    status 保留原始状态字节；payload 为事件参数（不含状态字节本身）。
    Meta 与 SysEx 用特殊 status 标记：
        Meta -> 0xFF，payload = [meta_type, *data]
        SysEx -> 0xF0，payload = 原始数据
    """
    events: List[Tuple[int, int, bytes]] = []
    tick = 0
    running = 0

    while pos < end:
        delta, pos = _read_varlen(buf, pos)
        tick += delta
        if pos >= end:
            break

        status = buf[pos]
        if status < 0x80:
            # Running Status：沿用上一个通道消息的状态字节
            if running == 0:
                break
            status = running
        else:
            pos += 1

        if status == 0xFF:  # Meta
            meta_type = buf[pos]
            pos += 1
            length, pos = _read_varlen(buf, pos)
            payload = bytes([meta_type]) + buf[pos:pos + length]
            pos += length
            events.append((tick, 0xFF, payload))
            if meta_type == 0x2F:  # End of Track
                break
            continue

        if status in (0xF0, 0xF7):  # SysEx
            length, pos = _read_varlen(buf, pos)
            events.append((tick, 0xF0, buf[pos:pos + length]))
            pos += length
            continue

        if status >= 0xF0:  # 其它系统消息，长度不一，保守跳过 1 字节
            pos += 1
            continue

        # 通道消息
        running = status
        kind = status & 0xF0
        if kind in (0xC0, 0xD0):
            payload = buf[pos:pos + 1]
            pos += 1
        else:
            payload = buf[pos:pos + 2]
            pos += 2
        events.append((tick, status, bytes(payload)))

    return events


def _build_timeline(
    all_events: Sequence[Sequence[Tuple[int, int, bytes]]],
    division: int,
    smpte: bool,
) -> Callable[[int], float]:
    """
    依据全局 Tempo 事件构造 tick -> 毫秒 的换算函数。
    SMPTE 时基下与 Tempo 无关，直接线性换算。
    """
    if smpte:
        fps = 256 - (division >> 8)
        sub = division & 0xFF
        ms_per_tick = 1000.0 / (max(1, fps) * max(1, sub))
        return lambda tick: tick * ms_per_tick

    # 收集所有轨道的 tempo 变更
    marks: Dict[int, int] = {}
    for track in all_events:
        for tick, status, payload in track:
            if status == 0xFF and payload and payload[0] == 0x51 and len(payload) >= 4:
                tempo = int.from_bytes(payload[1:4], "big")
                if tempo > 0:
                    marks[tick] = tempo
    if not marks:
        marks = {0: DEFAULT_TEMPO}
    elif 0 not in marks:
        # 开头没有显式速度，沿用第一个出现的
        marks[0] = marks[min(marks)]

    points = sorted(marks.items())
    # 预计算每个 Tempo 段起点的绝对毫秒
    anchors: List[Tuple[int, float, float]] = []
    clock = 0.0
    for i, (tick, tempo) in enumerate(points):
        mpt = (tempo / 1000.0) / division      # 每 tick 多少毫秒
        anchors.append((tick, clock, mpt))
        if i + 1 < len(points):
            clock += (points[i + 1][0] - tick) * mpt

    def tick_to_ms(tick: int) -> float:
        lo, hi = 0, len(anchors) - 1
        while lo < hi:                          # 二分找到所属 Tempo 段
            mid = (lo + hi + 1) // 2
            if anchors[mid][0] <= tick:
                lo = mid
            else:
                hi = mid - 1
        base_tick, base_ms, mpt = anchors[lo]
        return base_ms + (tick - base_tick) * mpt

    return tick_to_ms


def parse_midi(data: bytes) -> Song:
    """把 MIDI 字节流解析成 :class:`Song`。"""
    data = _strip_riff(data)
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiError("缺少 MThd 头，不是有效的 MIDI 文件")

    header_len = struct.unpack_from(">I", data, 4)[0]
    fmt, ntrks, division_raw = struct.unpack_from(">HHH", data, 8)
    smpte = bool(division_raw & 0x8000)
    division = division_raw & 0x7FFF if smpte else division_raw
    if not smpte and division == 0:
        raise MidiError("division 为 0，文件头损坏")

    # 第一遍：扫描各 MTrk
    pos = 8 + header_len
    raw_tracks: List[List[Tuple[int, int, bytes]]] = []
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        chunk_len = struct.unpack_from(">I", data, pos + 4)[0]
        body_start = pos + 8
        if chunk_id == b"MTrk":
            raw_tracks.append(_scan_track(data, body_start, min(body_start + chunk_len, len(data))))
        pos = body_start + chunk_len
        if len(raw_tracks) >= ntrks and ntrks > 0:
            # 头里声明的轨数够了也要继续扫完，防止尾部有多余轨
            pass

    if not raw_tracks:
        raise MidiError("文件中没有可用的 MTrk 轨")

    tick_to_ms = _build_timeline(raw_tracks, division, smpte)

    # 第二遍：把 Note On/Off 配对成 Note
    song = Song(format=fmt, division=division)
    for idx, events in enumerate(raw_tracks):
        track = Track(index=idx)
        pending: Dict[Tuple[int, int], Tuple[float, int]] = {}
        for tick, status, payload in events:
            if status == 0xFF:
                meta_type = payload[0]
                if meta_type == 0x03 and not track.name:      # Track Name
                    track.name = payload[1:].decode("utf-8", "ignore").strip()
                continue
            if status == 0xF0:
                continue
            kind = status & 0xF0
            channel = status & 0x0F
            if kind == 0x90 and len(payload) >= 2:
                pitch, velocity = payload[0], payload[1]
                if velocity == 0:
                    _close_note(track, pending, pitch, channel, tick_to_ms(tick))
                else:
                    pending[(channel, pitch)] = (tick_to_ms(tick), velocity)
                track.channel = channel
            elif kind == 0x80 and len(payload) >= 2:
                _close_note(track, pending, payload[0], channel, tick_to_ms(tick))
        # 文件结束仍未收尾的音符给一个最短时长
        for (channel, pitch), (start_ms, velocity) in pending.items():
            track.notes.append(Note(start_ms, 60.0, pitch, velocity))
        track.notes.sort(key=lambda n: (n.start, n.pitch))
        song.tracks.append(track)

    return song


def _close_note(
    track: Track,
    pending: Dict[Tuple[int, int], Tuple[float, int]],
    pitch: int,
    channel: int,
    end_ms: float,
) -> None:
    """配对 Note On -> Note Off，落成一个 Note。"""
    key = (channel, pitch)
    item = pending.pop(key, None)
    if item is None:
        return
    start_ms, velocity = item
    duration = end_ms - start_ms
    if duration <= 0:
        duration = 30.0          # 零时长音符给个下限，避免产生空按键
    track.notes.append(Note(start_ms, duration, pitch, velocity))


def read_midi(path: str) -> Song:
    """从文件读取 MIDI。"""
    with open(path, "rb") as fp:
        return parse_midi(fp.read())


# --------------------------------------------------------------------------- #
# 写出
# --------------------------------------------------------------------------- #


def _track_chunk(events: List[Tuple[int, bytes]], name: str, division: int) -> bytes:
    """把 (绝对tick, 原始事件字节) 列表打包成一个 MTrk 块。"""
    body = bytearray()
    if name:
        raw = name.encode("utf-8")[:120]
        body += _write_varlen(0) + b"\xff\x03" + _write_varlen(len(raw)) + raw

    last_tick = 0
    for tick, blob in events:
        body += _write_varlen(max(0, tick - last_tick)) + blob
        last_tick = tick
    body += _write_varlen(0) + b"\xff\x2f\x00"       # End of Track
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def write_midi(song: Song, path: str, tempo_bpm: float = 120.0) -> None:
    """
    把 :class:`Song` 写回标准 MIDI 文件（format 1）。

    所有音符重新量化到曲子的第一条轨道网格上，只输出 Note On/Off 与速度信息，
    用于把编辑后的结果留档。
    """
    division = song.division or 480
    us_per_quarter = int(60_000_000 / max(1.0, tempo_bpm))
    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, 1, len(song.tracks) + 1, division))

    # 轨 0：速度轨
    out += _track_chunk([(0, b"\xff\x51\x03" + us_per_quarter.to_bytes(3, "big"))], "Tempo", division)

    for track in song.tracks:
        events: List[Tuple[int, bytes]] = []
        for note in track.notes:
            on_tick = int(round(note.start * division * tempo_bpm / 60_000))
            off_tick = int(round(note.end * division * tempo_bpm / 60_000))
            ch = track.channel & 0x0F
            events.append((on_tick, bytes([0x90 | ch, note.pitch & 0x7F, max(1, note.velocity & 0x7F)])))
            events.append((off_tick, bytes([0x80 | ch, note.pitch & 0x7F, 0])))
        events.sort(key=lambda e: e[0])
        out += _track_chunk(events, track.name, division)

    with open(path, "wb") as fp:
        fp.write(bytes(out))
