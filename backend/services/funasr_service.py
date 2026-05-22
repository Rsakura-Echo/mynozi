"""FunASR 服务 — ASR + VAD + 说话人分离（模型从国内 ModelScope 下载，无需 token）。

切分策略（按优先级）：
1. 时间间隙 > UTTERANCE_GAP_THRESHOLD → 视为不同"话语块"（不同说话人/不同轮次）
2. 话语块内按 CAM++ 聚类分配说话人
3. 话语块内按标点（。！？）切分句子
4. 长句在逗号/停顿处二次切分
"""

import os
from pathlib import Path
from config import settings

# FunASR 模型从 ModelScope 国内源下载，设置离线模式使用本地缓存
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")

# ── 切分配置 ──
UTTERANCE_GAP_THRESHOLD = 0.45  # 词间隔超过此秒数 = 新话语块（VAD 边界）
MAX_SENTENCE_DURATION = 8.0     # 超过此值尝试在逗号/停顿处二次切分
HARD_SPLIT_DURATION = 12.0      # 大于此值强制均分（即使无标点）
MIN_SENTENCE_DURATION = 0.3     # 短于此值合并到相邻句
MIN_SENTENCE_CHARS = 2          # 少于此字符数视为无效

SENTENCE_END_PUNCT = set("。！？!")
SENTENCE_PAUSE_PUNCT = set("，,；;：:")


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

                # ── Stage 5: 按时间间隙分"话语块"（VAD 边界优先于标点）──
                _update_progress(project_id, "切分句子...", 40)
                words = asr_text.split()
                if not words or len(timestamps) < len(words):
                    project.status = "error"
                    project.last_error = "ASR 时间戳数量不匹配"
                    await session.commit()
                    return

                # 打印完整模型输出结构（仅 key）用于调试
                print(f"[funasr] Model output keys: {list(result_data.keys())}")
                if "sentences" in result_data:
                    print(f"[funasr] Model has 'sentences' field: {len(result_data['sentences'])} entries")
                if "sentence_info" in result_data:
                    print(f"[funasr] Model has 'sentence_info' field: {len(result_data['sentence_info'])} entries")

                # 步骤 1：按时间间隙分组 → "话语块"（utterance）
                utterances = _group_words_into_utterances(words, timestamps)
                print(f"[funasr] Utterances from time gaps: {len(utterances)}")

                # 步骤 2：对每个话语块做 CAM++ 说话人识别
                _update_progress(project_id, "说话人分离...", 55)
                _assign_speakers_to_utterances(utterances, audio_data, sample_rate, spk_model)

                # 步骤 3：话语块内按标点切分句子
                _update_progress(project_id, "后处理...", 75)
                sub_segments = _split_utterances_into_sentences(utterances)

                # 步骤 4：合并过短的相邻同说话人句子
                sub_segments = _merge_adjacent_same_speaker(sub_segments)

                # 统一转换毫秒 -> 秒
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


# ═══════════════════════════════════════════════════════════════
# 步骤 1：按时间间隙将词分组为"话语块"（utterance）
# ═══════════════════════════════════════════════════════════════

def _group_words_into_utterances(
    words: list[str], timestamps: list,
) -> list[dict]:
    """按词间时间间隙分组。间隙 > UTTERANCE_GAP_THRESHOLD 视为话语边界。

    这样 A 的一段连续发言 → 一个话语块，B 的回应 → 另一个话语块。
    话语块之间天然就是说话人/轮次的切换点。
    """
    utterances: list[dict] = []
    cur_words: list[str] = []
    cur_start_ms = 0
    cur_end_ms = 0

    for i, (word, ts) in enumerate(zip(words, timestamps)):
        w_start = ts[0] if len(ts) > 0 else 0
        w_end = ts[1] if len(ts) > 1 else w_start

        # 检查与上一个词的间隙
        if i > 0 and cur_words:
            prev_end = timestamps[i - 1][1] if len(timestamps[i - 1]) > 1 else 0
            gap = (w_start - prev_end) / 1000.0
            if gap > UTTERANCE_GAP_THRESHOLD:
                # 间隙超阈值 → 提交当前话语块
                utt_text = "".join(cur_words).strip()
                if utt_text and cur_end_ms > cur_start_ms:
                    utterances.append({
                        "text": utt_text,
                        "start_ms": cur_start_ms,
                        "end_ms": cur_end_ms,
                    })
                cur_words = []
                cur_start_ms = w_start

        if not cur_words:
            cur_start_ms = w_start
        cur_words.append(word)
        cur_end_ms = w_end

    # 最后一块
    if cur_words:
        utt_text = "".join(cur_words).strip()
        if utt_text and cur_end_ms > cur_start_ms:
            utterances.append({
                "text": utt_text,
                "start_ms": cur_start_ms,
                "end_ms": cur_end_ms,
            })

    return utterances


