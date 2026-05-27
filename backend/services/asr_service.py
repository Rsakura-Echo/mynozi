"""ASR 服务 — WhisperX + pyannote：语音识别 + 词级时间戳 + 说话人分离。"""

import os
from pathlib import Path
from config import settings


def _speaker_label_to_name(label: str) -> str:
    mapping = {
        "SPEAKER_00": "说话人A", "SPEAKER_01": "说话人B",
        "SPEAKER_02": "说话人C", "SPEAKER_03": "说话人D",
        "SPEAKER_04": "说话人E", "SPEAKER_05": "说话人F",
    }
    return mapping.get(label, label)


async def process_audio_with_asr(project_id: str, file_path: str, file_hash: str = ""):
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _process_sync, project_id, file_path, file_hash)


def _update_progress(project_id: str, stage: str, pct: float):
    _progress_store[project_id] = {"stage": stage, "pct": pct}


_progress_store: dict = {}


def _process_sync(project_id: str, file_path: str, file_hash: str = ""):
    from database import async_session
    from models import Project, Speaker, Sentence
    from services.audio_service import extract_audio_from_video, extract_sentence_audio
    from sqlalchemy import select, update as sql_update
    import asyncio

    async def _update():
        async with async_session() as session:
            result = await session.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if not project:
                return

            try:
                # ── Stage 1: 提取音频 ──
                _update_progress(project_id, "提取音频...", 5)
                src_path = Path(file_path)
                audio_path = src_path

                video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}
                if src_path.suffix.lower() in video_exts:
                    audio_dest = settings.data_dir / "uploads" / project_id / f"{src_path.stem}_extracted.wav"
                    if extract_audio_from_video(str(src_path), str(audio_dest)):
                        audio_path = audio_dest
                        project.original_audio = str(audio_dest)
                    else:
                        project.status = "error"
                        project.last_error = "视频提取音频失败"
                        await session.commit()
                        return
                else:
                    project.original_audio = str(audio_path)

                audio_str = str(audio_path)

                # ── Stage 2: 加载模型 ──
                _update_progress(project_id, "加载模型...", 10)
                try:
                    import whisperx
                    import torch
                except ImportError:
                    project.status = "error"
                    project.last_error = "WhisperX 未安装"
                    await session.commit()
                    return

                import json
                _settings_file = settings.data_dir / "settings.json"
                _model_size = "medium"
                if _settings_file.exists():
                    try:
                        _data = json.loads(_settings_file.read_text(encoding="utf-8"))
                        _model_size = _data.get("whisper_model_size", "medium")
                    except Exception:
                        pass

                device = settings.asr_device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"

                compute_type = "float16" if device == "cuda" else "int8"
                print(f"[asr] Loading WhisperX model={_model_size} device={device} compute_type={compute_type}")

                # Monkey-patch faster-whisper 1.0+ TranscriptionOptions 兼容 whisperx 3.2.0
                import faster_whisper.transcribe as _fwt
                _orig_init = _fwt.TranscriptionOptions.__init__
                def _patched_init(self, *args, **kwargs):
                    kwargs.setdefault("multilingual", True)
                    kwargs.setdefault("hotwords", None)
                    return _orig_init(self, *args, **kwargs)
                _fwt.TranscriptionOptions.__init__ = _patched_init
                print("[asr] Patched faster-whisper TranscriptionOptions")

                # Monkey-patch pyannote.audio.Inference 自适应不同版本的参数名
                # whisperx 3.2.0 传 use_auth_token；pyannote 3.1- 接受 use_auth_token；
                # pyannote 3.2+ 改名 token；都不接受时通过 HF_TOKEN 环境变量传递
                try:
                    from pyannote.audio import Inference
                    import inspect as _inspect
                    _orig_inf_init = Inference.__init__
                    _inf_params = set(_inspect.signature(_orig_inf_init).parameters.keys())
                    print(f"[asr] pyannote.audio.Inference params: {sorted(_inf_params)}")
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
                    print("[asr] Patched pyannote.audio.Inference (adaptive)")
                except ImportError:
                    pass

                old_offline = os.environ.get("HF_HUB_OFFLINE", None)
                old_endpoint = os.environ.get("HF_ENDPOINT", "")
                os.environ["HF_HUB_OFFLINE"] = "0"
                if not old_endpoint:
                    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                try:
                    asr_model = whisperx.load_model(
                        _model_size, device=device, compute_type=compute_type
                    )
                finally:
                    if old_offline is not None:
                        os.environ["HF_HUB_OFFLINE"] = old_offline
                    else:
                        os.environ.pop("HF_HUB_OFFLINE", None)
                    if not old_endpoint:
                        os.environ.pop("HF_ENDPOINT", None)

                # ── Stage 3: ASR 转写 ──
                _update_progress(project_id, "ASR 语音识别...", 20)
                print("[asr] Transcribing...")
                transcribe_result = asr_model.transcribe(audio_str, batch_size=16)
                n_segments = len(transcribe_result.get("segments", []))
                print(f"[asr] Transcribed {n_segments} segments")

                # ── Stage 4: 词级时间戳对齐 ──
                _update_progress(project_id, "时间轴对齐...", 40)
                lang = transcribe_result.get("language", "zh")
                model_a, metadata = whisperx.load_align_model(language_code=lang, device=device)
                aligned = whisperx.align(
                    transcribe_result["segments"], model_a, metadata, audio_str, device
                )

                # ── Stage 5: pyannote 说话人分离 ──
                _update_progress(project_id, "说话人分离...", 60)
                hf_token = settings.hf_token
                if hf_token:
                    try:
                        print("[asr] Running pyannote speaker diarization...")
                        diarize_model = whisperx.DiarizationPipeline(
                            use_auth_token=hf_token, device=device
                        )
                        # 调优聚类参数：同性别说话人声音相似，默认 threshold=0.7045
                        # 会过度合并。降低 threshold + min_cluster_size 以区分更多说话人
                        # 注意：instantiate() 返回新 pipeline 对象，必须接住！
                        diarize_model.model = diarize_model.model.instantiate({
                            "clustering": {
                                "threshold": 0.50,          # 默认 0.7045，降低 = 更多聚类 = 更多说话人
                                "min_cluster_size": 5,      # 默认 12，降低 = 短台词说话人也能被识别
                            },
                        })
                        diarize_segments = diarize_model(audio_str)
                        # 日志：pyannote 识别到的说话人及时间分布
                        diarize_speakers = set()
                        speaker_time: dict[str, float] = {}
                        for _, row in diarize_segments.iterrows():
                            spk = row.get("speaker", "?")
                            diarize_speakers.add(spk)
                            dur = float(row.get("end", 0)) - float(row.get("start", 0))
                            speaker_time[spk] = speaker_time.get(spk, 0) + dur
                        print(f"[asr] Diarization: {len(diarize_segments)} segments, "
                              f"{len(diarize_speakers)} speakers: "
                              f"{ {k: f'{v:.1f}s' for k, v in speaker_time.items()} }")
                        aligned = whisperx.assign_word_speakers(diarize_segments, aligned)
                        print("[asr] Word-level speaker assignment complete")
                    except Exception as e:
                        print(f"[asr] Diarization failed (continuing without): {e}")
                else:
                    print("[asr] No HF token, skipping diarization")

                segments = aligned.get("segments", [])
                if not segments:
                    project.status = "error"
                    project.last_error = "ASR 识别结果为空"
                    await session.commit()
                    return

                # ── Stage 6: 按说话人轮次构建句子 ──
                _update_progress(project_id, "构建台词...", 80)
                sub_segments = _build_sentences(segments)

                if not sub_segments:
                    project.status = "error"
                    project.last_error = "未能提取有效句子"
                    await session.commit()
                    return

                print(f"[asr] Built {len(sub_segments)} sentences")
                for i, s in enumerate(sub_segments[:5]):
                    print(f"  [{i}] {s['speaker']} {s['start']:.1f}-{s['end']:.1f}s: {s['text'][:50]}...")

                # ── Stage 7: 写入数据库 ──
                _update_progress(project_id, "写入结果...", 90)

                speaker_sub_segs: dict[str, list] = {}
                for sub in sub_segments:
                    spk = sub["speaker"]
                    if spk not in speaker_sub_segs:
                        speaker_sub_segs[spk] = []
                    speaker_sub_segs[spk].append(sub)

                speaker_map = {}
                for spk_label in sorted(speaker_sub_segs.keys()):
                    speaker = Speaker(
                        project_id=project_id,
                        name=_speaker_label_to_name(spk_label),
                    )
                    session.add(speaker)
                    await session.flush()
                    speaker_map[spk_label] = speaker.id

                sort_order = 0
                for sub in sub_segments:
                    sentence = Sentence(
                        project_id=project_id,
                        speaker_id=speaker_map.get(sub["speaker"]),
                        text=sub["text"],
                        start_time=sub["start"],
                        end_time=sub["end"],
                        sort_order=sort_order,
                    )
                    session.add(sentence)
                    sort_order += 1

                # 为每个说话人提取参考音频
                for spk_label, speaker_id in speaker_map.items():
                    spk_segs = speaker_sub_segs[spk_label]
                    best = max(spk_segs, key=lambda s: s["end"] - s["start"])
                    ref_dir = settings.data_dir / "references" / project_id
                    ref_dir.mkdir(parents=True, exist_ok=True)
                    ref_path = ref_dir / f"{speaker_id}.wav"
                    extract_sentence_audio(str(audio_path), best["start"], best["end"], str(ref_path))

                    await session.execute(
                        sql_update(Speaker)
                        .where(Speaker.id == speaker_id)
                        .values(reference_audio=str(ref_path))
                    )

                project.duration = segments[-1].get("end", 0)
                project.status = "ready"
                if file_hash:
                    project.file_hash = file_hash
                await session.commit()

                _update_progress(project_id, "完成", 100)
                print(f"[asr] Done: {len(sub_segments)} sentences, {len(speaker_map)} speakers")

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(tb)
                print(f"[asr] Processing error: {e}")
                project.status = "error"
                project.last_error = f"{type(e).__name__}: {e}"
                await session.commit()

    asyncio.run(_update())


