from bot import proxy
from bot import handler
import os
import posixpath
import tempfile
import time
from contextlib import contextmanager

import requests
from flask import Flask, Response, request, jsonify


@contextmanager
def _timed(label):
    start = time.perf_counter()
    try:
        yield
    finally:
        print(f'[timing] {label}: {time.perf_counter() - start:.3f}s')

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----- 本地语音模型：faster-whisper（优先使用）-----
# 默认使用中等效果模型 "medium"；可用 ASR_MODEL 覆盖（tiny/base/small/medium/large-v3）。
_asr_model = None


def _get_asr_model():
    global _asr_model
    if _asr_model is None:
        from faster_whisper import WhisperModel

        model_size = os.environ.get("ASR_MODEL", "medium")
        print(f'loading local faster-whisper model: {model_size}')
        _asr_model = WhisperModel(
            model_size,
            device=os.environ.get("ASR_DEVICE", "cpu"),
            compute_type=os.environ.get("ASR_COMPUTE_TYPE", "int8"),
        )
    return _asr_model


def _transcribe_local(path: str) -> str:
    with _timed("asr local faster-whisper"):
        segments, _ = _get_asr_model().transcribe(
            path,
            language="zh",
            initial_prompt="以下是普通话的句子，请使用简体中文转写。",
        )
        return "".join(segment.text for segment in segments).strip()


# ----- 备用实现：硅基流动（SiliconFlow）在线语音转写 -----
SILICONFLOW_ASR_URL = os.environ.get(
    "SILICONFLOW_ASR_URL", "https://api.siliconflow.cn/v1/audio/transcriptions"
)
SILICONFLOW_ASR_MODEL = os.environ.get(
    "SILICONFLOW_ASR_MODEL", "FunAudioLLM/SenseVoiceSmall"
)

# 意图识别服务：POST {"text": ...} -> {"intent": ...}
INTENTION_SORT_URL = os.environ.get(
    "INTENTION_SORT_URL", "http://127.0.0.1:8087/intention/sort"
)

_t2s = None


def _get_t2s():
    global _t2s
    if _t2s is None:
        from opencc import OpenCC

        _t2s = OpenCC("t2s")
    return _t2s


def _to_simplified(text: str) -> str:
    if not text:
        return text
    try:
        return _get_t2s().convert(text)
    except Exception as exc:
        print(f"opencc t2s convert failed: {exc}")
        return text


def _transcribe_siliconflow(path: str) -> str:
    print('using SILICONFLOW')
    api_key = os.environ.get("SILICONFLOW_API_KEY")
    if not api_key:
        raise RuntimeError("环境变量 SILICONFLOW_API_KEY 未设置")

    try:
        size = os.path.getsize(path)
        duration = max(0.0, size / (16000 * 2))
        print(f'[size] siliconflow upload: {os.path.basename(path)} = {size / 1024:.1f} KB '
              f'(~{duration:.2f}s @16kHz mono s16le)')
    except OSError:
        pass

    with _timed("asr upstream http request (siliconflow)"):
        with open(path, "rb") as f:
            resp = requests.post(
                SILICONFLOW_ASR_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={
                    "file": (os.path.basename(path), f, "application/octet-stream"),
                    "model": (None, SILICONFLOW_ASR_MODEL),
                },
                timeout=120,
            )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


