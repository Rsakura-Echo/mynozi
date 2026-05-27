"""whisperx 3.2.0 + Python 3.14 兼容补丁层。

Python 3.14 强制使用新版 torch/torchaudio/huggingface-hub，
但 whisperx 3.2.0（唯一提供 Python 3.14 wheel 的版本）依赖这些包的旧 API。
本模块提供完整兼容层，所有补丁自然幂等（检测缺失才打），可安全多次调用。

用法:
    from services.compat_patches import apply_all
    apply_all()  # 可在任何时机调用，多次安全
"""

import os
import inspect as _inspect


def _patch_huggingface_hub():
    """huggingface-hub >=0.27: is_offline_mode 被移除。

    whisperx 3.2.0 代码中 import is_offline_mode 检查离线状态，
    新版 hub 已删除该导出。从 HF_HUB_OFFLINE 环境变量判断。
    """
    try:
        import huggingface_hub
    except ImportError:
        return
    if hasattr(huggingface_hub, 'is_offline_mode'):
        return
    huggingface_hub.is_offline_mode = lambda: os.environ.get("HF_HUB_OFFLINE", "0") == "1"
    print("[compat] Patched huggingface_hub.is_offline_mode")


def _patch_torchaudio():
    """torchaudio >=2.5 移除多个 pyannote.audio 3.x 依赖的 API。

    - list_audio_backends() → 后端始终可用，返回虚拟列表
    - AudioMetaData → 改名为 AudioMetadata
    """
    try:
        import torchaudio
    except ImportError:
        return

    if not hasattr(torchaudio, 'list_audio_backends'):
        torchaudio.list_audio_backends = lambda: ['ffmpeg', 'sox', 'soundfile']
        print("[compat] Patched torchaudio.list_audio_backends")

    if not hasattr(torchaudio, 'AudioMetaData'):
        if hasattr(torchaudio, 'AudioMetadata'):
            torchaudio.AudioMetaData = torchaudio.AudioMetadata
            print("[compat] Aliased torchaudio.AudioMetaData -> AudioMetadata")
        else:
            from collections import namedtuple
            _AMD = namedtuple('AudioMetaData',
                ['sample_rate', 'num_frames', 'num_channels', 'bits_per_sample', 'encoding'])
            torchaudio.AudioMetaData = _AMD
            print("[compat] Created placeholder torchaudio.AudioMetaData")


def _patch_torch_load():
    """PyTorch >=2.6: torch.load 默认 weights_only 改为 True。

    pyannote.audio 等库用 torch.load 加载模型权重时不传 weights_only，
    新版 PyTorch 默认 True 导致 omegaconf/listconfig 等类型被拒绝。
    此补丁恢复 weights_only=False 的旧行为。
    """
    try:
        import torch
    except ImportError:
        return
    _orig_load = torch.load

    def _load(*args, **kwargs):
        kwargs.setdefault('weights_only', False)
        return _orig_load(*args, **kwargs)

    torch.load = _load
    print("[compat] Patched torch.load (weights_only default -> False)")


def _patch_transcription_options():
    """faster-whisper >=1.0: TranscriptionOptions 新增 multilingual / hotwords 参数。

    whisperx 3.2.0 未传递这两个必填参数，导致 TypeError。
    """
    try:
        import faster_whisper.transcribe as _fwt
    except ImportError:
        return

    _orig = _fwt.TranscriptionOptions.__init__

    def _patched(self, *args, **kwargs):
        kwargs.setdefault("multilingual", True)
        kwargs.setdefault("hotwords", None)
        return _orig(self, *args, **kwargs)

    _fwt.TranscriptionOptions.__init__ = _patched
    print("[compat] Patched faster_whisper.TranscriptionOptions")


def _patch_pyannote_inference():
    """pyannote.audio / huggingface-hub: use_auth_token -> token 迁移。

    whisperx 3.2.0 用 use_auth_token= 调 Inference/Pipeline.from_pretrained，
    不同 pyannote 版本参数名为 use_auth_token 或 token。
    用 inspect.signature 探测真实参数名，自适应选择。
    """
    try:
        from pyannote.audio import Inference
    except ImportError:
        return

    _orig = Inference.__init__
    _sig_params = set(_inspect.signature(_orig).parameters.keys())

    def _patched(self, *args, **kwargs):
        if "use_auth_token" in kwargs:
            token_val = kwargs.pop("use_auth_token")
            if "token" in _sig_params:
                kwargs["token"] = token_val
            elif "use_auth_token" in _sig_params:
                kwargs["use_auth_token"] = token_val
            elif token_val:
                os.environ.setdefault("HF_TOKEN", token_val)
        return _orig(self, *args, **kwargs)

    Inference.__init__ = _patched
    print("[compat] Patched pyannote.audio.Inference (use_auth_token -> token)")


def apply_all():
    """应用全部兼容补丁。自然幂等（检测 API 是否存在才打），可多次调用。"""
    _patch_huggingface_hub()
    _patch_torch_load()              # PyTorch 2.6+ weights_only 默认值变更
    _patch_torchaudio()
    _patch_transcription_options()
    _patch_pyannote_inference()
