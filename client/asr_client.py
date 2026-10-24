"""桌面语音转写客户端（跨平台：macOS / Windows）。

持续监听 -> 停顿自动断句 -> 调用 /command/voice -> 逐句显示。

与旧版（仅 macOS/AppKit）相比，本版使用跨平台技术栈，功能保持一致：
  - GUI：tkinter（Python 自带）
  - 录音：macOS 优先使用 ffmpeg(avfoundation) 采集原始 PCM（更稳定、免 PortAudio
    依赖）；其它平台回退到 sounddevice(PortAudio)。两者均用能量 VAD 自动断句。
  - 全局快捷键：pynput（可选，见 ASR_GLOBAL_HOTKEYS）
  - 打开监控/配置页：webbrowser

监听模式：
  点击「开始监听」后持续采集，检测到语音并在之后静音达到 ASR_SILENCE_SECONDS
  （默认 1.5 秒）即自动断句，写出 wav 并处理（默认本地调用 app.process_voice_file，
  可用 ASR_LOCAL=0 回退到 /command/voice 接口），识别结果逐句追加。

转写渠道配色：
  识别结果按所用渠道着色并加前缀，便于区分：
  - 本地模型（faster-whisper）：蓝色，前缀 [语音识别-本地模型]
  - 在线接口（SiliconFlow） ：橙色，前缀 [SiliconFlow]

「重新录制」按钮：
  点击后忽略此前已收到的音频（清空预滚缓冲与进行中的录音，并使此前发出、
  尚未返回的识别结果失效），从当前时间起重新开始录制。

意图浮层：
  每次识别返回意图后，在屏幕中央弹出浮动大字「即将{意图}」，约 2.5 秒后自动消失。

快捷键：
  默认仅在本窗口内生效（对齐旧版 AppKit 的 local monitor 语义）：
    - 空格键：在「快捷命令」标签页时等价于「单击」，其他标签页等价于「使用道具」
    - Shift 键（左/右）：切换监听启停
  设置环境变量 ASR_GLOBAL_HOTKEYS=1 可启用系统级全局快捷键（pynput）。
  注意：全局模式下空格键会在任何程序中被捕获并触发命令，请谨慎开启。

环境变量：
  ASR_URL             默认 http://127.0.0.1:6000/command/voice
  REMOTE_COMMAND_URL  默认 http://127.0.0.1:6000/command/remote
  ASR_AUDIO_DEVICE    录音设备序号或名称子串；macOS 下为 ffmpeg avfoundation
                      音频设备序号，默认自动探测（优先 AirPods/MacBook/麦克风）
  ASR_WAV_DIR         录音保存目录，默认为 <项目>/assets/wav
  ASR_SILENCE_SECONDS 断句静音时长（秒），默认 1.5
  ASR_MAX_SECONDS     单句最长时长（秒），默认 30
  ASR_TIMEOUT         请求超时（秒），默认 10；超时则本次不做任何动作
  ASR_MIN_SPEECH_MS   有效语音最短时长（毫秒），默认 300
  ASR_VAD_FRAME_MS    VAD 帧长（毫秒），默认 30
  ASR_VAD_THRESHOLD   能量阈值（0~1），设置后跳过自动校准
  ASR_MIN_RECORD_INTERVAL
                      两次录音之间的最小间隔（秒），默认 3；期间即使检测到语音
                      也不会开始新的录音，用于避免连续断句/重复识别
  ASR_GLOBAL_HOTKEYS  设为 1/true 时启用系统级全局快捷键，默认关闭
"""

import json
import os
import platform
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import wave
import webbrowser
from collections import deque
from datetime import datetime
import tkinter as tk
from tkinter import ttk

import numpy as np
import requests

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - 仅在缺少依赖时触发
    sd = None

try:
    from pynput import keyboard as pynput_keyboard
except Exception:  # pragma: no cover - 仅在缺少依赖时触发
    pynput_keyboard = None


_SYSTEM = platform.system()


ASR_URL = os.environ.get("ASR_URL") or "http://127.0.0.1:6000/command/voice"
COMMAND_URL = os.environ.get("REMOTE_COMMAND_URL") or "http://127.0.0.1:6000/command/remote"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAV_DIR = os.environ.get("ASR_WAV_DIR") or os.path.join(_PROJECT_ROOT, "assets", "wav")
# 截屏保存目录：assets/screenshots
SCREENSHOT_DIR = os.environ.get("ASR_SCREENSHOT_DIR") or os.path.join(
    _PROJECT_ROOT, "assets", "screenshots"
)

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from bot import proxy
except Exception as _proxy_exc:  # pragma: no cover - 仅在缺少依赖时触发
    proxy = None
    print(f"[warn] 无法导入 bot.proxy: {_proxy_exc}")

# 本地语音处理：直接调用 app.process_voice_file（转写->意图->分发），
# 不再经过 HTTP 接口 /command/voice。设为 ASR_LOCAL=0 可强制走接口。
ASR_LOCAL = (os.environ.get("ASR_LOCAL", "1").strip().lower() in ("1", "true", "yes", "on"))
try:
    from app import process_voice_file
except Exception as _app_exc:  # pragma: no cover - 仅在缺少依赖时触发
    process_voice_file = None
    print(f"[warn] 无法导入 app.process_voice_file，将回退 HTTP 接口: {_app_exc}")

# 视为「未知」的意图：不进行意图提示、不执行动作，仅监听下一段音频。
UNKNOWN_INTENTS = {"", "未知", "unknown", "none", "null"}

try:
    import pyautogui
except Exception as _pyautogui_exc:  # pragma: no cover - 仅在缺少依赖时触发
    pyautogui = None
    print(f"[warn] 无法导入 pyautogui: {_pyautogui_exc}")

SAMPLE_RATE = 16000

MONITOR_URL = (
    "https://10.143.167.46:20068/d/cfag5n3h5uewae/"
    "e998bf-e9878c-e4ba91-gts-e7ae97-e6b395"
    "?orgId=1&from=now-30m&to=now&timezone=browser"
    "&var-Api=ASR&var-query0="
)
TTS_MONITOR_URL = (
    "https://10.143.167.46:20068/d/cfag5n3h5uewae/"
    "e998bf-e9878c-e4ba91-gts-e7ae97-e6b395"
    "?orgId=1&from=now-30m&to=now&timezone=browser"
    "&var-Api=TTS&var-query0="
)
MSE_CONFIG_URL = (
    "https://mse.console.aliyun.com/?spm=5176.mse-prod.0.0.7e262675wu0SiO"
    "#/Instance/Config/List?ClusterId=mse-343d9350"
    "&ClusterName=prod-%E5%85%AC%E5%85%B1%E7%BB%84%E4%BB%B6-nacos"
    "&ClusterType=Nacos-Ans"
    "&InstanceId=mse_prepaid_public_cn-wbl4uzsje01"
    "&VersionCode=NACOS_2_3_2_1&AppVersion=2.3.2.1&MseVersion=mse_platinum"
    "&ChargeType=PREPAY&region=cn-shanghai&namespaceId=ns-gts"
    "&prometheusVersion=basic"
)
GTS_APP_URL = (
    "https://10.143.165.251:32013/application/k8sDeploymentManage"
    "?id=2949&appId=1552&appName=global-traffic-scheduler"
)


