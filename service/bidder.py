"""自动出价辅助：图像识别定位界面元素 -> 输入金额 -> 点出价 -> 点确认。

由于不同游戏/分辨率的界面差异很大，这里不写死坐标，而是用"模板截图"在屏幕上
实时定位。使用前请把界面元素的截图放到 assets 目录，并写一份配置 JSON：

{
  "confidence": 0.85,
  "timeout": 5.0,
  "input_mode": "auto",
  "assets_dir": "bid_assets",
  "assets": {
    "input":          "input.png",
    "bid_button":     "bid_button.png",
    "confirm_button": "confirm_button.png",
    "digit_0": "digit_0.png",
    "digit_1": "digit_1.png"
  }
}

input_mode:
  - "keypad": 通过点击界面上的数字键输入金额（需要 digit_0..digit_9 截图）
  - "type":   点击输入框后用键盘输入金额
  - "auto":   有数字键截图则用 keypad，否则用 type（默认）

图像识别依赖 opencv-python（pyautogui 的 confidence 参数需要它）。
"""

from __future__ import annotations

import json
import random
import platform
import time
from pathlib import Path
from typing import Any

import pyautogui

from .controller import Controller

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

_SELECT_ALL = ("command", "a") if platform.system() == "Darwin" else ("ctrl", "a")


def pop_price(c):
    c.move(910, 1010, duration=0.5)
    c.click()


def confirm_price(c):
    c.move(910, 1010, duration=0.5)
    c.click()


def press_number(c, number):
    if number == 1:
        c.click(150, 800)
    elif number == 2:
        c.click(289, 800)
    elif number == 3:
        c.click(400, 800)
    elif number == 4:
        c.click(150, 900)
    elif number == 5:
        c.click(300, 900)
    elif number == 6:
        c.click(400, 900)
    elif number == 7:
        c.click(150, 980)
    elif number == 8:
        c.click(305, 980)
    elif number == 9:
        c.click(400, 980)
    elif number == 0:
        c.click(150, 1030)


def generate_random_latency():
    v = random.randint(20, 45)
    return v / 100




class LocateError(RuntimeError):
    """在超时时间内未能在屏幕上定位到目标元素。"""


class AutoBidder:
    def __init__(
        self,
        assets: dict[str, str | Path],
        controller: Controller | None = None,
        *,
        confidence: float = 0.85,
        timeout: float = 5.0,
        input_mode: str = "auto",
        poll: float = 0.3,
        bid_delay: float = 0.4,
        confirm_delay: float = 0.6,
    ) -> None:
        self._assets = {name: Path(p) for name, p in assets.items()}
        self.ctl = controller or Controller()
        self.confidence = confidence
        self.timeout = timeout
        self.input_mode = input_mode
        self.poll = poll
        self.bid_delay = bid_delay
        self.confirm_delay = confirm_delay

    def bid_price(self, price):
        pop_price(self.ctl)
        for v in price:
            latency = generate_random_latency()
            time.sleep(latency)
            print(f'latency: {latency}')
            press_number(self.ctl, int(v))

        confirm_price(self.ctl)

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        controller: Controller | None = None,
        **overrides: Any,
    ) -> "AutoBidder":
        cfg_path = Path(config_path).resolve()
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        base = cfg_path.parent / cfg.get("assets_dir", ".")
        assets = {name: base / rel for name, rel in cfg["assets"].items()}
        kwargs = {
            key: cfg[key]
            for key in ("confidence", "timeout", "input_mode", "poll", "bid_delay", "confirm_delay")
            if key in cfg
        }
        kwargs.update(overrides)
        return cls(assets, controller, **kwargs)

    # ---------- 定位 ----------
    def locate(self, name: str, timeout: float | None = None) -> pyautogui.Point:
        if name not in self._assets:
            raise KeyError(f"配置中未定义元素: {name}，已定义: {sorted(self._assets)}")
        image = self._assets[name]
        if not image.exists():
            raise FileNotFoundError(f"模板截图不存在: {image}")
        limit = self.timeout if timeout is None else timeout
        deadline = time.time() + limit
        while True:
            try:
                pos = pyautogui.locateCenterOnScreen(str(image), confidence=self.confidence)
            except pyautogui.ImageNotFoundException:
                pos = None
            if pos is not None:
                return pos
            if time.time() >= deadline:
                raise LocateError(f"超时 {limit}s 未找到 '{name}'（模板: {image}）")
            time.sleep(self.poll)

    def click(self, name: str, timeout: float | None = None, *, clicks: int = 1, button: str = "left") -> pyautogui.Point:
        pos = self.locate(name, timeout)
        pyautogui.click(pos.x, pos.y, clicks=clicks, button=button)
        return pos

    # ---------- 金额输入 ----------
    def _digits(self) -> dict[str, Path]:
        return {k[len("digit_"):]: v for k, v in self._assets.items() if k.startswith("digit_") and len(k) == 7}

    def enter_amount(self, amount: int | str, *, interval: float = 0.06) -> None:
        text = str(int(amount))
        digits = self._digits()
        mode = self.input_mode
        if mode == "auto":
            mode = "keypad" if digits and set(text) <= set(digits) else "type"

        if mode == "keypad":
            for ch in text:
                self.click(f"digit_{ch}")
                time.sleep(interval)
        elif mode == "type":
            self.click("input")
            time.sleep(interval)
            self.ctl.hotkey(*_SELECT_ALL)
            pyautogui.write(text, interval=interval)
        else:
            raise ValueError(f"未知 input_mode: {mode}")

    # ---------- 出价流程 ----------
    def place_bid(self, amount: int | str) -> None:
        self.enter_amount(amount)
        time.sleep(self.bid_delay)
        self.click("bid_button")
        if "confirm_button" in self._assets:
            time.sleep(self.confirm_delay)
            self.click("confirm_button")


def auto_bid(
    config_path: str | Path,
    amount: int | str,
    controller: Controller | None = None,
    **overrides: Any,
) -> None:
    """便捷函数：加载配置并完成一次出价。"""
    AutoBidder.from_config(config_path, controller, **overrides).place_bid(amount)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("用法: python -m service.bidder <config.json> <amount>")
        raise SystemExit(1)
    auto_bid(sys.argv[1], sys.argv[2])
    print("出价完成")