# ═══════════════════════════════════════════════════════
# 句子构建
# ═══════════════════════════════════════════════════════

MAX_SENTENCE_DURATION = 8.0   # 超过此值切分（对话句子不应超过 8s）
MERGE_MAX_GAP = 0.5          # 合并时两句间隔不能超过 0.5s


def _build_sentences(segments: list[dict]) -> list[dict]:
    """从 WhisperX segments（带词级说话人标注）构建最终句子列表。

    1. 按词级说话人变化切分（说话人切换 = 新句子）
    2. 合并相邻同说话人的短句子
    3. 切分过长的同说话人句子
    """
    raw: list[dict] = []

    for seg in segments:
        words = seg.get("words", [])
        has_word_speakers = words and any(w.get("speaker") for w in words)

        if not has_word_speakers:
            # 无说话人分离数据，整段作为一句
            spk = seg.get("speaker", "SPEAKER_00")
            raw.append({
                "speaker": spk,
                "text": (seg.get("text") or "").strip(),
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
            })
            continue

        # 按词级说话人变化切分
        cur_spk = None
        cur_words: list[dict] = []

        for w in words:
            spk = w.get("speaker", "SPEAKER_00")

            if spk != cur_spk and cur_words:
                # 说话人切换 → 提交当前句子（不依赖 word_text，避免标点词跳过检测）
                _commit_words(raw, cur_spk, cur_words)
                cur_words = []

            cur_spk = spk
            cur_words.append(w)

        if cur_words:
            _commit_words(raw, cur_spk, cur_words)

    # 合并相邻同说话人短句
    raw = _merge_adjacent_same_speaker(raw)

    # 切分过长句子
    result = []
    for s in raw:
        dur = s["end"] - s["start"]
        if dur > MAX_SENTENCE_DURATION:
            result.extend(_split_long(s))
        else:
            result.append(s)

    return result


