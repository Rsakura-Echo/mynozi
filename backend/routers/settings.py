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




def check_models_cached(asr_model: str) -> tuple[bool, str]:
    """检查 WhisperX 模型是否已缓存。

    Returns:
        (is_cached, error_message)
    """
    data = _load()
    size = data.get("whisper_model_size", "medium")
    info = ASR_MODELS.get(size)
    if not info:
        return False, f"未知 Whisper 模型大小: {size}"
    status = _check_hf_model_cached(info["repo"])
    if not status["downloaded"]:
        msg = (
            f"WhisperX {size} 模型尚未下载，请在右上角设置中点击「下载模型」按钮。\n"
            f"模型大小约 {info['size_gb']} GB，首次下载需要几分钟。"
        )
        return False, msg
    try:
        import whisperx  # noqa: F401
    except ImportError:
        return False, "WhisperX 库未安装。请在设置中点击「下载模型」自动安装。"
    return True, ""


@router.get("/models")
async def get_model_status():
    """返回 WhisperX 模型缓存状态。"""
    models = []
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
    else:
        raise HTTPException(400, detail=f"未知引擎: {body.engine}")

    return _download_state


def _install_whisperx(python_exe: str):
    """安装 whisperx + 依赖，锁定兼容版本。

    whisperx 3.2.0 与新版 pyannote-audio (>=4.0) 和 huggingface-hub (>=1.0) 不兼容。
    关键顺序：huggingface-hub 版本锁定必须放在 faster-whisper 之前，
    否则后者会拉入不兼容的新版 hub，导致 is_offline_mode 等 API 缺失。
    """
    import subprocess, importlib

    PIP_INDEX = ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                 "--trusted-host", "pypi.tuna.tsinghua.edu.cn"]

    def _pip(packages: list[str], upgrade: bool = True, no_deps: bool = False) -> None:
        cmd = [python_exe, "-m", "pip", "install"]
        if upgrade:
            cmd.append("--upgrade")
        cmd.extend(PIP_INDEX)
        cmd.extend(packages)
        if no_deps:
            cmd.append("--no-deps")
        print(f"[settings] pip {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            err = r.stderr.strip().splitlines()[-5:] if r.stderr else []
            raise RuntimeError(f"pip install failed:\n" + "\n".join(err))

    # 1. PyTorch
    try:
        import torch  # noqa: F401
        print("[settings] torch already installed")
    except ImportError:
        print("[settings] Installing torch + torchaudio...")
        try:
            _pip(["torch", "torchaudio"])
        except RuntimeError:
            subprocess.check_call(
                [python_exe, "-m", "pip", "install", "torch", "torchaudio",
                 "--index-url", "https://download.pytorch.org/whl/cpu"], timeout=900)

    # 2. huggingface-hub 版本锁定（必须在其他依赖之前）
    # whisperx 3.2.0 需要 is_offline_mode，只在 hub>=0.20,<1.0 中存在
    # 直接 pip install --upgrade 从 1.x 降级到 0.x 时可能不生效，先卸载再安装
    print("[settings] Ensuring compatible huggingface-hub (>=0.20, <1.0)...")
    subprocess.run(
        [python_exe, "-m", "pip", "uninstall", "huggingface-hub", "-y"],
        capture_output=True, timeout=60,
    )
    _pip(["huggingface-hub>=0.20,<1.0"], upgrade=False)
    # 验证 is_offline_mode 可导入
    import huggingface_hub as _hub
    assert hasattr(_hub, "is_offline_mode"), \
        f"huggingface-hub {_hub.__version__} 缺少 is_offline_mode，请手动运行: pip install 'huggingface-hub>=0.20,<1.0'"
    print(f"[settings] huggingface-hub {_hub.__version__} ready")

    # 3. ctranslate2（破掉 whisperx 对 ctranslate2==4.4.0 的死锁）
    try:
        import ctranslate2  # noqa: F401
        print("[settings] ctranslate2 already installed")
    except ImportError:
        _pip(["ctranslate2"], upgrade=False)

    # 4. faster-whisper（版本不限，compat_patches 处理 API 兼容）
    try:
        import faster_whisper  # noqa: F401
        print(f"[settings] faster-whisper already installed")
    except ImportError:
        _pip(["faster-whisper"], upgrade=False)

    # 5. whisperx --no-deps（绕过 ctranslate2==4.4.0 死锁，依赖已在前几步安装）
    try:
        import whisperx  # noqa: F401
        print(f"[settings] whisperx already installed")
    except ImportError:
        _pip(["whisperx"], upgrade=False, no_deps=True)

    # 6. 补装 whisperx 运行时依赖（--no-deps 跳过的依赖在此覆盖）
    for mod_name, pip_name in [
        ("transformers", "transformers"),
        ("nltk", "nltk"),
        ("pandas", "pandas"),
        ("librosa", "librosa"),
        ("pyannote.audio", "pyannote-audio>=3.1,<4.0"),
    ]:
        try:
            importlib.import_module(mod_name)
        except ImportError:
            print(f"[settings] {mod_name} missing, installing...")
            _pip([pip_name], upgrade=False)

    import whisperx
    print(f"[settings] whisperx ready")


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

    # ── Step 2: 下载模型 ──
    _download_state.update(
        current=f"faster-whisper-{size}",
        message=f"正在下载 WhisperX {size} 模型（首次约 3-5 分钟，共约 3GB）...",
        total=2, done=1,
    )

    # 应用 whisperx 3.2.0 兼容补丁（集中管理，与 asr_service.py 共享）
    from services.compat_patches import apply_all as _apply_compat
    _apply_compat()
    print("[settings] Applied whisperx 3.2.0 compatibility patches")

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
