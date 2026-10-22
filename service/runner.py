"""把 JSON 动作脚本映射到 Controller 执行。

脚本格式（列表，按顺序执行）：
[
  {"action": "move",   "x": 100, "y": 200, "duration": 0.3},
  {"action": "click",  "x": 100, "y": 200},
  {"action": "type",   "text": "hello"},
  {"action": "type",   "text": "你好，世界"},     # 含非 ASCII 时自动改用剪贴板粘贴
  {"action": "paste",  "text": "中文内容"},        # 显式走剪贴板粘贴
  {"action": "key",    "keys": ["enter"]},
  {"action": "scroll", "amount": -500},
  {"action": "wait",   "seconds": 1.0}
]
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .controller import Controller

# 动作注册表：动作名 -> 处理函数。新增动作只需用 @action 装饰一个函数，
# 无需修改 _dispatch，符合开闭原则。
ActionHandler = Callable[[Controller, dict[str, Any]], None]
_ACTIONS: dict[str, ActionHandler] = {}


def action(name: str) -> Callable[[ActionHandler], ActionHandler]:
    """把函数注册为名为 name 的动作处理器。"""

    def decorator(fn: ActionHandler) -> ActionHandler:
        if name in _ACTIONS:
            raise ValueError(f"动作重复注册: {name}")
        _ACTIONS[name] = fn
        return fn

    return decorator


def execute(actions: list[dict[str, Any]], controller: Controller | None = None) -> None:
    ctl = controller or Controller()
    for i, step in enumerate(actions):
        name = step.get("action")
        try:
            _dispatch(ctl, name, step)
        except Exception as exc:  # 保留出错步骤上下文，便于定位
            raise RuntimeError(f"第 {i + 1} 步 '{name}' 执行失败: {exc}") from exc


def _dispatch(ctl: Controller, name: str | None, step: dict[str, Any]) -> None:
    handler = _ACTIONS.get(name)  # type: ignore[arg-type]
    if handler is None:
        raise ValueError(f"未知动作: {name}，可用动作: {sorted(_ACTIONS)}")
    handler(ctl, step)


@action("move")
def _move(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.move(step["x"], step["y"], duration=step.get("duration", 0.2))


@action("click")
def _click(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.click(step.get("x"), step.get("y"), button=step.get("button", "left"))


@action("double_click")
def _double_click(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.double_click(step.get("x"), step.get("y"))


@action("drag")
def _drag(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.drag(tuple(step["start"]), tuple(step["end"]), duration=step.get("duration", 0.5))


@action("scroll")
def _scroll(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.scroll(step["amount"])


@action("type")
def _type(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.type_text(step["text"], interval=step.get("interval", 0.03))


@action("paste")
def _paste(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.paste_text(step.get("text"))


@action("key")
def _key(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.press(*step["keys"], presses=step.get("presses", 1))


@action("hotkey")
def _hotkey(ctl: Controller, step: dict[str, Any]) -> None:
    ctl.hotkey(*step["keys"])


@action("wait")
def _wait(ctl: Controller, step: dict[str, Any]) -> None:
    time.sleep(step.get("seconds", 1.0))


@action("bid")
def _bid(ctl: Controller, step: dict[str, Any]) -> None:
    from .bidder import AutoBidder

    bidder = AutoBidder.from_config(step["config"], ctl)
    bidder.place_bid(step["amount"])


def run_file(path: str | Path, controller: Controller | None = None) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    execute(data, controller)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("用法: python -m service.runner <script.json>")
        raise SystemExit(1)
    run_file(sys.argv[1])
    print("执行完成")