# ═══════════════════════════════════════════════════════════════
# 步骤 2：对话语块做 CAM++ 说话人识别（聚类）
# ═══════════════════════════════════════════════════════════════

MIN_SPK_AUDIO_DURATION = 0.8  # 短于此值的话语块跳过 CAM++，从相邻块继承


def _assign_speakers_to_utterances(
    utterances: list[dict], audio_data, sample_rate: int, spk_model,
):
    """对每个足够长的话语块跑 CAM++，提取声纹后聚类分配说话人。

    短话语块（< 0.8s）继承最近长话语块的说话人标签。
    """
    import numpy as np

    if not utterances:
        return

    audio_len = len(audio_data)

    # 找出哪些话语块够长，值得做 CAM++
    long_idx = [
        i for i, u in enumerate(utterances)
        if (u["end_ms"] - u["start_ms"]) / 1000.0 >= MIN_SPK_AUDIO_DURATION
    ]

    if len(long_idx) < 2:
        # 单说话人或全是短句 → 所有人同一标签
        for u in utterances:
            u["speaker"] = "SPEAKER_00"
        return

    print(f"[funasr] Running CAM++ on {len(long_idx)}/{len(utterances)} utterances...")

    spk_embeddings = []
    valid_idx = []

    for idx in long_idx:
        u = utterances[idx]
        start_s = int(u["start_ms"] * sample_rate / 1000)
        end_s = int(u["end_ms"] * sample_rate / 1000)
        start_s = max(0, start_s)
        end_s = min(audio_len, end_s)

        seg_audio = audio_data[start_s:end_s]

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
                valid_idx.append(idx)
        except Exception as e:
            print(f"[funasr] CAM++ error for utterance {idx}: {e}")

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
        print(f"[funasr] Found {num_speakers} speakers from {len(valid_idx)} utterances")
    elif len(spk_embeddings) == 1:
        labels = [0]
    else:
        labels = []

    idx_to_label = {valid_idx[i]: int(labels[i]) for i in range(len(labels))}

    # 分配说话人（短话语块继承最近的长话语块的标签）
    for i, u in enumerate(utterances):
        if i in idx_to_label:
            u["speaker"] = f"SPEAKER_{idx_to_label[i]:02d}"
        else:
            best_dist = float("inf")
            best_label = 0
            for vi in valid_idx:
                dist = abs(i - vi)
                if dist < best_dist:
                    best_dist = dist
                    best_label = idx_to_label[vi]
            u["speaker"] = f"SPEAKER_{best_label:02d}"


# ═══════════════════════════════════════════════════════════════
# 步骤 3：话语块内按标点切分为句子
# ═══════════════════════════════════════════════════════════════

def _split_utterances_into_sentences(utterances: list[dict]) -> list[dict]:
    """将每个话语块按标点切分为最终句子。

    规则：
    - 按。！？切分句子
    - 句子时长 > MAX_SENTENCE_DURATION 则在逗号处二次切分
    - 句子时长 > HARD_SPLIT_DURATION 则强制按时间均分
    - 过短句子合并到相邻句
    - 所有句子继承话语块的 speaker 标签
    """
    result: list[dict] = []

    for u in utterances:
        text = u["text"]
        duration_ms = u["end_ms"] - u["start_ms"]
        duration_s = duration_ms / 1000.0
        speaker = u.get("speaker", "SPEAKER_00")

        # 对话语块较短 → 直接作为一个句子
        if duration_s <= MAX_SENTENCE_DURATION and len(text) < 50:
            if len(text) >= MIN_SENTENCE_CHARS:
                result.append({
                    "text": text,
                    "start_ms": u["start_ms"],
                    "end_ms": u["end_ms"],
                    "speaker": speaker,
                })
            continue

        # 按标点切分
        sentences = _split_by_punctuation(text, u["start_ms"], u["end_ms"])
        for s in sentences:
            s["speaker"] = speaker
            sd = (s["end_ms"] - s["start_ms"]) / 1000.0

            # 长句二次切分
            if sd > HARD_SPLIT_DURATION:
                subs = _split_by_duration(s)
                for sub in subs:
                    sub["speaker"] = speaker
                result.extend(subs)
            elif sd > MAX_SENTENCE_DURATION:
                subs = _split_at_commas(s)
                for sub in subs:
                    sub["speaker"] = speaker
                result.extend(subs)
            else:
                result.append(s)

    # 合并过短句子
    result = _merge_short_sentences(result)

    return result


