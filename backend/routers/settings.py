"""ASR 模型设置 API。"""

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_FILE = settings.data_dir / "settings.json"

MODEL_SIZE_LABELS = {
    "large-v3": "large-v3 (3.0 GB · 最高精度)",
    "large-v2": "large-v2 (3.0 GB · 高精度)",
    "medium":   "medium  (1.5 GB · 推荐平衡)",
    "small":    "small  (0.5 GB · 快速)",
    "base":     "base   (0.15 GB · 更快)",
    "tiny":     "tiny   (0.08 GB · 最快)",
}
MODEL_SIZE_DESCS = {
    "large-v3": "3.0 GB · 最高精度，中文识别最佳，CPU 推理慢",
    "large-v2": "3.0 GB · 高精度，上一代大模型",
    "medium":   "1.5 GB · 精度与速度平衡，推荐 macOS CPU 使用",
    "small":    "0.5 GB · 快速推理，精度尚可",
    "base":     "0.15 GB · 更快，精度较低",
    "tiny":     "0.08 GB · 最快，精度最低",
}

DEFAULT_SETTINGS = {
    "asr_model": "whisperx",
    "asr_model_label": "WhisperX (OpenAI)",
    "asr_model_desc": "多语言通用模型，词级说话人分离，支持 pyannote 调优。",
    "whisper_model_size": "medium",
    "runninghub_api_key": "",
    "runninghub_workflow_id": "",
    "available_models": [
        {
            "value": "whisperx",
            "label": "WhisperX (OpenAI)",
            "desc": "多语言通用模型，词级说话人分离，支持 pyannote diarization 调优。"
        }
    ]
}

# ---------- 模型缓存检测 ----------

HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"

ASR_MODELS = {
    "large-v3": {"repo": "Systran/faster-whisper-large-v3", "size_gb": 3.0},
    "large-v2": {"repo": "Systran/faster-whisper-large-v2", "size_gb": 3.0},
    "medium":   {"repo": "Systran/faster-whisper-medium",   "size_gb": 1.5},
    "small":    {"repo": "Systran/faster-whisper-small",    "size_gb": 0.5},
    "base":     {"repo": "Systran/faster-whisper-base",     "size_gb": 0.15},
    "tiny":     {"repo": "Systran/faster-whisper-tiny",     "size_gb": 0.08},
}


def _check_hf_model_cached(repo: str) -> dict:
    """检测一个 HuggingFace 模型是否已缓存完成。"""
    dir_name = "models--" + repo.replace("/", "--")
    model_dir = HF_CACHE / dir_name
    if not model_dir.exists():
        return {"downloaded": False, "size_downloaded_gb": 0, "path": str(model_dir)}

    # 计算已下载大小
    total = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
    # 检查是否有 snapshot 链接（下载完成的标志）
    snapshots = list((model_dir / "snapshots").glob("*")) if (model_dir / "snapshots").exists() else []
    completed = len(snapshots) > 0

    return {
        "downloaded": completed,
        "size_downloaded_gb": round(total / (1024 ** 3), 1),
        "path": str(model_dir),
    }


# ModelScope 缓存目录（Windows 和 macOS 路径不同）
MS_CACHE = Path.home() / ".cache" / "modelscope" / "hub" / "models" / "iic"

