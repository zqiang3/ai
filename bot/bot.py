import asyncio
import json
import logging
import os
import signal
import uuid
from . import handler

import websockets

WECOM_WS_URL = "wss://openws.work.weixin.qq.com"
HEARTBEAT_INTERVAL = 30
RECONNECT_BASE_DELAY = 1
RECONNECT_MAX_DELAY = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wecom-bot")



def new_req_id():
    return uuid.uuid4().hex


class WeComBot:
    def __init__(self, bot_id, secret):
        self.bot_id = bot_id
        self.secret = secret
        self._ws = None
        self._send_lock = None
        self._running = True

    def convert_price(self, price: str):
        if price.endswith('万'):
            return f'{price[:-1]}0000'
        else:
            return price


    async def send(self, cmd, body=None, req_id=None):
        frame = {
            "cmd": cmd,
            "headers": {"req_id": req_id or new_req_id()},
        }
        if body is not None:
            frame["body"] = body
        async with self._send_lock:
            await self._ws.send(json.dumps(frame, ensure_ascii=False))
        return frame["headers"]["req_id"]

    async def subscribe(self):
        return await self.send(
            "aibot_subscribe",
            body={"bot_id": self.bot_id, "secret": self.secret},
        )

    async def reply_text(self, req_id, content):
        # aibot_respond_msg 不支持 msgtype=text（会返回 40008），仅支持
        # stream/template_card/markdown/file/image/voice/video，这里用 markdown。
        return await self.send(
            "aibot_respond_msg",
            body={"msgtype": "markdown", "markdown": {"content": content}},
            req_id=req_id,
        )

    async def send_welcome(self, req_id, content):
        return await self.send(
            "aibot_respond_welcome_msg",
            body={"msgtype": "text", "text": {"content": content}},
            req_id=req_id,
        )

    async def handle_text(self, req_id, body):
        content = (body.get("text") or {}).get("content", "")
        sender = (body.get("from") or {}).get("userid", "")
        chattype = body.get("chattype", "single")
        logger.info(f'message from {sender}, req_id={req_id}, chat_type={chattype}, content={content}')
        # dispatch action handler
        print(handler.ACTIONS)
        if content in handler.ACTIONS:
            f = handler.ACTIONS[content]
            f()
        else:
            if content.startswith("出价"):
                price = self.convert_price(content[2:].strip())
                print(f'price of this round is {price}')
                handler.bid(price)


        # await self.reply_text(req_id, f"已收到：{content}")

    async def handle_event(self, req_id, body):
        event = body.get("event") or {}
        eventtype = event.get("eventtype")
        logger.info("event type=%s", eventtype)
        if eventtype == "enter_chat":
            await self.send_welcome(req_id, "您好！我是智能助手，有什么可以帮您的吗？")
        elif eventtype == "disconnected_event":
            logger.warning("connection replaced by a newer session, exiting")
            self._running = False

    async def dispatch(self, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("ignoring invalid frame: %s", message)
            return

        cmd = data.get("cmd")
        req_id = (data.get("headers") or {}).get("req_id")
        body = data.get("body") or {}

        if cmd == "aibot_msg_callback":
            msgtype = body.get("msgtype")
            if msgtype == "text":
                await self.handle_text(req_id, body)
            else:
                await self.reply_text(req_id, f"暂不支持的消息类型：{msgtype}")
        elif cmd == "aibot_event_callback":
            await self.handle_event(req_id, body)
        elif data.get("errcode") not in (None, 0):
            logger.error(
                "request failed req_id=%s errcode=%s errmsg=%s",
                req_id,
                data.get("errcode"),
                data.get("errmsg"),
            )

    async def heartbeat(self):
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await self.send("ping")
                logger.debug("heartbeat ping sent")
            except Exception as exc:
                logger.warning("heartbeat failed: %s", exc)
                return

    async def session(self):
        self._send_lock = asyncio.Lock()
        async with websockets.connect(
            WECOM_WS_URL,
            ping_interval=None,
            max_size=None,
            proxy=os.environ.get("WECOM_WS_PROXY"),
        ) as ws:
            self._ws = ws
            logger.info("connected to %s", WECOM_WS_URL)
            await self.subscribe()
            logger.info("subscription sent, listening for callbacks")

            hb_task = asyncio.create_task(self.heartbeat())
            try:
                async for message in ws:
                    if not self._running:
                        break
                    await self.dispatch(message)
            finally:
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

    async def run(self):
        delay = RECONNECT_BASE_DELAY
        while self._running:
            try:
                await self.session()
                if not self._running:
                    break
                delay = RECONNECT_BASE_DELAY
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("connection error: %s", exc)
                if not self._running:
                    break
                logger.info("reconnecting in %ss", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)


async def main():
    bot_id = os.environ.get("WECOM_BOT_ID")
    secret = os.environ.get("WECOM_BOT_SECRET")
    if not bot_id or not secret:
        raise SystemExit("environment variables WECOM_BOT_ID and WECOM_BOT_SECRET are required")

    bot = WeComBot(bot_id, secret)
    loop = asyncio.get_running_loop()

    def request_stop(*_):
        bot._running = False
        if bot._ws is not None:
            loop.create_task(bot._ws.close())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("shutdown requested")