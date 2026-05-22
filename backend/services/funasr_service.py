"""FunASR 服务 — 使用模型内置的 VAD + 标点 + 说话人分离能力。

模型 iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn 自带完整的
VAD → ASR → 标点恢复 → 说话人分离流水线。sentence_info 字段直接提供
每句话的文本、时间戳、说话人 ID，无需手动切分。
"""

import os
import math
from pathlib import Path
from config import settings

os.environ.setdefault("MODELSCOPE_OFFLINE", "1")

# ── 后处理参数 ──
MAX_SENTENCE_DURATION = 15.0   # 超过此值尝试在逗号处切分
MIN_SENTENCE_DURATION = 0.3    # 短于此值合并到相邻句
MIN_SENTENCE_CHARS = 2         # 少于此字符数视为无效

SENTENCE_PAUSE_PUNCT = set("，,；;：:")


def _speaker_label_to_name(label: str) -> str:
    mapping = {
        "SPEAKER_00": "说话人A", "SPEAKER_01": "说话人B",
        "SPEAKER_02": "说话人C", "SPEAKER_03": "说话人D",
        "SPEAKER_04": "说话人E", "SPEAKER_05": "说话人F",
    }
    return mapping.get(label, label)


async def process_audio_with_funasr(project_id: str, file_path: str, file_hash: str = ""):
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

                # ── Stage 2: 检测设备 ──
                import torch
                device = settings.asr_device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                print(f"[funasr] Using device: {device}")

                # ── Stage 3: 加载模型 ──
                _update_progress(project_id, "加载 ASR 模型...", 10)
                from funasr import AutoModel

                asr_model = AutoModel(
                    model="iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn",
                    disable_update=True,
                    device=device,
                )

                # ── Stage 4: ASR + VAD + 标点 + 说话人分离（模型一次性完成）──
                _update_progress(project_id, "ASR 语音识别 + 说话人分离...", 20)
                print(f"[funasr] Transcribing with built-in VAD/punc/spk...")
                asr_result = asr_model.generate(input=audio_str, batch_size_s=300)

                if not asr_result or not asr_result[0].get("text"):
                    project.status = "error"
                    project.last_error = "ASR 识别结果为空"
                    await session.commit()
                    return

                result_data = asr_result[0]
                sentence_info = result_data.get("sentence_info", [])

                print(f"[funasr] Model output keys: {list(result_data.keys())}")
                print(f"[funasr] sentence_info entries: {len(sentence_info)}")

                if not sentence_info:
                    # 兼容：旧版模型可能没有 sentence_info，回退到手动切分
                    print("[funasr] No sentence_info, falling back to text/timestamp parsing")
                    sentence_info = _fallback_split(result_data)
                    print(f"[funasr] Fallback produced {len(sentence_info)} sentences")

                # ── Stage 5: 解析 + 后处理 ──
                _update_progress(project_id, "后处理...", 70)

                # 将模型输出转换为统一格式
                segments = []
                for si in sentence_info:
                    text = (si.get("text") or "").strip()
                    if not text or len(text) < MIN_SENTENCE_CHARS:
                        continue
                    start_s = si.get("start", 0)
                    end_s = si.get("end", 0)
                    if end_s - start_s < MIN_SENTENCE_DURATION:
                        continue

                    spk_id = si.get("spk", 0)
                    if isinstance(spk_id, (int, float)):
                        spk_id = int(spk_id)

                    segments.append({
                        "text": text,
                        "start": float(start_s),
                        "end": float(end_s),
                        "speaker": f"SPEAKER_{spk_id:02d}",
                    })

                if not segments:
                    project.status = "error"
                    project.last_error = "未能提取有效句子"
                    await session.commit()
                    return

                # 后处理：合并相邻同说话人短句 + 切分过长句子
                segments = _post_process(segments)

                print(f"[funasr] Final segments: {len(segments)}")
                for i, seg in enumerate(segments[:5]):
                    print(f"  [{i}] {seg['speaker']} {seg['start']:.1f}-{seg['end']:.1f}s: {seg['text'][:40]}...")
                if len(segments) > 5:
                    print(f"  ... and {len(segments) - 5} more")

                # ── Stage 6: 写入数据库 ──
                _update_progress(project_id, "写入结果...", 90)

                speaker_sub_segs: dict[str, list] = {}
                for seg in segments:
                    spk = seg["speaker"]
                    if spk not in speaker_sub_segs:
                        speaker_sub_segs[spk] = []
                    speaker_sub_segs[spk].append(seg)

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
                for seg in segments:
                    sentence = Sentence(
                        project_id=project_id,
                        speaker_id=speaker_map.get(seg["speaker"]),
                        text=seg["text"],
                        start_time=seg["start"],
                        end_time=seg["end"],
                        sort_order=sort_order,
                    )
                    session.add(sentence)
                    sort_order += 1

                # 为每个说话人提取参考音频（取最长句子）
                for spk_label, speaker_id in speaker_map.items():
                    spk_segs = speaker_sub_segs[spk_label]
                    if not spk_segs:
                        continue
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

                project.duration = segments[-1]["end"] if segments else 0
                project.status = "ready"
                if file_hash:
                    project.file_hash = file_hash
                await session.commit()

                _update_progress(project_id, "完成", 100)
                print(f"[funasr] Done: {len(segments)} sentences, {len(speaker_map)} speakers")

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(tb)
                print(f"[funasr] Processing error: {e}")
                project.status = "error"
                project.last_error = f"{type(e).__name__}: {e}"
                await session.commit()

    asyncio.run(_update())