FUNASR_MODELS = {
    "paraformer-large-vad-punc-spk": {
        "dir": "speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn",
        "label": "paraformer-large (综合·VAD+标点+说话人分离)",
        "size_gb": 1.0,
        "model_id": "iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn",
    },
    "fsmn-vad": {
        "dir": "speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "label": "FSMN-VAD (语音端点检测)",
        "size_gb": 0.01,
        "model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    },
    "cam++": {
        "dir": "speech_campplus_sv_zh-cn_16k-common",
        "label": "CAM++ (说话人识别)",
        "size_gb": 0.01,
        "model_id": "iic/speech_campplus_sv_zh-cn_16k-common",
    },
}


def _check_ms_model_cached(dir_name: str) -> dict:
    """检测 ModelScope 模型是否已缓存（以 model 文件存在为完成标志）。"""
    model_dir = MS_CACHE / dir_name
    # 检查是否有实际模型文件（.pt / .pth / .onnx）或配置文件
    downloaded = False
    if model_dir.exists():
        for pattern in ["model.pt", "pytorch_model.bin", "*.onnx", "config.json", "configuration.json"]:
            if list(model_dir.glob(pattern)):
                downloaded = True
                break
    size = 0
    if model_dir.exists():
        try:
            size = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
        except Exception:
            pass
    return {
        "downloaded": downloaded,
        "size_downloaded_gb": round(size / (1024 ** 3), 1),
        "path": str(model_dir),
    }


def check_models_cached(asr_model: str) -> tuple[bool, str]:
    """检查指定 ASR 引擎所需模型是否全部缓存。

    Returns:
        (is_cached, error_message) — is_cached=True 表示可以正常使用，
        error_message 在未缓存时包含提示信息。
    """
    if asr_model == "funasr":
        missing = []
        for name, info in FUNASR_MODELS.items():
            status = _check_ms_model_cached(info["dir"])
            if not status["downloaded"]:
                missing.append(f"  • {info['label']} ({info['model_id']})")
        if missing:
            msg = (
                "ASR 模型尚未下载，请先在右上角设置中点击「下载模型」按钮完成下载后再上传音频。\n"
                "缺少以下模型：\n" + "\n".join(missing)
            )
            return False, msg
        return True, ""

    if asr_model == "whisperx":
        # 读取用户配置的模型大小
        data = _load()
        size = data.get("whisper_model_size", "medium")
        info = ASR_MODELS.get(size)
        if not info:
            return False, f"未知 Whisper 模型大小: {size}"
        status = _check_hf_model_cached(info["repo"])
        if not status["downloaded"]:
            msg = (
                f"WhisperX {size} 模型尚未下载，请先在右上角设置中切换至 WhisperX 引擎并点击「下载模型」按钮。\n"
                f"模型大小约 {info['size_gb']} GB，首次下载需要几分钟。"
            )
            return False, msg
        # 额外检查：whisperx 本身是否安装
        try:
            import whisperx  # noqa: F401
        except ImportError:
            return False, "WhisperX 库未安装。请运行: pip install whisperx torch torchaudio"
        return True, ""

    return False, f"未知 ASR 引擎: {asr_model}"


@router.get("/models")
async def get_model_status():
    """返回所有模型缓存状态（含 WhisperX 和 FunASR）。"""
    models = []
    # WhisperX 模型
    for name, info in ASR_MODELS.items():
        status = _check_hf_model_cached(info["repo"])
        models.append({
            "name": name,
            "label": MODEL_SIZE_LABELS.get(name, f"faster-whisper-{name}"),
            "desc": MODEL_SIZE_DESCS.get(name, ""),
            "size_gb": info["size_gb"],
            "engine": "whisperx",
            **status,
        })
    # FunASR 模型
    for name, info in FUNASR_MODELS.items():
        status = _check_ms_model_cached(info["dir"])
        models.append({
            "name": name,
            "label": info["label"],
            "desc": "",
            "size_gb": info["size_gb"],
            "engine": "funasr",
            **status,
        })
    current = _load().get("whisper_model_size", "medium")
    return {"models": models, "current_model": current}


def _load() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            # 硬编码 WhisperX：强制覆盖旧配置中的 funasr 残留
            data["asr_model"] = "whisperx"
            data["asr_model_label"] = DEFAULT_SETTINGS["asr_model_label"]
            data["asr_model_desc"] = DEFAULT_SETTINGS["asr_model_desc"]
            data["available_models"] = DEFAULT_SETTINGS["available_models"]
            if "whisper_model_size" not in data:
                data["whisper_model_size"] = DEFAULT_SETTINGS["whisper_model_size"]
            if "runninghub_api_key" not in data:
                data["runninghub_api_key"] = DEFAULT_SETTINGS["runninghub_api_key"]
            if "runninghub_workflow_id" not in data:
                data["runninghub_workflow_id"] = DEFAULT_SETTINGS["runninghub_workflow_id"]
            return data
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def _save(data: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("")
async def get_settings():
    data = _load()
    # 返回时把 api_key 脱敏（只显示后 4 位）
    key = data.get("runninghub_api_key", "")
    data["runninghub_api_key"] = f"****{key[-4:]}" if len(key) > 4 else key
    return data


class UpdateSettings(BaseModel):
    asr_model: str | None = None
    whisper_model_size: str | None = None
    runninghub_api_key: str | None = None
    runninghub_workflow_id: str | None = None


@router.put("")
async def update_settings(body: UpdateSettings):
    data = _load()
    if body.asr_model is not None:
        data["asr_model"] = body.asr_model
        for m in data.get("available_models", []):
            if m["value"] == body.asr_model:
                data["asr_model_label"] = m["label"]
                data["asr_model_desc"] = m["desc"]
                break
    if body.whisper_model_size is not None:
        data["whisper_model_size"] = body.whisper_model_size
    if body.runninghub_api_key is not None:
        if not body.runninghub_api_key.startswith("****"):
            data["runninghub_api_key"] = body.runninghub_api_key
    if body.runninghub_workflow_id is not None:
        data["runninghub_workflow_id"] = body.runninghub_workflow_id
    _save(data)
    # 返回时脱敏
    key = data.get("runninghub_api_key", "")
    data["runninghub_api_key"] = f"****{key[-4:]}" if len(key) > 4 else key
    return data


# ── 模型预下载 ──

_download_state: dict = {"status": "idle", "message": "", "current": "", "total": 0, "done": 0}


@router.get("/download-model/status")
async def get_download_status():
    return _download_state


class DownloadModelRequest(BaseModel):
    engine: str  # "whisperx" or "funasr"
    model_size: str | None = None  # for whisperx: tiny/base/small/medium/large-v3


@router.post("/download-model")
async def download_model(body: DownloadModelRequest):
    """触发模型预下载（后台线程执行）。"""
    import threading

    if _download_state.get("status") == "downloading":
        raise HTTPException(400, detail="已有模型正在下载中，请等待完成")

    if body.engine == "whisperx":
        size = body.model_size or "medium"
        _download_state.update(
            status="downloading", message="正在准备 WhisperX 环境...",
            current="", total=2, done=0
        )
        threading.Thread(target=_download_whisperx_model, args=(size,), daemon=True).start()
    elif body.engine == "funasr":
        total = len(FUNASR_MODELS)
        _download_state.update(
            status="downloading", message="正在下载 FunASR 模型（约 1GB，从 ModelScope 国内源）...",
            current="", total=total, done=0
        )
        threading.Thread(target=_download_funasr_models, daemon=True).start()
    else:
        raise HTTPException(400, detail=f"未知引擎: {body.engine}")

    return _download_state


def _install_whisperx(python_exe: str):
    """安装 whisperx + pyannote.audio。

    策略：先尝试一键 pip install whisperx（带全部依赖）。
    如果遇到 ctranslate2==4.4.0 已下架问题，则回退到分步安装。
    所有 pip 调用依次尝试默认源 → 清华 → 阿里云。
    """
    import subprocess

    sources = [
        ("默认源", []),
        ("清华镜像", ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                      "--trusted-host", "pypi.tuna.tsinghua.edu.cn"]),
        ("阿里云镜像", ["-i", "https://mirrors.aliyun.com/pypi/simple/",
                       "--trusted-host", "mirrors.aliyun.com"]),
    ]

    def _try_pip(packages: list[str]) -> None:
        last_error = None
        for name, extra_args in sources:
            print(f"[settings] pip install {packages} via {name}...")
            cmd = [python_exe, "-m", "pip", "install", "--upgrade"]
            cmd.extend(extra_args)
            cmd.extend(packages)
            print(f"[settings]   {' '.join(cmd)}")
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                if r.returncode == 0:
                    print(f"[settings] pip {name} succeeded for {packages}")
                    return
                err_tail = r.stderr.strip().splitlines()[-5:] if r.stderr else []
                err_msg = "\n".join(err_tail) if err_tail else r.stdout.strip()[-500:]
                print(f"[settings] pip {name} FAILED:\n{err_msg}")
                last_error = err_msg or f"exit code {r.returncode}"
            except subprocess.TimeoutExpired:
                print(f"[settings] pip {name} timeout")
                last_error = "下载超时（>15分钟）"
        raise RuntimeError(f"{packages} 安装失败:\n{last_error}")

    # Step 1: 确保 PyTorch 已安装（start.bat 通常已装好）
    try:
        import torch  # noqa: F401
        print("[settings] torch already installed")
    except ImportError:
        print("[settings] Installing torch + torchaudio...")
        try:
            _try_pip(["torch", "torchaudio"])
        except RuntimeError:
            print("[settings] Trying PyTorch official source...")
            subprocess.check_call(
                [python_exe, "-m", "pip", "install", "torch", "torchaudio",
                 "--index-url", "https://download.pytorch.org/whl/cpu"],
                timeout=900,
            )

    # Step 2: 先装 ctranslate2（不限定版本），破掉 ctranslate2==4.4.0 死锁
    try:
        import ctranslate2  # noqa: F401
        print(f"[settings] ctranslate2 already installed")
    except ImportError:
        _try_pip(["ctranslate2"])

    # Step 3: 确保 faster-whisper 已安装（不限定版本，monkey-patch 处理兼容性）
    # whisperx 3.2.0 与 faster-whisper 1.0+ 的 TranscriptionOptions API 不兼容，
    # 但 0.10.3 没有 Python 3.14 的 wheel，所以用 monkey-patch 而非降级
    try:
        import faster_whisper  # noqa: F401
        from importlib.metadata import version
        fw_ver = version("faster-whisper")
        print(f"[settings] faster-whisper {fw_ver} (monkey-patch will ensure compat)")
    except ImportError:
        _try_pip(["faster-whisper"])

    # Step 4: 安装 whisperx（带 deps，faster-whisper 已锁定兼容版本）
    try:
        import whisperx  # noqa: F401
        print(f"[settings] whisperx {getattr(whisperx, '__version__', '?')} already installed")
    except ImportError:
        _try_pip(["whisperx"])

    # Step 5: 验证关键依赖链 + 锁定兼容版本
    # huggingface-hub>=1.0 把 use_auth_token 改为 token，pyannote.audio>=4.0 不兼容 whisperx 3.2.0
    import importlib
    _deps = [
        ("transformers", "transformers"),
        ("nltk", "nltk"),
        ("pyannote.audio", "pyannote-audio"),
    ]
    for mod_name, pip_name in _deps:
        try:
            importlib.import_module(mod_name)
        except ImportError:
            print(f"[settings] {mod_name} missing, installing {pip_name}...")
            _try_pip([pip_name])

    # 强制锁定 huggingface-hub 和 pyannote-audio 兼容版本（whisperx 3.2.0 需要旧 API）
    _ensure_compat_versions(python_exe)

    import whisperx
    print(f"[settings] whisperx {getattr(whisperx, '__version__', '?')} ready")


def _ensure_compat_versions(python_exe: str):
    """强制安装 pyannote-audio + huggingface-hub 兼容版本。

    whisperx 3.2.0 使用 use_auth_token 参数，whisperx 的 pyproject.toml 未锁定上限，
    导致 pip 可能安装 pyannote-audio>=4.0 和 huggingface-hub>=1.0，两者 API 均不兼容。
    """
    import subprocess

    sources = [
        ("默认源", []),
        ("清华镜像", ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                      "--trusted-host", "pypi.tuna.tsinghua.edu.cn"]),
        ("阿里云镜像", ["-i", "https://mirrors.aliyun.com/pypi/simple/",
                       "--trusted-host", "mirrors.aliyun.com"]),
    ]

    for name, extra_args in sources:
        cmd = [python_exe, "-m", "pip", "install"]
        cmd.extend(extra_args)
        cmd.extend(["pyannote-audio>=3.1,<4.0", "huggingface-hub>=0.20,<1.0"])
        print(f"[settings] Ensuring compat versions via {name}...")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                print(f"[settings] Compat versions OK via {name}")
                return
            print(f"[settings] pip {name} returned {r.returncode}: {r.stderr[-300:]}")
        except Exception as e:
            print(f"[settings] pip {name} failed: {e}")
            continue

    print("[settings] WARNING: Could not pin compat versions, relying on monkey-patch")


