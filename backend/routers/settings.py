"""ASR 模型设置 API。"""

import json
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
    "asr_model_desc": "多语言通用模型，中文识别好，支持说话人分离",
    "whisper_model_size": "medium",
    "runninghub_api_key": "",
    "runninghub_workflow_id": "",
    "available_models": [
        {
            "value": "whisperx",
            "label": "WhisperX (OpenAI)",
            "desc": "多语言通用模型，中文识别好，支持说话人分离。首次下载 ~3GB，适合大多数场景。"
        },
        {
            "value": "funasr",
            "label": "FunASR (阿里达摩院)",
            "desc": "中文识别 SOTA，口音/方言/噪声环境表现更优。需额外安装 funasr 包，模型 ~2GB。"
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
}


def _check_ms_model_cached(dir_name: str) -> dict:
    """检测 ModelScope 模型是否已缓存（以 model.pt 存在为完成标志）。"""
    model_dir = MS_CACHE / dir_name
    downloaded = (model_dir / "model.pt").exists() or (model_dir / "pytorch_model.bin").exists()
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
    current = _load().get("whisper_model_size", settings.whisper_model)
    return {"models": models, "current_model": current}


def _load() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            # 确保新字段有默认值
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
            status="downloading", message=f"正在下载 WhisperX {size} 模型...",
            current="", total=1, done=0
        )
        threading.Thread(target=_download_whisperx_model, args=(size,), daemon=True).start()
    elif body.engine == "funasr":
        _download_state.update(
            status="downloading", message="正在下载 FunASR 模型（约 2GB，从 ModelScope 国内源）...",
            current="", total=2, done=0
        )
        threading.Thread(target=_download_funasr_models, daemon=True).start()
    else:
        raise HTTPException(400, detail=f"未知引擎: {body.engine}")

    return _download_state


def _download_whisperx_model(size: str):
    """后台下载 WhisperX / faster-whisper 模型。"""
    try:
        _download_state["current"] = f"faster-whisper-{size}"
        import whisperx
        import torch
        device = "cpu"
        compute_type = "int8"
        if torch.cuda.is_available():
            device = "cuda"
            compute_type = "float16"
        _download_state["message"] = f"正在加载 WhisperX {size} 模型（首次自动下载）..."
        whisperx.load_model(size, device=device, compute_type=compute_type)
        _download_state.update(
            status="done", message=f"WhisperX {size} 模型下载完成", done=1
        )
    except Exception as e:
        _download_state.update(
            status="error", message=f"下载失败: {e}"
        )


def _download_funasr_models():
    """后台下载 FunASR paraformer 模型（使用 funasr.AutoModel 从 ModelScope 国内源拉取）。"""
    model_id = "iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn"
    try:
        _download_state["current"] = model_id
        _download_state["message"] = "正在下载 FunASR 模型（约 1GB，从 ModelScope 国内源）..."
        print(f"[settings] Downloading {model_id} via FunASR AutoModel...")

        # 临时取消离线模式以允许下载
        old_offline = os.environ.pop("MODELSCOPE_OFFLINE", None)
        try:
            from funasr import AutoModel
            AutoModel(model=model_id, disable_update=True, device="cpu")
        finally:
            if old_offline is not None:
                os.environ["MODELSCOPE_OFFLINE"] = old_offline

        print(f"[settings] Downloaded {model_id}")
        _download_state.update(
            status="done", message="FunASR 模型下载完成", done=1
        )
    except Exception as e:
        _download_state.update(
            status="error", message=f"下载失败: {e}"
        )