# ── 后处理 ──

def _post_process(segments: list[dict]) -> list[dict]:
    """对模型输出的句子做后处理：
    1. 合并相邻同说话人短句（总时长不超过合理上限）
    2. 切分过长句子（在逗号处）
    """
    # Step 1: 合并相邻同说话人句子
    merged = []
    for seg in segments:
        if not merged:
            merged.append(seg)
            continue

        prev = merged[-1]
        prev_dur = prev["end"] - prev["start"]
        cur_dur = seg["end"] - seg["start"]

        if (
            seg["speaker"] == prev["speaker"]
            and prev_dur + cur_dur <= MAX_SENTENCE_DURATION
        ):
            prev["text"] += seg["text"]
            prev["end"] = seg["end"]
        else:
            merged.append(seg)

    # Step 2: 切分过长句子
    result = []
    for seg in merged:
        dur = seg["end"] - seg["start"]
        if dur > MAX_SENTENCE_DURATION:
            result.extend(_split_long(seg))
        else:
            result.append(seg)

    return result


def _split_long(seg: dict) -> list[dict]:
    """将过长句子在逗号处切分。"""
    text = seg["text"]
    dur = seg["end"] - seg["start"]

    positions = [i for i, ch in enumerate(text) if ch in SENTENCE_PAUSE_PUNCT]
    if len(positions) < 1:
        # 无逗号，按时间均分
        parts = math.ceil(dur / MAX_SENTENCE_DURATION)
        chars_per = math.ceil(len(text) / parts)
        time_per = dur / parts
        result = []
        for i in range(parts):
            chunk = text[i * chars_per:(i + 1) * chars_per].strip()
            if len(chunk) >= MIN_SENTENCE_CHARS:
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
        chunk = text[prev:pos + 1].strip("，,；;：:")
        if len(chunk) >= MIN_SENTENCE_CHARS:
            result.append({
                "text": chunk,
                "start": seg["start"] + prev * ratio,
                "end": seg["start"] + (pos + 1) * ratio,
                "speaker": seg["speaker"],
            })
        prev = pos + 1

    if prev < len(text):
        chunk = text[prev:].strip()
        if len(chunk) >= MIN_SENTENCE_CHARS:
            result.append({
                "text": chunk,
                "start": seg["start"] + prev * ratio,
                "end": seg["end"],
                "speaker": seg["speaker"],
            })

    return result if result else [seg]


# ── 兼容回退：模型没有 sentence_info 时手动切分 ──

def _fallback_split(result_data: dict) -> list[dict]:
    """旧版模型兼容：从 text + timestamp 重建句子。

    这个回退路径应该很少用到。当前使用的 paraformer spk 模型
    标准输出已包含 sentence_info。
    """
    text = result_data.get("text", "")
    timestamps = result_data.get("timestamp", [])
    words = text.split()

    if not words or len(timestamps) < len(words):
        return []

    # 按时间间隙分组
    GAP = 0.5
    sentences = []
    cur_words = []
    cur_start = 0
    cur_end = 0

    for i, (word, ts) in enumerate(zip(words, timestamps)):
        w_start = ts[0] if ts else 0
        w_end = ts[1] if len(ts) > 1 else w_start

        if i > 0 and cur_words:
            prev_end = timestamps[i - 1][1] if len(timestamps[i - 1]) > 1 else 0
            if (w_start - prev_end) / 1000.0 > GAP:
                s_text = "".join(cur_words).strip()
                if s_text and cur_end > cur_start:
                    sentences.append({
                        "text": s_text,
                        "start": cur_start / 1000.0,
                        "end": cur_end / 1000.0,
                        "spk": 0,
                    })
                cur_words = []
                cur_start = w_start

        if not cur_words:
            cur_start = w_start
        cur_words.append(word)
        cur_end = w_end

    if cur_words:
        s_text = "".join(cur_words).strip()
        if s_text and cur_end > cur_start:
            sentences.append({
                "text": s_text,
                "start": cur_start / 1000.0,
                "end": cur_end / 1000.0,
                "spk": 0,
            })

    return sentences