def _env_float(name, default):
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _ffmpeg_available():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (FileNotFoundError, OSError):
        return False


def detect_audio_device_ffmpeg():
    """枚举 ffmpeg avfoundation 音频设备，返回偏好的设备序号字符串。

    优先匹配 AirPods / MacBook / Microphone 等关键字，否则取第一个音频设备。
    未找到 ffmpeg 或无音频设备时返回 "0"。
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
        ).stderr
    except (FileNotFoundError, OSError):
        return "0"

    audio = []
    in_audio = False
    for line in (out or "").splitlines():
        if "AVFoundation audio devices:" in line:
            in_audio = True
            continue
        if in_audio:
            m = re.search(r"\[(\d+)\]\s+(.*)", line)
            if m:
                audio.append((m.group(1), m.group(2).strip()))
    if not audio:
        return "0"
    for keyword in ("AirPods", "MacBook", "Microphone"):
        for idx, name in audio:
            if keyword.lower() in name.lower():
                return idx
    return audio[0][0]


def detect_audio_device():
    """返回录音设备。

    macOS：优先返回 ffmpeg(avfoundation) 音频设备序号（字符串）。
    其它平台：返回 sounddevice 可用的输入设备（序号或名称）。
    """
    env = os.environ.get("ASR_AUDIO_DEVICE")
    if env:
        env = env.strip()
        if _SYSTEM == "Darwin":
            return env  # macOS 下统一按 ffmpeg 设备序号处理
        if env.isdigit():
            return int(env)
        return env

    if _SYSTEM == "Darwin":
        return detect_audio_device_ffmpeg()

    if sd is None:
        return None
    try:
        default = sd.default.device
        if isinstance(default, (list, tuple)) and default:
            if default[0] is not None and default[0] >= 0:
                return default[0]
    except Exception:
        pass
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                return idx
    except Exception:
        pass
    return None


def describe_audio_device(device):
    if _SYSTEM == "Darwin":
        return f"ffmpeg avfoundation [{device}]"
    if sd is None:
        return "(未安装 sounddevice)"
    try:
        dev = sd.query_devices(device, "input")
        return f"[{device}] {dev['name']}" if isinstance(device, int) else f"{device}"
    except Exception:
        return str(device)


def _pick_samplerate(device, desired=SAMPLE_RATE):
    """尽量用 16k；设备不支持时回退到设备默认采样率。"""
    if sd is None:
        return desired
    try:
        sd.check_input_settings(device=device, channels=1, samplerate=desired, dtype="int16")
        return desired
    except Exception:
        try:
            info = sd.query_devices(device, "input")
            return int(info["default_samplerate"])
        except Exception:
            return desired


def _rms(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def _resample(data: bytes, src_rate: int, dst_rate: int = SAMPLE_RATE) -> bytes:
    if src_rate == dst_rate or not data:
        return data
    x = np.frombuffer(data, dtype=np.int16)
    if x.size == 0:
        return b""
    n = int(round(x.size * dst_rate / src_rate))
    if n <= 0:
        return b""
    idx = np.linspace(0.0, x.size - 1, n)
    y = np.interp(idx, np.arange(x.size), x).astype(np.int16)
    return y.tobytes()


class AudioCapture:
    """封装 sounddevice 输入流，回调把 int16 PCM 投递到队列。"""

    def __init__(self, device, samplerate, blocksize):
        self.device = device
        self.samplerate = samplerate
        self.blocksize = blocksize
        self._q = queue.Queue()
        self._stream = None

    def start(self):
        def callback(indata, frames, time_info, status):
            if status:
                print(f"[audio] {status}")
            self._q.put(bytes(indata))

        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            blocksize=self.blocksize,
            callback=callback,
        )
        self._stream.start()

    def read(self, timeout=0.5):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


class FfmpegCapture:
    """用 ffmpeg(avfoundation) 采集麦克风，输出 16k/单声道/s16le PCM 到 stdout。

    与 AudioCapture 保持相同接口（read/stop），便于上层 VAD 循环复用。
    macOS 下更稳定：不依赖 PortAudio，规避 sounddevice 的设备/采样率/权限问题。
    """

    def __init__(self, device, samplerate=SAMPLE_RATE):
        self.device = device
        self.samplerate = samplerate
        self._proc = None
        self._q = queue.Queue()
        self._reader = None
        self._closed = False

    def start(self):
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "avfoundation",
            "-i", f":{self.device}",
            "-ar", str(self.samplerate),
            "-ac", "1",
            "-f", "s16le",
            "-",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._closed = False
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self):
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while not self._closed:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                self._q.put(chunk)
        except Exception:
            pass

    def read(self, timeout=0.5):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._closed = True
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


class AsrClient:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._listening = False
        self._shift_down = False
        self._proc = None  # AudioCapture
        self._reader = None
        self._acc = bytearray()
        self._restart_requested = threading.Event()
        self._epoch = 0
        self._overlay = None
        self._overlay_label = None
        self._overlay_timer = None
        self._last_estimate_wan = 6.0
        self._editable = False
        self._kb_listener = None
        # 空格键去抖：避免全局热键与窗口绑定对同一次按键重复触发。
        self._last_space_ts = 0.0
        self._space_debounce = 0.3
        # 命令去重：同一命令在短时间窗内仅发送一次，消除多路径重复触发。
        self._last_command = None
        self._last_command_ts = 0.0
        self._command_debounce = 0.3
        # 本局记录：开始/结束时间 + 每次收益；已结束的对局持久化到文件。
        self._records_path = os.path.join(_PROJECT_ROOT, "assets", "game_records.json")
        self._records = self._load_records()
        self._session_start = None
        self._session_end = None
        self._session_profits = []
        self._session_active = False
        self.records_per_page = 10
        self._record_page = 0
        # 估价任务：每 task_interval 秒发送一次「估价20000」，
        # task_estimate_count 次后发送「开始排位」，循环执行直到停止。
        self.task_interval = 10
        self.task_estimate_count = 6
        self._task_running = False
        self._task_stop = threading.Event()
        self._task_thread = None

        self.audio_device = detect_audio_device()
        self.url = ASR_URL
        self.command_url = COMMAND_URL
        self.silence_seconds = _env_float("ASR_SILENCE_SECONDS", 1.5)
        self.max_seconds = _env_float("ASR_MAX_SECONDS", 30.0)
        self.asr_timeout = _env_float("ASR_TIMEOUT", 10.0)
        self.frame_ms = max(10, int(_env_float("ASR_VAD_FRAME_MS", 30)))
        self.min_speech_ms = max(0.0, _env_float("ASR_MIN_SPEECH_MS", 300.0))
        self.threshold_override = os.environ.get("ASR_VAD_THRESHOLD")
        self.global_hotkeys = _env_bool("ASR_GLOBAL_HOTKEYS", False)
        # 两次录音之间的最小间隔（秒），防止连续断句/重复识别。
        self.min_record_interval = max(0.0, _env_float("ASR_MIN_RECORD_INTERVAL", 3.0))

        self._build_window()
        self._install_hotkeys()
        self._render_session()
        self._render_records_page()

    # ---------- 主线程调度 ----------
    def _ui(self, block):
        def _run():
            try:
                block()
            except Exception as exc:
                print(f"[error] UI block 执行失败: {exc}")

        try:
            self.root.after(0, _run)
        except Exception:
            _run()

    def _device_status(self):
        return f"设备: {describe_audio_device(self.audio_device)}  ->  {self.url}"

    def _idle_status(self):
        return f"监听中… 静音 {self.silence_seconds:g}s 自动识别 | {self._device_status()}"

    # ---------- 界面 ----------
    def _build_window(self):
        self.root.title("语音转写客户端")
        self.root.geometry("640x505")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        voice_view = ttk.Frame(notebook)
        estimate_view = ttk.Frame(notebook)
        action_view = ttk.Frame(notebook)
        work_view = ttk.Frame(notebook)
        records_view = ttk.Frame(notebook)
        notebook.add(voice_view, text="语音转写")
        notebook.add(estimate_view, text="估价")
        notebook.add(action_view, text="快捷命令")
        notebook.add(work_view, text="工作")
        notebook.add(records_view, text="记录")
        self.tab_views = {
            "voice": voice_view,
            "estimate": estimate_view,
            "action": action_view,
            "work": work_view,
            "records": records_view,
        }

        # ----- 语音转写页 -----
        row1 = ttk.Frame(voice_view, padding=(10, 10, 10, 0))
        row1.pack(fill="x")
        self.button = ttk.Button(row1, text="开始监听", command=self.toggle_listen)
        self.button.pack(side="left")
        self.restart_button = ttk.Button(row1, text="重新录制", command=self.restart_recording)
        self.restart_button.pack(side="left", padx=(10, 0))

        row2 = ttk.Frame(voice_view, padding=(10, 5))
        row2.pack(fill="x")
        self.edit_button = ttk.Button(row2, text="编辑", command=self.toggle_edit)
        self.edit_button.pack(side="left")
        ttk.Button(row2, text="复制", command=self.copy_text).pack(side="left", padx=(10, 0))
        ttk.Button(row2, text="清空", command=self.clear_text).pack(side="left", padx=(10, 0))

        self.status = ttk.Label(voice_view, text=self._device_status(), padding=(10, 0, 10, 5))
        self.status.pack(fill="x")

        scroll = ttk.Scrollbar(voice_view, orient="vertical")
        self.text = tk.Text(voice_view, wrap="word", font=("", 12), yscrollcommand=scroll.set)
        scroll.config(command=self.text.yview)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        # 用颜色区分语音转写渠道：本地模型(蓝) / SiliconFlow(橙) / 其它(默认)
        self.text.tag_configure("local", foreground="#1565c0")
        self.text.tag_configure("siliconflow", foreground="#e65100")
        self.text.configure(state="disabled")

        # ----- 估价页 -----
        top = ttk.Frame(estimate_view, padding=(10, 10))
        top.pack(fill="x")
        use_tool_btn = ttk.Button(top, text="使用道具", command=self.use_game_tool, width=20, takefocus=0)
        use_tool_btn.grid(row=0, column=0, sticky="w")
        self.use_tool_btn = use_tool_btn
        start_rank_btn = tk.Button(
            top,
            text="开始排位",
            command=self.start_ranked,
            bg="yellow",
            font=("", 14, "bold"),
            takefocus=0,
        )
        start_rank_btn.grid(row=0, column=1, sticky="w", padx=(10, 0))

        estimates = (
            "估价1000000", "估价900000", "估价800000",
            "估价700000", "估价600000", "估价500000",
            "估价360000",
        )
        grid = ttk.Frame(estimate_view, padding=(10, 0))
        grid.pack(fill="x")
        for i, title in enumerate(estimates):
            row, col = divmod(i, 3)
            ttk.Button(grid, text=title, width=18, takefocus=0, command=lambda t=title: self.proxy_command(t)).grid(
                row=row, column=col, padx=3, pady=3, sticky="w"
            )

        inputs = ttk.Frame(estimate_view, padding=(10, 10))
        inputs.pack(fill="x")
        self.estimate_input = ttk.Entry(inputs, width=18)
        self.estimate_input.pack(side="left")
        self._placeholder(self.estimate_input, "单价（万）")
        self.estimate_yuan_input = ttk.Entry(inputs, width=18)
        self.estimate_yuan_input.pack(side="left", padx=(10, 0))
        self._placeholder(self.estimate_yuan_input, "单价（元）")
        # 在任一估价输入框按回车，等价于点击「确认估价」。
        self.estimate_input.bind("<Return>", self._on_estimate_return)
        self.estimate_yuan_input.bind("<Return>", self._on_estimate_return)
        ttk.Button(inputs, text="确认估价", command=self.confirm_bid, takefocus=0).pack(side="left", padx=(10, 0))
        ttk.Button(inputs, text="上次出价", command=self.last_estimate, takefocus=0).pack(side="left", padx=(10, 0))

        # ----- 估价任务（定时循环） -----
        task = ttk.Frame(estimate_view, padding=(10, 0))
        task.pack(fill="x")
        ttk.Button(task, text="开始任务", width=20, command=self.start_task, takefocus=0).pack(side="left")
        ttk.Button(task, text="停止任务", width=20, command=self.stop_task, takefocus=0).pack(side="left", padx=(10, 0))

        # ----- 本局记录 -----
        profit = ttk.Frame(estimate_view, padding=(10, 10))
        profit.pack(fill="x")
        self.profit_input = ttk.Entry(profit, width=18)
        self.profit_input.pack(side="left")
        self._placeholder(self.profit_input, "本局收益（元）")
        ttk.Button(profit, text="记录收益", command=self.record_profit, takefocus=0).pack(side="left", padx=(10, 0))
        ttk.Button(profit, text="开始本局", command=self.start_session, takefocus=0).pack(side="left", padx=(10, 0))
        ttk.Button(profit, text="结束本局", command=self.end_session, takefocus=0).pack(side="left", padx=(10, 0))

        session_frame = ttk.Frame(estimate_view, padding=(10, 0, 10, 10))
        session_frame.pack(fill="both", expand=True)
        session_scroll = ttk.Scrollbar(session_frame, orient="vertical")
        self.session_log = tk.Text(
            session_frame, wrap="word", font=("", 11), height=6, yscrollcommand=session_scroll.set
        )
        session_scroll.config(command=self.session_log.yview)
        session_scroll.pack(side="right", fill="y")
        self.session_log.pack(side="left", fill="both", expand=True)
        self.session_log.configure(state="disabled")

        # ----- 记录页 -----
        records_scroll = ttk.Scrollbar(records_view, orient="vertical")
        self.records_text = tk.Text(
            records_view, wrap="word", font=("", 11), yscrollcommand=records_scroll.set
        )
        records_scroll.config(command=self.records_text.yview)
        records_scroll.pack(side="right", fill="y")
        self.records_text.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(10, 0))
        self.records_text.configure(state="disabled")

        records_nav = ttk.Frame(records_view, padding=(10, 5))
        records_nav.pack(fill="x")
        self.page_label = ttk.Label(records_nav, text="")
        self.page_label.pack(side="left")
        ttk.Button(records_nav, text="上一页", command=self.prev_records_page).pack(
            side="right", padx=(10, 0)
        )
        ttk.Button(records_nav, text="下一页", command=self.next_records_page).pack(
            side="right", padx=(10, 0)
        )
        ttk.Button(records_nav, text="刷新", command=self.refresh_records).pack(
            side="right", padx=(10, 0)
        )

        # ----- 工作页 -----
        work = ttk.Frame(work_view, padding=(10, 10))
        work.pack(fill="x")
        ttk.Button(
            work, text="打开监控", width=20,
            command=lambda: self.open_url(MONITOR_URL, "监控"),
        ).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Button(
            work, text="打开TTS 监控", width=20,
            command=lambda: self.open_url(TTS_MONITOR_URL, "TTS 监控"),
        ).grid(row=1, column=0, sticky="w", pady=3)
        ttk.Button(
            work, text="打开MSE 配置", width=20,
            command=lambda: self.open_url(MSE_CONFIG_URL, "MSE 配置"),
        ).grid(row=2, column=0, sticky="w", pady=3)
        ttk.Button(
            work, text="打开gts 应用", width=20,
            command=lambda: self.open_url(GTS_APP_URL, "gts 应用"),
        ).grid(row=3, column=0, sticky="w", pady=3)
        ttk.Button(
            work, text="标识满意", width=20,
            command=lambda: self.send_command("标识满意"),
        ).grid(row=4, column=0, sticky="w", pady=3)
        ttk.Button(
            work, text="截屏", width=20,
            command=self.capture_screenshot,
        ).grid(row=5, column=0, sticky="w", pady=3)

        # ----- 快捷命令页 -----
        commands = (
            ("捡快递", "捡快递"),
            ("上架物品", "上架物品"),
            ("领取奖励", "领取奖励"),
            ("打开商店", "打开商店"),
            ("单击", "单击"),
            ("上架当前物品", "上架当前物品"),
        )
        buttons = ttk.Frame(action_view, padding=(10, 0))
        buttons.pack(fill="x")
        for i, (title, command) in enumerate(commands):
            ttk.Button(
                buttons, text=title, width=28,
                command=lambda c=command: self.proxy_command(c),
            ).grid(row=i, column=0, sticky="w", pady=3)

    @staticmethod
    def _placeholder(entry, text):
        entry.insert(0, text)
        entry.configure(foreground="grey")

        def on_focus_in(_):
            if entry.get() == text:
                entry.delete(0, "end")
                entry.configure(foreground="black")

        def on_focus_out(_):
            if not entry.get():
                entry.insert(0, text)
                entry.configure(foreground="grey")

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

    def _entry_value(self, entry, placeholder):
        value = (entry.get() or "").strip()
        if value == placeholder:
            return ""
        return value

    # ---------- 文本操作 ----------
    def toggle_edit(self):
        self._editable = not self._editable
        self.text.configure(state="normal" if self._editable else "disabled")
        self.edit_button.configure(text="只读" if self._editable else "编辑")
        self.status.configure(text="显示区可编辑" if self._editable else "显示区只读")

    def copy_text(self):
        text = self.text.get("1.0", "end-1c")
        if not text:
            self.status.configure(text="无可复制内容")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.status.configure(text=f"已复制 {len(text)} 个字符到剪贴板")

    def clear_text(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="normal" if self._editable else "disabled")
        self.status.configure(text=self._idle_status() if self._listening else self._device_status())

    def _append(self, text, tag=None):
        state = self.text.cget("state")
        self.text.configure(state="normal")
        prev = self.text.get("1.0", "end-1c")
        sep = "\n" if prev.strip() else ""
        self.text.insert("end", sep + text, (tag,) if tag else ())
        self.text.see("end")
        self.text.configure(state=state)

    # ---------- 打开外部页面 ----------
    def open_url(self, url, label):
        try:
            ok = webbrowser.open(url)
            self.status.configure(text=f"已在浏览器打开{label}" if ok else f"[错误] 无法打开: {url}")
        except Exception as exc:
            self.status.configure(text=f"[错误] 打开{label}失败: {exc}")

    # ---------- 截屏 ----------
    def capture_screenshot(self):
        """截取整个屏幕，保存到 assets/screenshots 目录。"""
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(
            SCREENSHOT_DIR, datetime.now().strftime("screenshot_%Y%m%d_%H%M%S_%f.png")
        )
        self.status.configure(text=f"正在截屏，保存到 {path} …")

        def _shot():
            try:
                self._capture_screen(path)
                print(f"[screenshot] saved: {path}")
                self._ui(lambda: self.status.configure(text=f"截屏已保存: {path}"))
            except Exception as exc:
                print(f"[screenshot] failed: {exc}")
                self._ui(lambda e=exc: self.status.configure(text=f"[错误] 截屏失败: {e}"))

        threading.Thread(target=_shot, daemon=True).start()

    @staticmethod
    def _capture_screen(path):
        """截取屏幕并保存到 path；优先用 macOS 原生 screencapture，失败回退 pyautogui。"""
        try:
            subprocess.run(["screencapture", "-x", path], check=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        if pyautogui is None:
            raise RuntimeError("截屏失败：未找到 screencapture，且未安装 pyautogui")
        pyautogui.screenshot(path)

    # ---------- 远程命令 ----------
    def send_command(self, command):
        self.status.configure(text=f"正在调用 {self.command_url} … ({command})")
        threading.Thread(target=self._send_command, args=(command,), daemon=True).start()

    def _send_command(self, command):
        try:
            resp = requests.post(self.command_url, json={"command": command}, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            if result.get("code", 0) != 0:
                self._ui(lambda: self.status.configure(text=f"[错误] {command} 失败: {result}"))
            else:
                self._ui(lambda: self.status.configure(text=f"{command} 已发送"))
        except Exception as exc:
            self._ui(lambda e=exc: self.status.configure(text=f"[错误] {command} 失败: {e}"))

    def use_game_tool(self):
        """点击「使用道具」：直接调用 bot.proxy.send_remote_command。

        若点击前焦点在输入区（Entry / 可编辑文本区），先使其失去焦点，
        再把焦点转到「使用道具」按钮上。
        """
        if self._focus_is_editable_input():
            self.root.focus_set()
            self.use_tool_btn.focus_set()
        self.proxy_command("使用道具")

    def proxy_command(self, command):
        """直接调用 bot.proxy.send_remote_command 发送命令（不经本机 /command/remote 转发）。"""
        if proxy is None:
            self.status.configure(text=f"[错误] 未导入 bot.proxy，无法执行：{command}")
            return
        # 去重：同一命令在 command_debounce 秒内重复到达时只发送第一次，
        # 避免「按钮空格激活」与「bind_all 空格触发」等路径各发一次。
        now = time.monotonic()
        if command == self._last_command and now - self._last_command_ts < self._command_debounce:
            return
        self._last_command = command
        self._last_command_ts = now
        self.status.configure(text=f"正在调用 proxy.send_remote_command … ({command})")
        threading.Thread(target=self._proxy_command, args=(command,), daemon=True).start()

    def _proxy_command(self, command):
        try:
            result = proxy.send_remote_command(command)
            self._ui(lambda: self.status.configure(text=f"{command} 已发送: {result}"))
        except Exception as exc:
            self._ui(lambda e=exc, c=command: self.status.configure(text=f"[错误] {c} 失败: {e}"))

    def _on_estimate_return(self, _event):
        """在估价输入框按回车：等价于点击「确认估价」。"""
        self.confirm_bid()
        return "break"

    def confirm_bid(self):
        raw_yuan = self._entry_value(self.estimate_yuan_input, "单价（元）")
        if raw_yuan:
            try:
                value = int(round(float(raw_yuan)))
            except ValueError:
                self.status.configure(text=f"无效的估价输入（元）：{raw_yuan}")
                return
            self.proxy_command(f"估价{value}")
            return

        raw = self._entry_value(self.estimate_input, "单价（万）")
        if not raw:
            self.status.configure(text="请输入估价数字（元或万）")
            return
        try:
            value = int(round(float(raw) * 10000))
        except ValueError:
            self.status.configure(text=f"无效的估价输入：{raw}")
            return
        self._last_estimate_wan = float(raw)
        self.proxy_command(f"估价{value}")

    def last_estimate(self):
        value = self._last_estimate_wan
        self.estimate_input.delete(0, "end")
        self.estimate_input.insert(0, f"{value:g}")
        self.estimate_input.configure(foreground="black")
        self.proxy_command(f"估价{int(round(value * 10000))}")

    def start_ranked(self):
        """点击「开始排位」：若尚未开始本局则自动开始，再发送开始排位命令。"""
        if not self._session_active:
            self.start_session()
        self.send_command("开始排位")

    # ---------- 估价任务（定时循环） ----------
    def start_task(self):
        """点击「开始任务」：每 task_interval 秒发送「估价20000」，
        task_estimate_count 次后发送「开始排位」，循环执行直到停止。"""
        if self._task_running:
            self.status.configure(text="任务已在运行中")
            return
        self._task_running = True
        self._task_stop.clear()
        self.status.configure(
            text=f"任务已启动：每 {self.task_interval:g}s 估价20000，"
                 f"{self.task_estimate_count} 次后开始排位"
        )
        self._task_thread = threading.Thread(target=self._task_loop, daemon=True)
        self._task_thread.start()

    def stop_task(self):
        """点击「停止任务」：停止正在运行的估价任务。"""
        if not self._task_running:
            self.status.configure(text="任务未在运行")
            return
        self._task_running = False
        self._task_stop.set()
        self.status.configure(text="任务已停止")

    def _task_send(self, command):
        """任务内发送命令到 /command/remote；失败只记录日志与状态，不抛出。"""
        try:
            resp = requests.post(self.command_url, json={"command": command}, timeout=30)
            resp.raise_for_status()
            print(f"[task] sent {command}: {resp.json()}")
        except Exception as exc:
            print(f"[task] 发送 {command} 失败: {exc}")
            self._ui(lambda e=exc, c=command: self.status.configure(text=f"[任务错误] {c} 发送失败: {e}"))

    def _task_loop(self):
        count = self.task_estimate_count
        while self._task_running:
            for i in range(count):
                if not self._task_running:
                    return
                self._task_send("估价20000")
                self._ui(lambda i=i: self.status.configure(
                    text=f"任务进行中：估价20000 {i + 1}/{count}"
                ))
                if i < count - 1 and self._task_stop.wait(self.task_interval):
                    return
            if not self._task_running:
                return
            self._task_send("开始排位")
            self._ui(lambda: self.status.configure(text="任务：已发送开始排位"))
            if self._task_stop.wait(self.task_interval):
                return
        self._ui(lambda: self.status.configure(text="任务已结束"))

    # ---------- 本局记录 ----------
    def _load_records(self):
        """从文件读取历史对局记录；文件不存在或损坏时返回空列表。"""
        try:
            with open(self._records_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"[error] 读取本局记录失败: {exc}")
        return []

    def _save_records(self):
        """把已结束的对局记录写入文件。"""
        try:
            os.makedirs(os.path.dirname(self._records_path), exist_ok=True)
            with open(self._records_path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[error] 保存本局记录失败: {exc}")

    def _render_session(self):
        """把当前本局的开始时间、每次收益与结束时间渲染到日志区。"""
        lines = []
        if self._session_start is not None:
            lines.append(f"本局开始: {self._session_start}")
        for i, (t, amount) in enumerate(self._session_profits, 1):
            lines.append(f"收益{i}: {amount:g} 元  ({t})")
        if self._session_profits:
            total = sum(amount for _, amount in self._session_profits)
            lines.append(f"合计收益: {total:g} 元")
        if self._session_end is not None:
            lines.append(f"本局结束: {self._session_end}")
        if not lines:
            lines.append("尚未开始本局：点击「开始本局」后填写收益并「记录收益」。")
        self.session_log.configure(state="normal")
        self.session_log.delete("1.0", "end")
        self.session_log.insert("1.0", "\n".join(lines))
        self.session_log.configure(state="disabled")

    def start_session(self):
        """点击「开始本局」：记录开始时间并清空本局收益。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._session_start = now
        self._session_end = None
        self._session_profits = []
        self._session_active = True
        self._render_session()
        self.status.configure(text=f"已开始本局：{now}")

    def record_profit(self):
        """点击「记录收益」：读取输入框金额，追加到本局记录。"""
        raw = self._entry_value(self.profit_input, "本局收益（元）")
        if not raw:
            self.status.configure(text="请输入收益金额（元）")
            return
        try:
            amount = float(raw)
        except ValueError:
            self.status.configure(text=f"无效的收益输入：{raw}")
            return

        if self._session_start is None:
            self.start_session()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._session_profits.append((now, amount))
        self.profit_input.delete(0, "end")
        self._placeholder(self.profit_input, "本局收益（元）")
        self._render_session()
        total = sum(a for _, a in self._session_profits)
        self.status.configure(text=f"已记录收益 {amount:g} 元，本局合计 {total:g} 元")

    def end_session(self):
        """点击「结束本局」：记录结束时间、汇总并持久化到文件。"""
        if self._session_start is None:
            self.status.configure(text="尚未开始本局，无法结束")
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._session_end = now
        self._session_active = False
        record = {
            "start": self._session_start,
            "end": now,
            "profits": [{"time": t, "amount": a} for t, a in self._session_profits],
            "total": sum(a for _, a in self._session_profits),
        }
        self._records.append(record)
        self._save_records()
        self._render_session()
        self._record_page = 0
        self._render_records_page()
        self.status.configure(text=f"本局结束：{now}，合计收益 {record['total']:g} 元")

    # ---------- 记录页 ----------
    def _format_record(self, index, rec):
        """把单条对局记录格式化为多行文本。index 为该局的序号（从 1 开始）。"""
        lines = [f"第 {index} 局"]
        lines.append(f"    开始: {rec.get('start', '-')}")
        lines.append(f"    结束: {rec.get('end', '-')}")
        profits = rec.get("profits") or []
        if profits:
            detail = "，".join(
                f"{p.get('amount', 0):g} 元 ({str(p.get('time', ''))[-8:]})"
                for p in profits
            )
            lines.append(f"    收益: {detail}")
        lines.append(f"    合计: {rec.get('total', 0):g} 元")
        return "\n".join(lines)

    def _record_pages(self):
        total = len(self._records)
        per = self.records_per_page
        return max(1, (total + per - 1) // per)

    def _render_records_page(self):
        """渲染当前页（每页 records_per_page 条，最新的一局排在最前）。"""
        total = len(self._records)
        pages = self._record_pages()
        if self._record_page >= pages:
            self._record_page = pages - 1
        if self._record_page < 0:
            self._record_page = 0

        per = self.records_per_page
        ordered = list(reversed(self._records))
        start = self._record_page * per
        chunk = ordered[start:start + per]

        blocks = []
        for i, rec in enumerate(chunk):
            index = total - (start + i)  # 原始序号（1 开始）
            blocks.append(self._format_record(index, rec))

        self.records_text.configure(state="normal")
        self.records_text.delete("1.0", "end")
        if blocks:
            self.records_text.insert("1.0", "\n\n".join(blocks))
        else:
            self.records_text.insert("1.0", "暂无游戏记录。")
        self.records_text.configure(state="disabled")

        self.page_label.configure(text=f"第 {self._record_page + 1}/{pages} 页  ·  共 {total} 局")

    def prev_records_page(self):
        """点击「上一页」：翻到更接近最新的一页。"""
        if self._record_page > 0:
            self._record_page -= 1
            self._render_records_page()

    def next_records_page(self):
        """点击「下一页」：翻到更早的一页。"""
        if self._record_page < self._record_pages() - 1:
            self._record_page += 1
            self._render_records_page()

    def refresh_records(self):
        """点击「刷新」：从本地文件重新加载记录并回到第一页。"""
        self._records = self._load_records()
        self._record_page = 0
        self._render_records_page()
        self.status.configure(text=f"已刷新，共 {len(self._records)} 局记录")

    # ---------- 快捷键 ----------
    def _install_hotkeys(self):
        # 窗口内快捷键（对齐旧版 AppKit local monitor 语义：命中后消费事件）
        self.root.bind_all("<KeyPress-space>", self._on_space_event)
        self.root.bind_all("<KeyPress-Shift_L>", self._on_shift_event)
        self.root.bind_all("<KeyPress-Shift_R>", self._on_shift_event)
        self.root.bind_all("<KeyRelease-Shift_L>", self._on_shift_release)
        self.root.bind_all("<KeyRelease-Shift_R>", self._on_shift_release)

        if self.global_hotkeys:
            self._install_global_hotkeys()

    def _focus_is_editable_input(self):
        widget = self.root.focus_get()
        if widget is None:
            return False
        if isinstance(widget, ttk.Entry) or isinstance(widget, tk.Entry):
            return True
        if widget is self.text and self._editable:
            return True
        return False

    def _on_space_event(self, event):
        if self._focus_is_editable_input():
            return None  # 让空格正常输入
        self._trigger_space()
        return "break"

    def _on_shift_event(self, event):
        if not self._shift_down:
            self._shift_down = True
            self.toggle_listen()
        return "break"

    def _on_shift_release(self, event):
        self._shift_down = False
        return "break"

    def _current_tab_identifier(self):
        try:
            return self.notebook.tab(self.notebook.select(), "text")
        except Exception:
            return ""

    def _trigger_space(self):
        # 去抖：全局热键(pynput)与窗口绑定(tkinter)可能对同一次空格各触发一次，
        # 短时间内重复到达时只处理第一次，避免发出两次命令。
        now = time.monotonic()
        if now - self._last_space_ts < self._space_debounce:
            return
        self._last_space_ts = now

        tab = self._current_tab_identifier()
        # 「快捷命令」页空格相当于在当前位置按下鼠标左键（本机执行）；
        # 「估价」页（及其它页）等价于「使用道具」。
        if tab == "快捷命令":
            self._local_left_click()
        else:
            self.proxy_command("使用道具")

    def _local_left_click(self):
        """在当前位置执行一次鼠标左键点击（本机 pyautogui）。

        先用 position() 取得当前位置，再以该坐标调用 click(x, y)。
        """
        if pyautogui is None:
            self.status.configure(text="[错误] 未安装 pyautogui，无法模拟鼠标左键")
            return

        def _click():
            try:
                x, y = pyautogui.position()
                pyautogui.click(x, y)
                self._ui(lambda: self.status.configure(text=f"已在本机 ({x}, {y}) 点击鼠标左键"))
            except Exception as exc:
                self._ui(lambda e=exc: self.status.configure(text=f"[错误] 鼠标左键点击失败: {e}"))

        threading.Thread(target=_click, daemon=True).start()

    def _install_global_hotkeys(self):
        if pynput_keyboard is None:
            print("[warn] 未安装 pynput，无法启用全局快捷键")
            return

        try:
            shift_keys = (
                pynput_keyboard.Key.shift,
                pynput_keyboard.Key.shift_l,
                pynput_keyboard.Key.shift_r,
            )
        except AttributeError:
            shift_keys = ()

        def on_press(key):
            try:
                if key in shift_keys:
                    if not self._shift_down:
                        self._shift_down = True
                        self._ui(self.toggle_listen)
                elif key == pynput_keyboard.Key.space:
                    self._ui(self._trigger_space)
            except Exception:
                pass

        def on_release(key):
            try:
                if key in shift_keys:
                    self._shift_down = False
            except Exception:
                pass

        self._kb_listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
        self._kb_listener.start()

    # ---------- 意图浮层 ----------
    def _ensure_overlay(self):
        if self._overlay is not None:
            return self._overlay
        width, height = 900, 260
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.78)
        except Exception:
            pass
        win.configure(bg="black")
        label = tk.Label(win, text="", bg="black", fg="white", font=("", 72, "bold"))
        label.pack(expand=True, fill="both")
        win.withdraw()
        self._overlay = win
        self._overlay_label = label
        return win

    def _flash_intent(self, intent):
        try:
            self._dismiss_overlay()
            width, height = 900, 260
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = (sw - width) // 2
            y = (sh - height) // 2
            win = self._ensure_overlay()
            win.geometry(f"{width}x{height}+{x}+{y}")
            self._overlay_label.configure(text=f"即将{intent}")
            win.deiconify()
            win.lift()
            self._overlay_timer = self.root.after(2500, self._dismiss_overlay)
        except Exception as exc:
            print(f"[error] 显示意图浮层失败: {exc}")

    def _dismiss_overlay(self):
        if self._overlay_timer is not None:
            try:
                self.root.after_cancel(self._overlay_timer)
            except Exception:
                pass
            self._overlay_timer = None
        if self._overlay is not None:
            try:
                self._overlay.withdraw()
            except Exception:
                pass

    # ---------- 监听启停 ----------
    def toggle_listen(self):
        if self._listening:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self):
        os.makedirs(WAV_DIR, exist_ok=True)

        capture, rate = self._open_capture()
        if capture is None:
            return

        self._proc = capture
        self._acc = bytearray()
        self._listening = True
        self._restart_requested.clear()
        self.button.configure(text="停止监听")
        self.status.configure(text="监听中… 正在校准环境噪声")
        self._reader = threading.Thread(target=self._vad_loop, args=(capture, rate), daemon=True)
        self._reader.start()

    def _open_capture(self):
        """打开录音源。macOS 优先 ffmpeg(avfoundation)，否则用 sounddevice。

        返回 (capture, rate)；失败时返回 (None, None) 并更新界面提示。
        """
        # macOS：优先 ffmpeg，失败再回退 sounddevice。
        if _SYSTEM == "Darwin" and _ffmpeg_available():
            cap = FfmpegCapture(self.audio_device, SAMPLE_RATE)
            try:
                cap.start()
                print(f"[audio] macOS 使用 ffmpeg avfoundation 设备 [{self.audio_device}]")
                return cap, SAMPLE_RATE
            except Exception as exc:
                print(f"[audio] ffmpeg 采集失败，回退 sounddevice: {exc}")

        if sd is None:
            self.text_set(
                "[错误] 无可用录音后端：macOS 请安装 ffmpeg（brew install ffmpeg）；"
                "其它平台请安装 sounddevice（pip install sounddevice）"
            )
            return None, None

        rate = _pick_samplerate(self.audio_device)
        frame_samples = max(1, int(rate * self.frame_ms / 1000))
        capture = AudioCapture(self.audio_device, rate, frame_samples)
        try:
            capture.start()
        except Exception as exc:
            self.text_set(f"[错误] 打开录音设备失败: {exc}")
            return None, None
        return capture, rate

    def _stop_listening(self):
        self._listening = False
        self._restart_requested.clear()
        proc = self._proc
        self._proc = None
        if proc is not None:
            proc.stop()
        self.button.configure(text="开始监听")
        self.status.configure(text=self._device_status())

    def restart_recording(self):
        if not self._listening:
            self.status.configure(text="未在监听，无法重新录制")
            return
        self._epoch += 1
        self._restart_requested.set()
        self.status.configure(text="已请求忽略此前音频，将从当前时间重新录制…")

    def text_set(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", text)
        self.text.configure(state="normal" if self._editable else "disabled")

    # ---------- 音频读取 ----------
    def _read_frame(self, capture, frame_bytes):
        while len(self._acc) < frame_bytes:
            chunk = capture.read(0.5)
            if chunk is None:
                if not self._listening:
                    return None
                continue
            self._acc.extend(chunk)
        frame = bytes(self._acc[:frame_bytes])
        del self._acc[:frame_bytes]
        return frame

    def _calibrate(self, capture, frame_bytes):
        if self.threshold_override:
            try:
                return float(self.threshold_override)
            except (TypeError, ValueError):
                pass
        n = max(1, int(500 / self.frame_ms))
        levels = []
        for _ in range(n):
            data = self._read_frame(capture, frame_bytes)
            if data is None:
                return None
            levels.append(_rms(data))
        noise = sorted(levels)[len(levels) // 2]
        threshold = max(0.010, noise * 3.0)
        self._ui(lambda: self.status.configure(
            text=f"已校准: 噪声 {noise:.4f} / 阈值 {threshold:.4f} | {self._device_status()}"
        ))
        return threshold

    # ---------- VAD 主循环（后台线程） ----------
    def _vad_loop(self, capture, rate):
        frame_bytes = max(1, int(rate * self.frame_ms / 1000)) * 2

        threshold = self._calibrate(capture, frame_bytes)
        if threshold is None:
            self._ui(lambda: self.status.configure(text="[错误] 无法读取音频，请检查麦克风权限/设备"))
            self._listening = False
            return

        pre_roll = deque(maxlen=max(1, int(300 / self.frame_ms)))
        silence_limit = max(1, int(self.silence_seconds * 1000 / self.frame_ms))
        max_frames = max(1, int(self.max_seconds * 1000 / self.frame_ms))
        min_speech_frames = max(1, int(90 / self.frame_ms))

        self._ui(lambda: self.status.configure(text=self._idle_status()))

        recording = False
        buffer = []
        silence_frames = 0
        speech_frames = 0
        voiced_frames = 0
        # 上次录音结束（断句）的时刻，用于强制两次录音间隔 >= min_record_interval。
        last_end_ts = 0.0

        while self._listening:
            if self._restart_requested.is_set():
                self._restart_requested.clear()
                pre_roll.clear()
                buffer = []
                recording = False
                silence_frames = 0
                speech_frames = 0
                voiced_frames = 0
                last_end_ts = 0.0
                self._ui(lambda: self.status.configure(text="已忽略此前音频，从当前时间开始重新录制…"))
                continue

            data = self._read_frame(capture, frame_bytes)
            if data is None:
                break
            level = _rms(data)

            if not recording:
                pre_roll.append(data)
                if level >= threshold:
                    speech_frames += 1
                    if speech_frames >= min_speech_frames:
                        # 距上次录音结束不足 min_record_interval 时不开始新录音，
                        # 丢弃已累积的语音帧，等待间隔满足后再重新判定。
                        if last_end_ts and time.monotonic() - last_end_ts < self.min_record_interval:
                            speech_frames = 0
                            pre_roll.clear()
                            continue
                        recording = True
                        rec_start = time.perf_counter()
                        buffer = list(pre_roll)
                        pre_roll.clear()
                        silence_frames = 0
                        voiced_frames = speech_frames
                        self._ui(lambda: self.status.configure(text="检测到语音，录音中…"))
                else:
                    speech_frames = 0
            else:
                buffer.append(data)
                if level >= threshold:
                    silence_frames = 0
                    voiced_frames += 1
                else:
                    silence_frames += 1
                if silence_frames >= silence_limit or len(buffer) >= max_frames:
                    voiced_ms = voiced_frames * self.frame_ms
                    reason = "silence" if silence_frames >= silence_limit else "max_seconds"
                    print(f"[timing] client endpoint wait ({reason} {silence_frames * self.frame_ms}ms): "
                          f"{time.perf_counter() - rec_start:.3f}s since speech start")
                    if voiced_ms >= self.min_speech_ms:
                        frames = self._trim(buffer, threshold)
                        threading.Thread(
                            target=self._process_utterance,
                            args=(frames, rate, self._epoch),
                            daemon=True,
                        ).start()
                    else:
                        self._ui(lambda: self.status.configure(
                            text=f"忽略短促噪声（{voiced_ms}ms < {self.min_speech_ms:g}ms）"
                        ))
                    recording = False
                    buffer = []
                    silence_frames = 0
                    speech_frames = 0
                    voiced_frames = 0
                    last_end_ts = time.monotonic()
                    if self.min_record_interval > 0:
                        self._ui(lambda: self.status.configure(
                            text=f"静默等待 {self.min_record_interval:g}s 后可再次录音…"
                        ))
                    else:
                        self._ui(lambda: self.status.configure(text=self._idle_status()))

        self._ui(lambda: self.status.configure(text=self._device_status()))

    def _trim(self, frames, threshold):
        keep_tail = max(1, int(200 / self.frame_ms))
        end = len(frames)
        removed = 0
        while end > 1 and removed < len(frames) - keep_tail and _rms(frames[end - 1]) < threshold:
            end -= 1
            removed += 1
        return frames[:end]

    # ---------- 转写 ----------
    def _process_utterance(self, frames, rate, epoch=0):
        path = os.path.join(WAV_DIR, datetime.now().strftime("rec_%Y%m%d_%H%M%S_%f.wav"))
        t0 = time.perf_counter()
        try:
            pcm = _resample(b"".join(frames), rate, SAMPLE_RATE)
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm)
        except Exception as exc:
            self._ui(lambda e=exc: self._append(f"[错误] 写文件失败: {e}"))
            return
        t1 = time.perf_counter()
        print(f"[timing] client wav write ({len(frames)} frames): {t1 - t0:.3f}s")

        # 转写/意图回调：在 process_voice_file 内部的关键节点立即更新界面，
        # 从而在调用远程接口之前就先显示文本与意图。
        def on_transcribed(text, channel):
            if epoch != self._epoch:
                return
            tag = "local" if channel == "local" else ("siliconflow" if channel == "siliconflow" else None)
            label = {"local": "语音识别-本地模型", "siliconflow": "SiliconFlow"}.get(channel, "")
            display = f"[{label}] {text}" if label else text
            self._ui(lambda: self._append(display, tag))

        def on_intent(intent, channel):
            if epoch != self._epoch or not intent:
                return
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[intent] {now} {intent}")
            # 输出文本区与屏幕浮层都立即提示，此时远程接口尚未调用。
            self._ui(lambda: self.status.configure(text=f"意图：{intent}（正在执行…）"))
            self._ui(lambda: self._append(f"[{now}] 意图: {intent}"))
            self._ui(lambda: self._flash_intent(intent))

        text, intent, channel = self._transcribe(path, on_transcribed, on_intent)
        t2 = time.perf_counter()
        print(f"[timing] client round-trip (upload+server): {t2 - t1:.3f}s")
        if text is None:
            return
        if epoch != self._epoch:
            print("discarded transcription from before restart-recording")
            return
        if not intent:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[intent] {now} (empty) text={text!r}")

    def _transcribe(self, path, on_transcribed=None, on_intent=None):
        t0 = time.perf_counter()
        if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
            return "[错误] 录音文件为空，请检查麦克风权限/设备", "", ""

        # 优先本地处理：直接调用 app.process_voice_file（转写->意图->分发）。
        if ASR_LOCAL and process_voice_file is not None:
            return self._process_local(path, t0, on_transcribed, on_intent)

        # HTTP 回退路径：接口不提供回调，这里自行在拿到结果后触发回调，
        # 并对「未知」意图同样跳过意图提示。
        text, intent, channel = self._process_http(path, t0)
        if text is not None and on_transcribed is not None and text and not text.startswith("[错误]"):
            try:
                on_transcribed(text, channel)
            except Exception as exc:
                print(f"on_transcribed callback failed: {exc}")
        if text is not None and intent and intent not in UNKNOWN_INTENTS and on_intent is not None:
            try:
                on_intent(intent, channel)
            except Exception as exc:
                print(f"on_intent callback failed: {exc}")
        return text, intent, channel

    def _process_local(self, path, t0, on_transcribed=None, on_intent=None):
        """本地调用处理录音文件（不经 HTTP 接口）。返回 (text, intent, channel)。

        on_transcribed / on_intent 会透传给 app.process_voice_file，在转写完成、
        意图识别完成（但尚未执行动作）时回调，使界面提示不滞后。
        """
        try:
            text, intent, error, channel = process_voice_file(
                path, on_transcribed=on_transcribed, on_intent=on_intent
            )
            t1 = time.perf_counter()
            print(f"[timing] client local process_voice_file ({os.path.getsize(path)} bytes): "
                  f"{t1 - t0:.3f}s (channel={channel})")
            if error is not None:
                return f"[错误] {error}", intent or "", channel or ""
            return (text or "（未识别到内容）"), (intent or ""), (channel or "")
        except Exception as exc:
            return f"[错误] 本地处理失败: {exc}", "", ""

    def _process_http(self, path, t0):
        """回退实现：把录音 POST 到 /command/voice 接口。返回 (text, intent, channel)。"""
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                resp = requests.post(
                    self.url,
                    files={"audio": ("rec.wav", f, "audio/wav")},
                    timeout=self.asr_timeout,
                )
            t1 = time.perf_counter()
            print(f"[timing] client POST {self.url} ({size} bytes): {t1 - t0:.3f}s")
            try:
                payload = resp.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                text = (payload.get("text") or "").strip()
                intent = (payload.get("intent") or "").strip()
                channel = (payload.get("channel") or "").strip()
                if resp.status_code >= 400 and not text:
                    return f"[错误] {payload.get('error') or payload}", intent, channel
                return text or "（未识别到内容）", intent, channel
            resp.raise_for_status()
            return resp.text.strip() or "（未识别到内容）", "", ""
        except requests.Timeout:
            t1 = time.perf_counter()
            print(f"[timing] client POST {self.url} 超时(>{self.asr_timeout:g}s): "
                  f"{t1 - t0:.3f}s，本次请求直接返回，不做任何动作")
            self._ui(lambda: self.status.configure(
                text=f"语音识别超时（>{self.asr_timeout:g}s），本次已忽略"
            ))
            return None, "", ""
        except Exception as exc:
            return f"[错误] {exc}", "", ""

    # ---------- 生命周期 ----------
    def on_close(self):
        self._dismiss_overlay()
        self._listening = False
        if self._proc is not None:
            try:
                self._proc.stop()
            except Exception:
                pass
            self._proc = None
        if self._kb_listener is not None:
            try:
                self._kb_listener.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    if _SYSTEM == "Darwin":
        if not _ffmpeg_available() and sd is None:
            print("[warn] macOS 未检测到 ffmpeg 且未安装 sounddevice，录音功能不可用。"
                  "请执行: brew install ffmpeg")
    elif sd is None:
        print("[warn] 未安装 sounddevice，录音功能不可用。请执行: pip install sounddevice")
    root = tk.Tk()
    client = AsrClient(root)
    root.protocol("WM_DELETE_WINDOW", client.on_close)
    root.mainloop()



if __name__ == "__main__":
    main()