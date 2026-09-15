"""
本地试听。

走 Windows 多媒体接口（MCI）的 ``sequencer`` 设备，直接用系统自带的
MIDI 合成器放音，不需要任何音频库，也完全独立于演奏通道——
试听时不会发送任何按键、不会倒计时、不会抢窗口焦点。

注意：MCI 只能播放"文件"。所以试听前会把当前（可能已被编辑过的）
音符表临时写成一个 .mid 再播放。这样"改完谱直接听"和"输出到游戏"
听到的是同一份内容。
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from typing import Optional

from .smf import Song, Track, Note, write_midi

__all__ = ["Preview", "midi_available"]

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("试听功能仅支持 Windows")

_winmm = ctypes.WinDLL("winmm", use_last_error=True)
_buf = ctypes.create_unicode_buffer(256)

_ALIAS = "kouqin_preview"


def _mci(command: str, want_reply: bool = False) -> tuple:
    """执行一条 MCI 命令，返回 (成功?, 返回文本)。"""
    _winmm.mciSendStringW(ctypes.c_wchar_p(command), _buf, 255, None)
    text = _buf.value
    if not text:
        return True, ""
    low = text.lower()
    if low.startswith("error") or "problem" in low:
        return False, text
    return True, text


def midi_available() -> bool:
    """探测系统是否有可用的 MIDI 输出设备。"""
    ok, _ = _mci(f'open " " type sequencer alias {_ALIAS}_probe')
    if ok:
        _mci(f"close {_ALIAS}_probe")
        return True
    return False


class Preview:
    """一个极简的试听播放器。"""

    def __init__(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="kouqin_preview_")
        self._opened = False
        self._path: Optional[str] = None
        self._counter = 0
        self.available = midi_available()

    # ------------------------------------------------------------------ #

    def _close(self) -> None:
        if self._opened:
            _mci(f"close {_ALIAS}")
            self._opened = False

    def _open(self, path: str) -> bool:
        self._close()
        ok, _ = _mci(f'open "{path}" type sequencer alias {_ALIAS}')
        self._opened = ok
        return ok

    # ------------------------------------------------------------------ #

    def play_file(self, path: str) -> bool:
        """直接播放一个 MIDI 文件。"""
        path = os.path.abspath(path)
        if not self._open(path):
            return False
        ok, _ = _mci(f"play {_ALIAS} from 0")
        return ok

    def play_notes(self, notes, bpm: float = 120.0) -> bool:
        """
        播放一串音符（会把它们写成临时 MIDI 再放）。

        试听一旦开始就"能响就行"，所以这里所有音符都允许超出真实音域，
        不做口琴音域裁剪——用户听的是原谱。
        """
        if not notes:
            return False
        song = Song()
        track = Track(index=0, name="preview", channel=0)
        track.notes = [n.copy() for n in notes]
        song.tracks.append(track)

        self._counter += 1
        path = os.path.join(self._tmpdir, f"preview_{self._counter}.mid")
        try:
            write_midi(song, path, tempo_bpm=bpm)
        except Exception:
            return False
        self._path = path
        return self.play_file(path)

    def stop(self) -> None:
        if self._opened:
            _mci(f"stop {_ALIAS}")

    def close(self) -> None:
        self._close()
        # 清掉临时文件
        try:
            for name in os.listdir(self._tmpdir):
                try:
                    os.remove(os.path.join(self._tmpdir, name))
                except OSError:
                    pass
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass
