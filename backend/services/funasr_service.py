"""FunASR 服务 — ASR + VAD + 说话人分离（模型从国内 ModelScope 下载，无需 token）。"""

import os
from pathlib import Path
from config import settings

# FunASR 模型从 ModelScope 国内源下载，设置离线模式使用本地缓存
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")

# 句子切分标点
SENTENCE_END_PUNCT = set("。！？!")
SENTENCE_PAUSE_PUNCT = set("，,；;：:")
ALL_PUNCT = SENTENCE_END_PUNCT | SENTENCE_PAUSE_PUNCT

# 句子时长限制（秒）
MAX_SENTENCE_DURATION = 15.0   # 超过此值尝试在逗号/停顿处二次切分
MIN_SENTENCE_DURATION = 0.3    # 短于此值合并到相邻句
MIN_SENTENCE_CHARS = 2         # 少于此字符数视为无效
WORD_PAUSE_THRESHOLD = 0.5     # 词间隔超过此秒数可切分（即使无标点）


def _speaker_label_to_name(label: str) -> str:
    mapping = {
        "SPEAKER_00": "说话人A", "SPEAKER_01": "说话人B",
        "SPEAKER_02": "说话人C", "SPEAKER_03": "说话人D",
        "SPEAKER_04": "说话人E", "SPEAKER_05": "说话人F",
    }
    return mapping.get(label, label)


async def process_audio_with_funasr(project_id: str, file_path: str, file_hash: str = ""):
    """后台任务：FunASR ASR + 句子切分 + VAD + CAM++ 说话人分离。"""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _process_sync, project_id, file_path, file_hash)


def _update_progress(project_id: str, stage: str, pct: float):
    """更新处理进度（存储到全局 dict，由 status API 读取）。"""
    _progress_store[project_id] = {"stage": stage, "pct": pct}


_progress_store: dict = {}


def _process_sync(project_id: str, file_path: str, file_hash: str = ""):
    from database import async_session
    from models import Project, Speaker, Sentence
    from services.audio_service import extract_audio_from_video, extract_sentence_audio
    from sqlalchemy import select, update as sql_update
    import asyncio
    import numpy as np
    import soundfile as sf

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

                # 加载音频数据
                audio_data, sample_rate = sf.read(audio_str, dtype="float32")
                if audio_data.ndim > 1:
                    audio_data = audio_data.mean(axis=1)

                total_duration = len(audio_data) / sample_rate
                print(f"[funasr] Audio duration: {total_duration:.1f}s, sample_rate: {sample_rate}")

                # ── Stage 2: 检测设备 ──
                import torch
                device = settings.asr_device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                print(f"[funasr] Using device: {device}")

                # ── Stage 3: 加载模型 ──
                _update_progress(project_id, "加载 ASR 模型...", 10)
                from funasr import AutoModel

                print(f"[funasr] Loading models...")

                asr_model = AutoModel(
                    model="iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn",
                    disable_update=True,
                    device=device,
                )

                vad_model = AutoModel(
                    model="fsmn-vad",
                    disable_update=True,
                    device=device,
                )

                spk_model = AutoModel(
                    model="cam++",
                    disable_update=True,
                    device=device,
                )

                # ── Stage 4: ASR 识别 ──
                _update_progress(project_id, "ASR 语音识别中...", 20)
                print(f"[funasr] Transcribing...")
                asr_result = asr_model.generate(input=audio_str, batch_size_s=300)

                if not asr_result or not asr_result[0].get("text"):
                    project.status = "error"
                    project.last_error = "ASR 识别结果为空"
                    await session.commit()
                    return

                result_data = asr_result[0]
                timestamps = result_data.get("timestamp", [])  # [[start_ms, end_ms], ...]
                asr_text = result_data.get("text", "")

                print(f"[funasr] ASR text length: {len(asr_text)}, timestamps: {len(timestamps)}")

                # ── Stage 5: 按标点切分句子 ──
                _update_progress(project_id, "切分句子...", 40)
                words = asr_text.split()
                raw_sentences = _split_text_into_sentences(asr_text, words, timestamps)

                # 二次处理：长句按逗号/停顿再切，短句合并
                refined = _refine_sentences(raw_sentences)

                print(f"[funasr] Raw sentences: {len(raw_sentences)}, refined: {len(refined)}")

                # ── Stage 6: 说话人分离（对每句话跑 CAM++，而非每 VAD 段）───
                _update_progress(project_id, "说话人分离...", 60)
                print(f"[funasr] Speaker diarization (per-sentence)...")

                # 对每句话提取说话人特征 + 聚类
                _assign_speakers_per_sentence(refined, audio_data, sample_rate, spk_model)

                # ── Stage 7: 合并相邻同说话人短句 ──
                _update_progress(project_id, "后处理...", 80)
                sub_segments = _merge_adjacent_same_speaker(refined)

                # 统一转换毫秒 -> 秒（后续 DB 写入使用 start/end）
                for seg in sub_segments:
                    seg["start"] = seg.pop("start_ms") / 1000.0
                    seg["end"] = seg.pop("end_ms") / 1000.0

                if not sub_segments:
                    project.status = "error"
                    project.last_error = "未能提取有效句子"
                    await session.commit()
                    return

                print(f"[funasr] Final segments: {len(sub_segments)}")

                # ── Stage 8: 写入数据库 ──
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

                project.duration = sub_segments[-1]["end"] if sub_segments else 0
                project.status = "ready"
                if file_hash:
                    project.file_hash = file_hash
                await session.commit()

                _update_progress(project_id, "完成", 100)
                print(f"[funasr] Done: {len(sub_segments)} sentences, {len(speaker_map)} speakers")

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(tb)
                print(f"[funasr] Processing error: {e}")
                project.status = "error"
                project.last_error = f"{type(e).__name__}: {e}"
                await session.commit()

    asyncio.run(_update())


# ── 句子切分 ──

def _split_text_into_sentences(
    full_text: str, words: list[str], timestamps: list,
) -> list[dict]:
    """按标点符号（。！？）将 ASR 文本 + 时间戳切分为句子。

    额外规则：
    - 如果两个词之间的时间间隔 > WORD_PAUSE_THRESHOLD，强制在此处切分
    - 每个句子携带其对应的词列表和时间范围
    """
    sentences = []
    current_words: list[str] = []
    current_ts: list[list] = []

    # 预处理：将文本按标点位置标注
    # words[i] 可能是 "你好" "。" "世界" 这样的，标点在单独的 token 中
    # 也可能标点附着在前一个词上："你好。" "世界"
    for i, (word, ts) in enumerate(zip(words, timestamps)):
        # 检查词中是否包含标点
        stripped = word.rstrip("。！？!，,；;：:")
        has_end_punct = any(c in SENTENCE_END_PUNCT for c in word)
        has_pause_punct = any(c in SENTENCE_PAUSE_PUNCT for c in word)

        # 词间隔检测（长停顿 = 句子边界）
        pause_split = False
        if i > 0 and len(timestamps) > i:
            prev_end = timestamps[i - 1][1] if len(timestamps[i - 1]) > 1 else 0
            curr_start = ts[0] if len(ts) > 0 else 0
            gap = (curr_start - prev_end) / 1000.0
            if gap > WORD_PAUSE_THRESHOLD:
                pause_split = True

        if pause_split and current_words:
            # 长停顿 → 提交当前句子
            sentences.append({
                "text": _rebuild_text(current_words),
                "start_ms": current_ts[0][0] if current_ts else 0,
                "end_ms": current_ts[-1][1] if current_ts else 0,
            })
            current_words = []
            current_ts = []

        if stripped:
            current_words.append(stripped)
        current_ts.append(ts)

        # 句子结束标点 → 切分
        if has_end_punct and current_words:
            text = _rebuild_text(current_words)
            if len(text) >= MIN_SENTENCE_CHARS:
                sentences.append({
                    "text": text,
                    "start_ms": current_ts[0][0] if current_ts else 0,
                    "end_ms": current_ts[-1][1] if current_ts else 0,
                })
            current_words = []
            current_ts = []

    # 残留词
    if current_words:
        text = _rebuild_text(current_words)
        if len(text) >= MIN_SENTENCE_CHARS:
            sentences.append({
                "text": text,
                "start_ms": current_ts[0][0] if current_ts else 0,
                "end_ms": current_ts[-1][1] if current_ts else 0,
            })

    return sentences


def _rebuild_text(words: list[str]) -> str:
    """重建中文文本（去除多余空格）。"""
    return "".join(words).strip()


def _refine_sentences(raw: list[dict]) -> list[dict]:
    """后处理优化：
    1. 超过 MAX_SENTENCE_DURATION 的句子在逗号/停顿处再切分
    2. 过短句子合并到相邻句
    """
    if not raw:
        return raw

    # Step 1: 长句二次切分
    split_result = []
    for seg in raw:
        duration = (seg["end_ms"] - seg["start_ms"]) / 1000.0
        if duration > MAX_SENTENCE_DURATION:
            # 尝试在逗号处切分
            sub_sentences = _split_long_sentence(seg)
            split_result.extend(sub_sentences)
        else:
            split_result.append(seg)

    if not split_result:
        return raw

    # Step 2: 合并过短句子到相邻句（优先合并到更短的那边）
    merged = []
    for seg in split_result:
        duration = (seg["end_ms"] - seg["start_ms"]) / 1000.0
        text_len = len(seg["text"])

        if duration < MIN_SENTENCE_DURATION or text_len < MIN_SENTENCE_CHARS:
            # 合并到前一句
            if merged:
                prev = merged[-1]
                prev["text"] += seg["text"]
                prev["end_ms"] = seg["end_ms"]
            # 否则下一轮循环会把它合并到后一句
            continue

        # 如果前一句太短，当前句吞并前句
        if merged:
            prev_duration = (merged[-1]["end_ms"] - merged[-1]["start_ms"]) / 1000.0
            if prev_duration < MIN_SENTENCE_DURATION and len(merged[-1]["text"]) < MIN_SENTENCE_CHARS * 3:
                seg["text"] = merged[-1]["text"] + seg["text"]
                seg["start_ms"] = merged[-1]["start_ms"]
                merged.pop()

        merged.append(seg)

    return merged


def _split_long_sentence(seg: dict) -> list[dict]:
    """将长句在逗号、分号等次要停顿处切分。"""
    text = seg["text"]
    duration_ms = seg["end_ms"] - seg["start_ms"]

    # 找到所有停顿标点的位置
    split_positions = []
    for i, ch in enumerate(text):
        if ch in SENTENCE_PAUSE_PUNCT:
            split_positions.append(i)

    if len(split_positions) < 2:
        # 标点太少，按时间均匀切分
        return _split_by_duration(seg)

    # 按标点位置切分
    result = []
    prev_pos = 0
    ratio_per_char = duration_ms / max(len(text), 1)

    for pos in split_positions:
        chunk = text[prev_pos:pos + 1].strip("，,；;：:")
        if len(chunk) >= MIN_SENTENCE_CHARS:
            chunk_start_ms = seg["start_ms"] + int(prev_pos * ratio_per_char)
            chunk_end_ms = seg["start_ms"] + int((pos + 1) * ratio_per_char)
            result.append({"text": chunk, "start_ms": chunk_start_ms, "end_ms": chunk_end_ms})
        prev_pos = pos + 1

    # 最后一段
    if prev_pos < len(text):
        chunk = text[prev_pos:].strip()
        if len(chunk) >= MIN_SENTENCE_CHARS:
            chunk_start_ms = seg["start_ms"] + int(prev_pos * ratio_per_char)
            result.append({"text": chunk, "start_ms": chunk_start_ms, "end_ms": seg["end_ms"]})

    return result if result else [seg]


def _split_by_duration(seg: dict) -> list[dict]:
    """无标点时，按时间均匀切分长句。"""
    import math
    duration = (seg["end_ms"] - seg["start_ms"]) / 1000.0
    text = seg["text"]
    parts = max(1, math.ceil(duration / MAX_SENTENCE_DURATION))
    chars_per_part = math.ceil(len(text) / parts)
    result = []
    time_per_part = duration / parts

    for i in range(parts):
        chunk = text[i * chars_per_part:(i + 1) * chars_per_part]
        if len(chunk) >= MIN_SENTENCE_CHARS:
            result.append({
                "text": chunk,
                "start_ms": int(seg["start_ms"] + i * time_per_part * 1000),
                "end_ms": int(seg["start_ms"] + (i + 1) * time_per_part * 1000),
            })

    return result if result else [seg]


# ── 说话人分配 ──

