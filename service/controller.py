"""鼠标/键盘控制核心，封装 pyautogui。

macOS 使用说明：
1. 系统设置 -> 隐私与安全性 -> 辅助功能，勾选运行本程序的终端 / Python。
2. 若使用截图功能，还需在 隐私与安全性 -> 屏幕录制 中授权。
3. pyautogui 坐标使用逻辑点（point），与 CGEvent 一致；Retina 下无需手动乘 2。
"""

from __future__ import annotations

import platform
import subprocess
import time
from typing import Iterable

import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

_SYSTEM = platform.system()
# 粘贴快捷键：macOS 为 Cmd+V，其余平台为 Ctrl+V。
_PASTE_KEYS = ("command", "v") if _SYSTEM == "Darwin" else ("ctrl", "v")


def _set_clipboard(text: str) -> None:
    data = text.encode("utf-8")
    if _SYSTEM == "Darwin":
        subprocess.run(["pbcopy"], input=data, check=True)
    elif _SYSTEM == "Windows":
        subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
    else:
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                subprocess.run(cmd, input=data, check=True)
                return
            except FileNotFoundError:
                continue
        raise RuntimeError("未找到剪贴板工具，请安装 xclip 或 xsel")


class Controller:
    def __init__(self, pause: float = 0.05, failsafe: bool = True) -> None:
        pyautogui.PAUSE = pause
        pyautogui.FAILSAFE = failsafe

    # ---------- mouse operations ----------
    def position(self):
        pos = pyautogui.position()
        return pos

    def move(self, x, y, duration=0.2) -> None:
        pyautogui.moveTo(x, y, duration=duration)

    def click(self, x=None, y=None, button="left") -> None:
        if x is not None and y is not None:
            self.move(x, y)
        pyautogui.click(button=button)

    def double_click(self, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        pyautogui.doubleClick()

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.5,
        button: str = "left",
    ) -> None:
        self.move(*start)
        pyautogui.mouseDown(button=button)
        self.move(*end, duration=duration)
        pyautogui.mouseUp(button=button)

    def scroll(self, amount: int) -> None:
        pyautogui.scroll(amount)

    # ---------- 剪贴板 ----------
    def set_clipboard(self, text: str) -> None:
        _set_clipboard(text)

    def paste_text(self, text: str | None = None) -> None:
        """把文本粘贴到当前焦点处。

        非 ASCII 字符（如中文）无法用 pyautogui 逐键输入，因此改走剪贴板 + 粘贴快捷键。
        text 为 None 时直接粘贴当前剪贴板内容。
        """
        if text is not None:
            self.set_clipboard(text)
        self.hotkey(*_PASTE_KEYS)

    # ---------- 键盘 ----------
    def type_text(self, text: str, interval: float = 0.03) -> None:
        # pyautogui.write 仅支持键盘映射表内的字符，中文等会被静默忽略，故自动降级为粘贴。
        if text.isascii():
            pyautogui.write(text, interval=interval)
        else:
            self.paste_text(text)

    def press(self, *keys: str, presses: int = 1, interval: float = 0.05) -> None:
        for _ in range(presses):
            pyautogui.press(list(keys))
            time.sleep(interval)

    def hotkey(self, *keys: str) -> None:
        pyautogui.hotkey(*keys)

    def key_down(self, key: str) -> None:
        pyautogui.keyDown(key)

    def key_up(self, key: str) -> None:
        pyautogui.keyUp(key)

    def hold(self, keys: Iterable[str]) -> "_HoldContext":
        return _HoldContext(keys)


class _HoldContext:
    def __init__(self, keys: Iterable[str]) -> None:
        self._keys = list(keys)

    def __enter__(self) -> "_HoldContext":
        for k in self._keys:
            pyautogui.keyDown(k)
        return self

    def __exit__(self, *exc) -> None:
        for k in reversed(self._keys):
            pyautogui.keyUp(k)