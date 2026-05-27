"""whisperx 3.2.0 兼容补丁 — Python 3.14 唯一的 whisperx 版本。

whisperx 3.2.0 是唯一提供 Python 3.14 wheel 的版本，但它依赖的
faster-whisper / pyannote.audio / huggingface-hub 均已升级 API，
导致 4 处不兼容。本模块集中处理这些补丁，确保 SETTINGS 和 ASR
两条代码路径使用完全相同的补丁逻辑。

用法:
    from services.compat_patches import apply_all
    apply_all()  # 幂等，可多次调用
"""

import os
import inspect as _inspect

_applied: bool = False


def _patch_torchaudio_backends():
    """torchaudio >=2.5 移除了 list_audio_backends()，pyannote.audio 3.x 导入时调用它。

    pyannote.audio 3.x 的 core/io.py 在模块级别执行
    torchaudio.list_audio_backends() 检查可用音频后端。
    新版 torchaudio 已删除该函数（后端始终可用），此补丁恢复它。
    必须在 pyannote.audio 导入之前调用。
    """
    try:
        import torchaudio
    except ImportError:
        return
    if not hasattr(torchaudio, 'list_audio_backends'):
        torchaudio.list_audio_backends = lambda: ['ffmpeg', 'sox', 'soundfile']
        print("[compat] Patched torchaudio.list_audio_backends")


def _patch_transcription_options():
    """faster-whisper >=1.0: TranscriptionOptions 新增 multilingual / hotwords 必填参数。

    whisperx 3.2.0 调用 TranscriptionOptions(...) 时未传递这两个参数，
    导致 TypeError。此补丁为缺失参数设置默认值。
    """
    import faster_whisper.transcribe as _fwt
    _orig = _fwt.TranscriptionOptions.__init__

    def _patched(self, *args, **kwargs):
        kwargs.setdefault("multilingual", True)
        kwargs.setdefault("hotwords", None)
        return _orig(self, *args, **kwargs)

    _fwt.TranscriptionOptions.__init__ = _patched


def _patch_pyannote_inference():
    """pyannote.audio / huggingface-hub: use_auth_token → token 参数名迁移。

    whisperx 3.2.0 调用 Inference(use_auth_token=...) 和
    Pipeline.from_pretrained(use_auth_token=...)。

    不同 pyannote 版本的参数名：
    - 3.0-3.1: use_auth_token
    - 3.2-3.x: token（use_auth_token 已移除）
    - 4.x: token（但 Inference API 有其它破坏性变更）

    此补丁用 inspect.signature 探测实际参数名，自适应选择正确名称。
    如果都不接受，回退到 HF_TOKEN 环境变量。
    """
    try:
        from pyannote.audio import Inference
    except ImportError:
        return  # pyannote 尚未安装，调用时再补丁

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


def apply_all():
    """应用全部兼容补丁。幂等，可安全多次调用。"""
    global _applied
    if _applied:
        return
    _patch_torchaudio_backends()       # 必须最先：pyannote 导入时需要
    _patch_transcription_options()
    _patch_pyannote_inference()
    _applied = True