def _split_by_punctuation(text: str, start_ms: int, end_ms: int) -> list[dict]:
    """按。！？切分文本，按字符比例分配时间戳。"""
    if not text:
        return []

    duration_ms = end_ms - start_ms
    total_chars = max(len(text), 1)
    ratio = duration_ms / total_chars

    sentences = []
    cur = ""
    cur_start_char = 0

    for i, ch in enumerate(text):
        cur += ch
        if ch in SENTENCE_END_PUNCT:
            clean = cur.strip()
            if len(clean) >= MIN_SENTENCE_CHARS:
                sentences.append({
                    "text": clean,
                    "start_ms": int(start_ms + cur_start_char * ratio),
                    "end_ms": int(start_ms + (i + 1) * ratio),
                })
            cur = ""
            cur_start_char = i + 1

    # 剩余部分（不以标点结尾的文本）
    if cur.strip():
        clean = cur.strip()
        if len(clean) >= MIN_SENTENCE_CHARS:
            sentences.append({
                "text": clean,
                "start_ms": int(start_ms + cur_start_char * ratio),
                "end_ms": end_ms,
            })

    return sentences if sentences else [{"text": text, "start_ms": start_ms, "end_ms": end_ms}]


def _split_at_commas(seg: dict) -> list[dict]:
    """在逗号、分号处切分长句。"""
    text = seg["text"]
    duration_ms = seg["end_ms"] - seg["start_ms"]
    total_chars = max(len(text), 1)
    ratio = duration_ms / total_chars

    # 找停顿标点位置
    positions = [i for i, ch in enumerate(text) if ch in SENTENCE_PAUSE_PUNCT]

    if len(positions) < 1:
        return [seg]

    result = []
    prev = 0
    for pos in positions:
        chunk = text[prev:pos + 1].strip("，,；;：:")
        if len(chunk) >= MIN_SENTENCE_CHARS:
            result.append({
                "text": chunk,
                "start_ms": int(seg["start_ms"] + prev * ratio),
                "end_ms": int(seg["start_ms"] + (pos + 1) * ratio),
            })
        prev = pos + 1

    if prev < len(text):
        chunk = text[prev:].strip()
        if len(chunk) >= MIN_SENTENCE_CHARS:
            result.append({
                "text": chunk,
                "start_ms": int(seg["start_ms"] + prev * ratio),
                "end_ms": seg["end_ms"],
            })

    return result if result else [seg]


def _split_by_duration(seg: dict) -> list[dict]:
    """无标点长句 → 按时间均分。"""
    import math
    duration = (seg["end_ms"] - seg["start_ms"]) / 1000.0
    text = seg["text"]
    parts = max(1, math.ceil(duration / HARD_SPLIT_DURATION))
    chars_per = math.ceil(len(text) / parts)
    time_per = duration / parts
    result = []

    for i in range(parts):
        chunk = text[i * chars_per:(i + 1) * chars_per]
        if len(chunk) >= MIN_SENTENCE_CHARS:
            result.append({
                "text": chunk,
                "start_ms": int(seg["start_ms"] + i * time_per * 1000),
                "end_ms": int(seg["start_ms"] + (i + 1) * time_per * 1000),
            })

    return result if result else [seg]


def _merge_short_sentences(sentences: list[dict]) -> list[dict]:
    """合并过短句子到相邻句。向前合并（合并到前一句的末尾）。"""
    if not sentences:
        return sentences

    merged = []
    for seg in sentences:
        duration = (seg["end_ms"] - seg["start_ms"]) / 1000.0
        text_len = len(seg["text"])

        # 过短 / 过少字符 → 合并到前一句
        if (duration < MIN_SENTENCE_DURATION or text_len < MIN_SENTENCE_CHARS):
            if merged:
                prev = merged[-1]
                prev["text"] += seg["text"]
                prev["end_ms"] = seg["end_ms"]
            # 如果前面没有句子就丢弃（开头孤立短词）
            continue

        merged.append(seg)

    return merged


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