def _transcribe_upload(upload, prefer_local: bool = False) -> str:
    suffix = os.path.splitext(upload.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        upload.save(tmp)
        tmp_path = tmp.name

    try:
        # 优先本地 faster-whisper（中等效果模型），失败时回退硅基流动在线接口
        if prefer_local:
            try:
                text = _transcribe_local(tmp_path)
                if text:
                    return _to_simplified(text)
                print("local asr returned empty text, fallback to siliconflow")
            except Exception as exc:
                print(f"local asr failed, fallback to siliconflow: {exc}")

        text = _transcribe_siliconflow(tmp_path)
        return _to_simplified(text)
    finally:
        os.unlink(tmp_path)


def _classify_intention(text: str) -> str:
    with _timed("intention classify http request"):
        resp = requests.post(INTENTION_SORT_URL, json={"user_input": text}, timeout=30)
    resp.raise_for_status()
    return (resp.json().get("intention") or "").strip()


def _convert_price(price: str) -> str:
    if price.endswith("万"):
        return f"{price[:-1]}0000"
    return price


# 支持的下单指令前缀：「出价」「估价」均可，二者执行相同动作。
ORDER_PREFIXES = ("出价", "估价")

# 意图识别返回这些值时视为「未知」：不提示、不执行动作，直接监听下一段音频。
UNKNOWN_INTENTS = {"", "未知", "unknown", "none", "null"}


def _order_price(text: str) -> str | None:
    """若 text 以「出价」/「估价」开头，返回换算后的金额，否则返回 None。"""
    for prefix in ORDER_PREFIXES:
        if text.startswith(prefix):
            return _convert_price(text[len(prefix):].strip())
    return None


@app.post("/asr")
def asr():
    upload = request.files.get("audio") or request.files.get("file")
    if upload is None:
        return {"error": "missing audio file field: audio"}, 400

    text = _transcribe_upload(upload)
    print(f'asr text = {text}')
    return Response(text, mimetype="text/plain")


def _transcribe_with_channel(path: str) -> tuple[str, str]:
    """转写并返回 (text, channel)。

    channel 取值："local"（本地 faster-whisper）或 "siliconflow"（在线接口）。
    优先本地，本地失败或结果为空时回退到 siliflow。
    """
    try:
        text = _to_simplified(_transcribe_local(path))
        if text:
            return text, "local"
        print("local asr returned empty text, fallback to siliconflow")
    except Exception as exc:
        print(f"local asr failed, fallback to siliconflow: {exc}")

    text = _to_simplified(_transcribe_siliconflow(path))
    return text, "siliconflow"


# ---------------------------------------------------------------
# 语音文件本地处理（转写 -> 意图识别 -> 分发），供 /command/voice
# 以及本机客户端直接调用（不经过 HTTP 接口）。
# ---------------------------------------------------------------
def process_voice_file(path: str, on_transcribed=None, on_intent=None) -> tuple[str, str, str | None, str]:
    """处理一个本地 wav 文件，返回 (text, intent, error, channel)。

    text    ：转写文本（已转简体）。
    intent  ：识别到的意图（可能为空字符串）。
    error   ：出错信息；成功时为 None。
    channel ：转写渠道，"local"=本地模型，"siliconflow"=在线接口，空串=转写失败。

    on_transcribed(text, channel)：转写完成后立即回调（在意图识别之前）。
    on_intent(intent, channel)    ：意图识别出来后、调用远程/本地动作之前立即回调，
                                    便于调用方先更新界面，再执行动作。
    """
    t0 = time.perf_counter()
    try:
        text, channel = _transcribe_with_channel(path)
    except Exception as exc:
        return "", "", f"transcribe failed: {exc}", ""
    t1 = time.perf_counter()
    print(f'command/voice asr text = {text} (channel={channel})')
    print(f'[timing] command/voice transcribe (incl. opencc): {t1 - t0:.3f}s')

    if on_transcribed is not None:
        try:
            on_transcribed(text, channel)
        except Exception as exc:
            print(f"on_transcribed callback failed: {exc}")

    try:
        intent = _classify_intention(text)
    except Exception as exc:
        print(f'command/voice intention failed: {exc}')
        return text, "", f"intention failed: {exc}", channel
    t2 = time.perf_counter()
    print(f'command/voice intent = {intent}')
    print(f'[timing] command/voice intention: {t2 - t1:.3f}s')

    # 意图为「未知」：不做任何提示、不执行动作，直接返回，等待下一段音频。
    if intent in UNKNOWN_INTENTS:
        print(f'command/voice intent is unknown, skip prompting and action: {intent!r}')
        return text, intent, None, channel

    # 意图已识别：先回调通知调用方（更新界面），再执行动作。
    if on_intent is not None:
        try:
            on_intent(intent, channel)
        except Exception as exc:
            print(f"on_intent callback failed: {exc}")

    if os.environ.get("REMOTE_COMMAND") == "1":
        print(f'command/voice -> remote command: {intent}')
        proxy.send_remote_command(intent)
    elif intent in handler.ACTIONS:
        handler.ACTIONS[intent]()
    else:
        price = _order_price(intent)
        if price is None:
            return text, intent, f"unknown intent: {intent}", channel
        print(f'command/voice order price = {price}')
        handler.bid(price)

    t3 = time.perf_counter()
    print(f'[timing] command/voice action dispatch: {t3 - t2:.3f}s')
    print(f'[timing] command/voice TOTAL (server-side): {t3 - t0:.3f}s')

    return text, intent, None, channel


@app.post("/command/voice")
def command_voice():
    upload = request.files.get("audio") or request.files.get("file")
    if upload is None:
        return {"error": "missing audio file field: audio"}, 400

    suffix = os.path.splitext(upload.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        upload.save(tmp)
        tmp_path = tmp.name

    try:
        text, intent, error, channel = process_voice_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    if error is not None:
        return {"code": -1, "error": error, "text": text, "intent": intent, "channel": channel}, 400

    return {"code": 0, "text": text, "intent": intent, "channel": channel}, 200


@app.post("/command/remote")
def command_remote():
    data = request.get_json(silent=True) or {}
    cmd = data.get("command", request.args.get("command"))
    if cmd is None or (isinstance(cmd, str) and cmd.strip() == ""):
        return {"code": -1, "error": "missing json field: command"}, 400

    resp = proxy.send_remote_command(cmd)

    return {"code": 0}, 200


@app.post("/command")
def command():
    data = request.get_json(silent=True) or {}
    cmd = data.get("command", request.args.get("command"))
    if cmd is None or (isinstance(cmd, str) and cmd.strip() == ""):
        return {"code": -1, "error": "missing json field: command"}, 400

    if cmd in handler.ACTIONS:
        handler.ACTIONS[cmd]()
    else:
        price = _order_price(cmd)
        if price is None:
            return {"code": -1, "error": "unknown command"}, 400
        print(f'command order price = {price}')
        handler.bid(price)

    return {"code": 0}, 200


@app.get("/sync")
def sync_file():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", request.args.get("filename"))
    if not filename:
        return {"error": "missing json field: filename"}, 400

    print(f'base directory: {BASE_DIR}')
    path = os.path.abspath(posixpath.join(BASE_DIR, filename))
    if not path.startswith(BASE_DIR + os.sep):
        return {"error": "invalid filename"}, 400
    if not os.path.isfile(path):
        return {"error": "file not found"}, 404

    with open(path, "rb") as f:
        data = f.read()

    return Response(data, mimetype="application/octet-stream")


@app.get("/sync/from")
def sync_from():
    filename = request.args.get("filename")
    if not filename:
        return {"code": -1, "error": "missing query parameter: filename"}, 400

    path = os.path.abspath(posixpath.join(BASE_DIR, filename))
    if not path.startswith(BASE_DIR + os.sep):
        return {"code": -1, "error": "invalid filename"}, 400

    data = proxy.sync_file(filename)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)

    return {"code": 0, "filename": filename, "size": len(data)}, 200


@app.get('/list')
def list_files():
    directory = request.args.get('directory')
    if not directory:
        return {"error": "missing query parameter: directory"}, 400

    base = os.path.abspath(BASE_DIR)
    path = os.path.abspath(posixpath.join(BASE_DIR, directory))
    if path != base and not path.startswith(base + os.sep):
        return {"error": "invalid directory"}, 400
    if not os.path.isdir(path):
        return {"error": "directory not found"}, 404

    files = []
    for root, _, filenames in os.walk(path):
        for name in filenames:
            files.append(os.path.relpath(posixpath.join(root, name), path))
    files.sort()
    print(f'walked files: {files}')

    return jsonify({'code': 0, 'directory': directory, 'files': files})

@app.get("/sync/dir/from")
def sync_dir_from():
    directory = request.args.get("directory")
    if not directory:
        return {"code": -1, "error": "missing query parameter: directory"}, 400

    base = os.path.abspath(BASE_DIR)
    local_dir = os.path.abspath(posixpath.join(BASE_DIR, directory))
    if local_dir != base and not local_dir.startswith(base + os.sep):
        return {"code": -1, "error": "invalid directory"}, 400

    files = proxy.list_files(directory)
    if not files:
        return {"code": -1, "error": "no files found"}, 404

    saved = []
    for name in files:
        remote_name = posixpath.join(directory, name)
        path = os.path.abspath(posixpath.join(BASE_DIR, remote_name))
        if not path.startswith(BASE_DIR + os.sep):
            continue

        data = proxy.sync_file(remote_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        saved.append(name)

    return {"code": 0, "directory": directory, "count": len(saved), "files": saved}, 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000, debug=True)