# 最短音频长度（秒），短于此值的句子跳过 CAM++，继承相邻句的说话人
MIN_SPK_AUDIO_DURATION = 0.8


def _assign_speakers_per_sentence(
    sentences: list[dict], audio_data, sample_rate: int, spk_model,
):
    """对每句话单独跑 CAM++ 提取声纹特征，然后聚类分配说话人。

    相比在 VAD 段上跑 CAM++，逐句识别能正确区分 VAD 段内交替说话的多个人。
    太短的句子跳过 CAM++，后续通过继承相邻句获得说话人标签。
    """
    import numpy as np

    if not sentences:
        return

    audio_len = len(audio_data)

    # 收集每句话的时长
    sent_durations = [
        (seg["end_ms"] - seg["start_ms"]) / 1000.0
        for seg in sentences
    ]

    # 判断是否可能是多人对话（有足够长的句子才做聚类）
    long_sentences = [i for i, d in enumerate(sent_durations) if d >= MIN_SPK_AUDIO_DURATION]

    if len(long_sentences) < 2:
        # 单说话人场景
        for seg in sentences:
            seg["speaker"] = "SPEAKER_00"
        return

    print(f"[funasr] Extracting speaker embeddings for {len(long_sentences)} sentences...")
    spk_embeddings = []
    valid_indices = []

    for idx in long_sentences:
        seg = sentences[idx]
        start_sample = int(seg["start_ms"] * sample_rate / 1000)
        end_sample = int(seg["end_ms"] * sample_rate / 1000)
        start_sample = max(0, start_sample)
        end_sample = min(audio_len, end_sample)

        seg_audio = audio_data[start_sample:end_sample]

        try:
            spk_res = spk_model.generate(input=seg_audio)
            if spk_res and "spk_embedding" in spk_res[0]:
                emb = spk_res[0]["spk_embedding"]
                if hasattr(emb, "cpu"):
                    emb = emb.cpu().numpy().flatten()
                elif hasattr(emb, "numpy"):
                    emb = emb.numpy().flatten()
                else:
                    emb = np.array(emb).flatten()
                spk_embeddings.append(emb)
                valid_indices.append(idx)
        except Exception as e:
            print(f"[funasr] CAM++ error for sentence {idx}: {e}")

    # 聚类
    if len(spk_embeddings) >= 2:
        from sklearn.cluster import AgglomerativeClustering
        embeddings_array = np.array(spk_embeddings)
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=0.4,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(embeddings_array)
        num_speakers = len(set(labels))
        print(f"[funasr] Found {num_speakers} speakers for {len(valid_indices)} sentences")
    elif len(spk_embeddings) == 1:
        labels = [0]
        num_speakers = 1
    else:
        labels = []
        num_speakers = 0

    # 为做了 CAM++ 的句子分配标签
    idx_to_label = {valid_indices[i]: int(labels[i]) for i in range(len(labels))}

    # 为所有句子分配说话人（短句继承最近的已识别句子）
    for i, seg in enumerate(sentences):
        if i in idx_to_label:
            seg["speaker"] = f"SPEAKER_{idx_to_label[i]:02d}"
        else:
            # 找最近的有标签的句子
            best_dist = float("inf")
            best_label = 0
            for vi in valid_indices:
                dist = abs(i - vi)
                if dist < best_dist:
                    best_dist = dist
                    best_label = idx_to_label[vi]
            seg["speaker"] = f"SPEAKER_{best_label:02d}"


def _merge_adjacent_same_speaker(segments: list[dict]) -> list[dict]:
    """合并相邻同说话人的句子（但保留合理的时长上限）。"""
    if not segments:
        return segments

    merged = []
    for seg in segments:
        if not merged:
            merged.append(seg)
            continue

        prev = merged[-1]
        prev_dur = (prev["end_ms"] - prev["start_ms"]) / 1000.0
        cur_dur = (seg["end_ms"] - seg["start_ms"]) / 1000.0

        # 同说话人 + 合并后不超长 → 合并
        if (
            seg.get("speaker") == prev.get("speaker")
            and prev_dur + cur_dur <= MAX_SENTENCE_DURATION * 1.5
        ):
            prev["text"] += seg["text"]
            prev["end_ms"] = seg["end_ms"]
        else:
            merged.append(seg)

    return merged