def _commit_words(result: list[dict], speaker: str, words: list[dict]):
    """将一组词提交为一个句子。"""
    if not words:
        return
    text = "".join(w.get("word", "") for w in words).strip()
    if not text:
        return
    result.append({
        "speaker": speaker,
        "text": text,
        "start": words[0].get("start", 0),
        "end": words[-1].get("end", 0),
    })


def _merge_adjacent_same_speaker(sentences: list[dict]) -> list[dict]:
    """合并相邻同说话人的待合并句子。

    合并条件：
    - 同一说话人
    - 两句间隔 ≤ MERGE_MAX_GAP
    - 合并后总时长 ≤ MAX_SENTENCE_DURATION
    """
    if not sentences:
        return sentences

    merged = []
    for s in sentences:
        if not merged:
            merged.append(s)
            continue

        prev = merged[-1]
        prev_dur = prev["end"] - prev["start"]
        cur_dur = s["end"] - s["start"]
        gap = s["start"] - prev["end"]

        if (
            s["speaker"] == prev["speaker"]
            and gap <= MERGE_MAX_GAP
            and prev_dur + cur_dur <= MAX_SENTENCE_DURATION
        ):
            prev["text"] += s["text"]
            prev["end"] = s["end"]
        else:
            merged.append(s)

    return merged


def _split_long(seg: dict) -> list[dict]:
    """将过长句子在逗号/停顿标点处切分。"""
    import math

    text = seg["text"]
    dur = seg["end"] - seg["start"]

    PAUSE_PUNCT = set("，,；;：:、")
    positions = [i for i, ch in enumerate(text) if ch in PAUSE_PUNCT]

    if len(positions) < 1:
        # 无标点，按时间均分
        parts = max(1, math.ceil(dur / MAX_SENTENCE_DURATION))
        chars_per = math.ceil(len(text) / parts)
        time_per = dur / parts
        result = []
        for i in range(parts):
            chunk = text[i * chars_per:(i + 1) * chars_per].strip()
            if len(chunk) >= 2:
                result.append({
                    "text": chunk,
                    "start": seg["start"] + i * time_per,
                    "end": seg["start"] + (i + 1) * time_per,
                    "speaker": seg["speaker"],
                })
        return result if result else [seg]

    ratio = dur / max(len(text), 1)
    result = []
    prev = 0
    for pos in positions:
        chunk = text[prev:pos + 1].strip("，,；;：:、")
        if len(chunk) >= 2:
            result.append({
                "text": chunk,
                "start": seg["start"] + prev * ratio,
                "end": seg["start"] + (pos + 1) * ratio,
                "speaker": seg["speaker"],
            })
        prev = pos + 1

    if prev < len(text):
        chunk = text[prev:].strip()
        if len(chunk) >= 2:
            result.append({
                "text": chunk,
                "start": seg["start"] + prev * ratio,
                "end": seg["end"],
                "speaker": seg["speaker"],
            })

    return result if result else [seg]
