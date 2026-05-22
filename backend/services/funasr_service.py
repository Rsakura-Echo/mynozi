"""FunASR 服务 — ASR + VAD + 说话人分离（模型从国内 ModelScope 下载，无需 token）。"""

import os
from pathlib import Path
from config import settings

# FunASR 模型从 ModelScope 国内源下载，设置离线模式使用本地缓存
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")


def _speaker_label_to_name(label: str) -> str:
    mapping = {
        "SPEAKER_00": "说话人A", "SPEAKER_01": "说话人B",
        "SPEAKER_02": "说话人C", "SPEAKER_03": "说话人D",
        "SPEAKER_04": "说话人E", "SPEAKER_05": "说话人F",
    }
    return mapping.get(label, label)


async def process_audio_with_funasr(project_id: str, file_path: str, file_hash: str = ""):
    """后台任务：FunASR ASR + VAD + CAM++ 说话人分离。"""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _process_sync, project_id, file_path, file_hash)


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
                # Step 1: 提取音频
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

                # 加载音频数据（用于后续切段）
                audio_data, sample_rate = sf.read(audio_str, dtype="float32")
                if audio_data.ndim > 1:
                    audio_data = audio_data.mean(axis=1)

                # Step 2: 自动检测设备
                import torch
                device = settings.asr_device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                print(f"[funasr] Using device: {device}")

                # Step 3: 加载模型
                from funasr import AutoModel

                print(f"[funasr] Loading models...")

                # ASR: Paraformer（VAD + 标点 + 说话人）
                asr_model = AutoModel(
                    model="iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn",
                    disable_update=True,
                    device=device,
                )

                # VAD
                vad_model = AutoModel(
                    model="fsmn-vad",
                    disable_update=True,
                    device=device,
                )

                # 说话人识别
                spk_model = AutoModel(
                    model="cam++",
                    disable_update=True,
                    device=device,
                )

                # Step 3: ASR 识别
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

                # Step 4: VAD 检测语音段
                print(f"[funasr] VAD detecting...")
                vad_result = vad_model.generate(input=audio_str)
                vad_segments = vad_result[0].get("value", []) if vad_result else []
                # vad_segments = [[start_ms, end_ms], ...]

                # Step 5: 对每个 VAD 段提取说话人特征 + 聚类
                if vad_segments and len(vad_segments) >= 2:
                    print(f"[funasr] Extracting speaker embeddings for {len(vad_segments)} segments...")
                    spk_embeddings = []
                    valid_segments = []

                    for seg in vad_segments:
                        start_ms, end_ms = seg[0], seg[1]
                        start_sample = int(start_ms * sample_rate / 1000)
                        end_sample = int(end_ms * sample_rate / 1000)
                        start_sample = max(0, start_sample)
                        end_sample = min(len(audio_data), end_sample)

                        if end_sample - start_sample < sample_rate * 0.3:
                            continue  # 跳过太短的段（< 0.3秒）

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
                                valid_segments.append(seg)
                        except Exception as e:
                            print(f"[funasr] CAM++ error for segment {seg}: {e}")

                    # 聚类：使用余弦距离
                    if len(spk_embeddings) >= 2:
                        from sklearn.cluster import AgglomerativeClustering
                        embeddings_array = np.array(spk_embeddings)
                        clustering = AgglomerativeClustering(
                            n_clusters=None,
                            distance_threshold=0.35,
                            metric="cosine",
                            linkage="average",
                        )
                        labels = clustering.fit_predict(embeddings_array)
                        speaker_labels = [f"SPEAKER_{l:02d}" for l in labels]
                        print(f"[funasr] Found {len(set(labels))} speakers for {len(valid_segments)} VAD segments")
                    elif len(spk_embeddings) == 1:
                        speaker_labels = ["SPEAKER_00"]
                    else:
                        speaker_labels = []

                    # Step 6: 用 VAD 段 + 说话人标签分割 ASR 文本
                    sub_segments = []
                    for i in range(len(valid_segments)):
                        vad_start_ms, vad_end_ms = valid_segments[i][0], valid_segments[i][1]

                        # 找出落在这个 VAD 段内的 ASR words（按时间戳中点）
                        seg_text_parts = []
                        seg_start_ms = None
                        seg_end_ms = None
                        words = asr_text.split()

                        for j, ts in enumerate(timestamps):
                            word_start_ms, word_end_ms = ts[0], ts[1]
                            word_mid = (word_start_ms + word_end_ms) / 2
                            if vad_start_ms <= word_mid <= vad_end_ms:
                                if seg_start_ms is None:
                                    seg_start_ms = word_start_ms
                                seg_end_ms = word_end_ms
                                if j < len(words):
                                    seg_text_parts.append(words[j])

                        text = "".join(seg_text_parts).strip()
                        # 过滤无意义短句（纯标点或空）
                        if not text or all(c in "，。、；：？！""''（）《》…—·" for c in text):
                            continue

                        spk = speaker_labels[i] if i < len(speaker_labels) else "SPEAKER_00"
                        sub_segments.append({
                            "speaker": spk,
                            "text": text,
                            "start": (seg_start_ms or vad_start_ms) / 1000.0,
                            "end": (seg_end_ms or vad_end_ms) / 1000.0,
                        })
                else:
                    # 无 VAD 段或只有一段：全文作为一句
                    print(f"[funasr] No VAD segments, using full text as one sentence...")
                    text = asr_text.replace(" ", "")
                    sub_segments = [{
                        "speaker": "SPEAKER_00",
                        "text": text,
                        "start": 0.0,
                        "end": len(audio_data) / sample_rate,
                    }]

                if not sub_segments:
                    project.status = "error"
                    project.last_error = "未能提取有效句子"
                    await session.commit()
                    return

                # Step 7: 合并相邻同说话人的段
                merged = []
                for sub in sub_segments:
                    if merged and merged[-1]["speaker"] == sub["speaker"]:
                        # 合并文本和时间
                        merged[-1]["text"] += sub["text"]
                        merged[-1]["end"] = sub["end"]
                    else:
                        merged.append(sub)
                sub_segments = merged

                # Step 8: 收集说话人
                speaker_sub_segs = {}
                for sub in sub_segments:
                    spk = sub["speaker"]
                    if spk not in speaker_sub_segs:
                        speaker_sub_segs[spk] = []
                    speaker_sub_segs[spk].append(sub)

                # Step 9: 写入说话人
                speaker_map = {}
                for spk_label in sorted(speaker_sub_segs.keys()):
                    speaker = Speaker(
                        project_id=project_id,
                        name=_speaker_label_to_name(spk_label),
                    )
                    session.add(speaker)
                    await session.flush()
                    speaker_map[spk_label] = speaker.id

                # Step 10: 写入句子
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

                # Step 11: 为每个说话人提取参考音频（取最长句子）
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