def _download_whisperx_model(size: str):
    """后台任务：自动安装 whisperx 库 + 下载 faster-whisper 模型。

    分两步：
    1. 检测/安装 whisperx 库（pip install）
    2. 触发模型下载（whisperx.load_model 首次自动拉取）
    """
    import subprocess, sys

    # ── Step 1: 安装 whisperx 库 ──
    try:
        import whisperx  # noqa: F401
        print("[settings] whisperx already installed")
    except ImportError:
        _download_state.update(
            current="whisperx 库",
            message="正在安装 WhisperX 库（首次约 2-5 分钟，含 torch/torchaudio）...",
            done=0,
        )
        print("[settings] Installing whisperx...")
        try:
            _install_whisperx(sys.executable)
            print("[settings] whisperx installed successfully")
        except Exception as e:
            _download_state.update(
                status="error",
                message=f"WhisperX 库安装失败: {e}",
            )
            return

    # ── Step 2: 确保依赖版本兼容 ──
    _download_state.update(
        current="依赖兼容",
        message="正在检查 pyannote-audio / huggingface-hub 版本兼容性...",
        total=2, done=1,
    )
    _ensure_compat_versions(sys.executable)

    # ── Step 3: 下载模型 ──
    _download_state.update(
        current=f"faster-whisper-{size}",
        message=f"正在下载 WhisperX {size} 模型（首次约 3-5 分钟，共约 3GB）...",
        total=2, done=1,
    )

    # Monkey-patch faster-whisper 1.0+ TranscriptionOptions 以兼容 whisperx 3.2.0
    # Python 3.14 只能用 whisperx 3.2.0，但它不传 multilingual/hotwords
    import faster_whisper.transcribe as _fwt
    _orig_init = _fwt.TranscriptionOptions.__init__
    def _patched_init(self, *args, **kwargs):
        kwargs.setdefault("multilingual", True)
        kwargs.setdefault("hotwords", None)
        return _orig_init(self, *args, **kwargs)
    _fwt.TranscriptionOptions.__init__ = _patched_init
    print("[settings] Patched faster-whisper TranscriptionOptions for whisperx 3.2.0 compat")

    # Monkey-patch pyannote.audio.Inference 自适应不同版本的参数名
    # whisperx 3.2.0 传 use_auth_token；pyannote 3.1- 接受 use_auth_token；
    # pyannote 3.2+ 改名 token；都不接受时通过 HF_TOKEN 环境变量传递
    try:
        from pyannote.audio import Inference
        import inspect as _inspect
        _orig_inf_init = Inference.__init__
        _inf_params = set(_inspect.signature(_orig_inf_init).parameters.keys())
        print(f"[settings] pyannote.audio.Inference params: {sorted(_inf_params)}")
        def _patched_inf_init(self, *args, **kwargs):
            if 'use_auth_token' in kwargs:
                token_val = kwargs.pop('use_auth_token')
                if 'token' in _inf_params:
                    kwargs['token'] = token_val
                elif 'use_auth_token' in _inf_params:
                    kwargs['use_auth_token'] = token_val
                elif token_val:
                    os.environ.setdefault('HF_TOKEN', token_val)
            return _orig_inf_init(self, *args, **kwargs)
        Inference.__init__ = _patched_inf_init
        print("[settings] Patched pyannote.audio.Inference (adaptive)")
    except ImportError:
        pass  # pyannote not yet installed, will be handled by install step

    # 确保 transformers 已安装（whisperx.load_model 内部依赖它）
    try:
        import transformers  # noqa: F401
    except ImportError:
        print("[settings] transformers missing, installing...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "transformers"],
            timeout=300,
        )

    old_offline = os.environ.get("HF_HUB_OFFLINE", None)
    old_endpoint = os.environ.get("HF_ENDPOINT", "")
    # 显式设为 0，覆盖 start.bat 中 HF_HUB_OFFLINE=1 的离线限制
    os.environ["HF_HUB_OFFLINE"] = "0"
    if not old_endpoint:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    try:
        import whisperx
        import torch
        device = "cpu"
        compute_type = "int8"
        if torch.cuda.is_available():
            device = "cuda"
            compute_type = "float16"
        print(f"[settings] Downloading faster-whisper-{size} (device={device}, mirror={os.environ.get('HF_ENDPOINT')})...")
        whisperx.load_model(size, device=device, compute_type=compute_type)
        print(f"[settings] WhisperX {size} model ready")

        # 预下载中文对齐模型（绕过 hf-mirror，镜像可能未缓存）
        _save_ep = os.environ.pop("HF_ENDPOINT", None)
        try:
            print("[settings] Pre-downloading alignment model for zh...")
            whisperx.load_align_model(language_code="zh", device=device)
            print("[settings] Alignment model for zh ready")
        except Exception as e:
            print(f"[settings] Alignment model pre-download failed (non-fatal): {e}")
        finally:
            if _save_ep:
                os.environ["HF_ENDPOINT"] = _save_ep

        _download_state.update(
            status="done", message=f"WhisperX {size} 就绪，可以上传音频了", done=2, total=2
        )
    except Exception as e:
        _download_state.update(
            status="error", message=f"模型下载失败: {e}"
        )
    finally:
        if old_offline is not None:
            os.environ["HF_HUB_OFFLINE"] = old_offline
        else:
            os.environ.pop("HF_HUB_OFFLINE", None)
        if not old_endpoint:
            os.environ.pop("HF_ENDPOINT", None)


def _download_funasr_models():
    """后台下载所有 FunASR 模型（paraformer + VAD + CAM++）。"""
    model_ids = [
        "iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn",
        "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "iic/speech_campplus_sv_zh-cn_16k-common",
    ]
    total = len(model_ids)
    done = 0

    # 临时取消离线模式以允许下载
    old_offline = os.environ.pop("MODELSCOPE_OFFLINE", None)
    try:
        from funasr import AutoModel
        import torch
        _device = "cuda" if torch.cuda.is_available() else "cpu"

        for i, model_id in enumerate(model_ids):
            _download_state.update(
                current=model_id,
                message=f"正在下载 FunASR 模型 ({i+1}/{total})...",
                done=i,
            )
            print(f"[settings] Downloading {model_id} via FunASR AutoModel (device={_device})...")
            AutoModel(model=model_id, disable_update=True, device=_device)
            print(f"[settings] Downloaded {model_id}")

        _download_state.update(
            status="done", message="FunASR 全部模型下载完成", done=total
        )
    except Exception as e:
        _download_state.update(
            status="error", message=f"下载失败: {e}"
        )
    finally:
        if old_offline is not None:
            os.environ["MODELSCOPE_OFFLINE"] = old_offline
