"""ASR 服务 — WhisperX：语音识别 + 时间戳 + 说话人分离。"""

from pathlib import Path

from config import settings


def _speaker_label_to_name(label: str) -> str:
    """WhisperX speaker label → 中文说话人名称。"""
    mapping = {
        "SPEAKER_00": "说话人A", "SPEAKER_01": "说话人B",
        "SPEAKER_02": "说话人C", "SPEAKER_03": "说话人D",
        "SPEAKER_04": "说话人E", "SPEAKER_05": "说话人F",
    }
    return mapping.get(label, label)


async def process_audio_with_asr(project_id: str, file_path: str, file_hash: str = ""):
    """后台任务：对上传文件执行 ASR，结果写入数据库。"""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _process_sync, project_id, file_path, file_hash)


def _process_sync(project_id: str, file_path: str, file_hash: str = ""):
    """同步处理逻辑，在线程池中运行。"""
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
                # Step 1: 提取音频（如果是视频）
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
                        await session.commit()
                        return
                else:
                    project.original_audio = str(audio_path)

                # Step 2: 加载 WhisperX 模型
                import whisperx
                import torch

                # 从用户设置中读取模型大小，默认 medium
                import json
                _settings_file = settings.data_dir / "settings.json"
                _model_size = "medium"
                if _settings_file.exists():
                    try:
                        _data = json.loads(_settings_file.read_text(encoding="utf-8"))
                        _model_size = _data.get("whisper_model_size", "medium")
                    except Exception:
                        pass

                device = settings.whisper_device
                if device == "auto":
                    if torch.cuda.is_available():
                        device = "cuda"
                        compute_type = "float16"
                    else:
                        device = "cpu"
                        compute_type = "int8"
                elif device == "mps":
                    # faster-whisper 不支持 MPS（macOS 专有），回退 CPU
                    device = "cpu"
                    compute_type = "int8"
                else:
                    compute_type = settings.whisper_compute_type
                    if compute_type == "auto":
                        compute_type = "float16" if device == "cuda" else "int8"

                print(f"[asr] Loading WhisperX model={_model_size} device={device} compute_type={compute_type}")

                asr_model = whisperx.load_model(
                    _model_size, device=device, compute_type=compute_type
                )

                # Step 3: 转写
                audio_str = str(audio_path)
                transcribe_result = asr_model.transcribe(audio_str, batch_size=16)
                print(f"[asr] Transcribed {len(transcribe_result.get('segments', []))} segments")

                # Step 4: 对齐时间戳
                lang = transcribe_result.get("language", "zh")
                model_a, metadata = whisperx.load_align_model(language_code=lang, device=device)
                aligned = whisperx.align(
                    transcribe_result["segments"], model_a, metadata, audio_str, device
                )

                # Step 5: 说话人分离（可选）
                hf_token = settings.hf_token
                if hf_token:
                    try:
                        diarize_model = whisperx.DiarizationPipeline(
                            use_auth_token=hf_token, device=device
                        )
                        diarize_segments = diarize_model(audio_str)
                        aligned = whisperx.assign_word_speakers(diarize_segments, aligned)
                    except Exception as e:
                        print(f"[asr] Diarization failed (continuing without): {e}")

                segments = aligned.get("segments", [])
                if not segments:
                    project.status = "error"
                    await session.commit()
                    return

                # Step 6: 按说话人轮次分句
                speaker_map = {}  # speaker_label → speaker_db_id
                sub_segments = []  # 拆分后的子句列表

                for seg in segments:
                    words = seg.get("words", [])
                    # 检查是否有词级说话人标注
                    has_word_speakers = words and any(w.get("speaker") for w in words)

                    if not has_word_speakers:
                        # 无说话人分离数据：整段作为一句
                        spk = seg.get("speaker", "SPEAKER_00")
                        sub_segments.append({
                            "speaker": spk,
                            "text": seg.get("text", "").strip(),
                            "start": seg.get("start", 0),
                            "end": seg.get("end", 0),
                        })
                        continue

                    # 按词级说话人变化切分
                    current_spk = None
                    current_words = []
                    for w in words:
                        spk = w.get("speaker", "SPEAKER_00")
                        if spk != current_spk:
                            if current_words:
                                text = "".join(cw.get("word", "") for cw in current_words)
                                sub_segments.append({
                                    "speaker": current_spk,
                                    "text": text.strip(),
                                    "start": current_words[0].get("start", 0),
                                    "end": current_words[-1].get("end", 0),
                                })
                            current_spk = spk
                            current_words = [w]
                        else:
                            current_words.append(w)

                    if current_words:
                        text = "".join(cw.get("word", "") for cw in current_words)
                        sub_segments.append({
                            "speaker": current_spk,
                            "text": text.strip(),
                            "start": current_words[0].get("start", 0),
                            "end": current_words[-1].get("end", 0),
                        })

                # 收集说话人 → 子句映射
                speaker_sub_segs = {}  # speaker_label → [sub_segments]
                for sub in sub_segments:
                    spk = sub["speaker"]
                    if spk not in speaker_sub_segs:
                        speaker_sub_segs[spk] = []
                    speaker_sub_segs[spk].append(sub)

                # 写入说话人
                for spk_label in sorted(speaker_sub_segs.keys()):
                    speaker = Speaker(
                        project_id=project_id,
                        name=_speaker_label_to_name(spk_label),
                    )
                    session.add(speaker)
                    await session.flush()
                    speaker_map[spk_label] = speaker.id

                # 写入句子
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

                # 为每个说话人提取参考音频（取最长子句）
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
                print(f"[asr] Done: {len(segments)} sentences, {len(speaker_map)} speakers")

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(tb)
                print(f"[asr] Processing error: {e}")
                project.status = "error"
                project.last_error = f"{type(e).__name__}: {e}"
                await session.commit()

    asyncio.run(_update())
