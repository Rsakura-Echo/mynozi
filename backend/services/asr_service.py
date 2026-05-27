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

                # 应用兼容补丁（必须在 import whisperx 之前，torchaudio/pyannote 导入时需要）
                from services.compat_patches import apply_all as _apply_compat
                _apply_compat()
                print("[asr] Applied whisperx 3.2.0 compatibility patches")

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
                _model_size = "large-v3"
                if _settings_file.exists():
                    try:
                        _data = json.loads(_settings_file.read_text(encoding="utf-8"))
                        _model_size = _data.get("whisper_model_size", "large-v3")
                    except Exception:
                        pass

                device = settings.asr_device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"

                compute_type = "float16" if device == "cuda" else "int8"
                print(f"[asr] Loading WhisperX model={_model_size} device={device} compute_type={compute_type}")

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
                # 确保使用 hf-mirror 下载对齐模型（国内必须）
                _ep_saved = os.environ.get("HF_ENDPOINT", "")
                if not _ep_saved:
                    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                try:
                    model_a, metadata = whisperx.load_align_model(language_code=lang, device=device)
                    aligned = whisperx.align(
                        transcribe_result["segments"], model_a, metadata, audio_str, device
                    )
                    print("[asr] Word-level alignment complete")
                except Exception as e:
                    # 对齐模型下载失败（镜像缓存可能缺失）→ 降级走切分模式
                    print(f"[asr] Alignment failed, will split by speaker turns: {e}")
                    aligned = transcribe_result
                finally:
                    if not _ep_saved:
                        os.environ.pop("HF_ENDPOINT", None)

                # ── Stage 5: pyannote 说话人分离 ──
                _update_progress(project_id, "说话人分离...", 60)
                # 优先从 settings.json 读取（前端可配置），回退到 .env
                import json
                _settings = {}
                if _settings_file.exists():
                    try:
                        _settings = json.loads(_settings_file.read_text("utf-8"))
                    except Exception:
                        pass
                hf_token = _settings.get("hf_token") or settings.hf_token
                if hf_token:
                    try:
                        print("[asr] Running pyannote speaker diarization...")
                        # 使用 3.1 模型（比默认 community-1 效果好很多）
                        diarize_model = whisperx.DiarizationPipeline(
                            model_name="pyannote/speaker-diarization-3.1",
                            token=hf_token,
                            device=device,
                        )
                        # 聚类参数：threshold 越低越容易分出独立说话人
                        # 官方默认 0.7045 → 我们设 0.35 适应多人对话场景
                        # 注意：instantiate() 返回新 pipeline 对象，必须赋值回去！
                        diarize_model.model = diarize_model.model.instantiate({
                            "clustering": {
                                "method": "centroid",
                                "min_cluster_size": settings.pyannote_min_cluster_size,
                                "threshold": settings.pyannote_clustering_threshold,
                            },
                        })
                        diarize_segments = diarize_model(
                            audio_str,
                            min_speakers=2,
                            max_speakers=8,
                        )
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
                        # 检测对齐是否成功：有词级数据才能做词级说话人分配
                        _segs = aligned.get("segments", [])
                        _has_words = any(
                            w for s in _segs
                            for w in s.get("words", [])
                        )
                        if _has_words:
                            aligned = whisperx.assign_word_speakers(diarize_segments, aligned)
                            print("[asr] Word-level speaker assignment complete")
                        else:
                            # 对齐模型不可用 → 用 pyannote 边界切分 whisperx 段落
                            aligned = _split_segments_by_diarization(diarize_segments, aligned)
                            print(f"[asr] Split by {len(diarize_segments)} diarization turns ({len(diarize_speakers)} speakers)")
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
# 用 pyannote 说话人边界切分 whisperx 段落（对齐模型不可用时）
# ═══════════════════════════════════════════════════════


def _split_segments_by_diarization(diarize_segments, result: dict) -> dict:
    """用 pyannote 说话人边界切分 whisperx 段落。

    对齐模型不可用时：pyannote 返回精确的说话人切换时间点（~1-2s 粒度），
    用这些边界来切分 whisperx 的大段落（~5-15s），产生 utterance 级句子。

    文字按每个子段占原段的时间比例截取。
    """
    segments = result.get("segments", [])
    new_segments: list[dict] = []

    for seg in segments:
        seg_start = float(seg.get("start", 0))
        seg_end = float(seg.get("end", 0))
        seg_text = (seg.get("text") or "").strip()
        seg_dur = seg_end - seg_start

        if seg_dur <= 0 or not seg_text:
            continue

        # 找到所有与此段落重叠的 pyannote 说话人段
        overlaps: list[dict] = []
        for _, row in diarize_segments.iterrows():
            d_start = float(row.get("start", 0))
            d_end = float(row.get("end", 0))
            ov_start = max(seg_start, d_start)
            ov_end = min(seg_end, d_end)
            if ov_end > ov_start:
                overlaps.append({
                    "speaker": str(row.get("speaker", "SPEAKER_00")),
                    "start": ov_start,
                    "end": ov_end,
                })

        if not overlaps:
            # 无重叠（理论上不会发生），保留原段
            seg["speaker"] = "SPEAKER_00"
            new_segments.append(seg)
            continue

        overlaps.sort(key=lambda x: x["start"])

        # 按时间比例分配文字给每个子段
        total_overlap_dur = sum(o["end"] - o["start"] for o in overlaps)
        char_pos = 0
        for ov in overlaps:
            ov_dur = ov["end"] - ov["start"]
            ratio = ov_dur / max(total_overlap_dur, 0.01)
            char_count = max(2, round(len(seg_text) * ratio))
            # 最后一个子段吃掉剩余文字
            if ov is overlaps[-1]:
                char_count = len(seg_text) - char_pos
            sub_text = seg_text[char_pos:char_pos + char_count].strip()
            char_pos += char_count

            if sub_text:
                new_segments.append({
                    "speaker": ov["speaker"],
                    "text": sub_text,
                    "start": ov["start"],
                    "end": ov["end"],
                })

    result["segments"] = new_segments
    return result


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
