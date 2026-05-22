"""句子管理 + TTS 生成 API。"""

import os
import asyncio
import json
import time
from pathlib import Path

os.environ.setdefault("MODELSCOPE_OFFLINE", "1")

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_session
from models import Project, Sentence, Speaker
from schemas import SentenceUpdate, SentenceSplitRequest, TTSGenerateRequest, RegionAddRequest
from services.runninghub_service import RunningHubService
from services.audio_service import extract_sentence_audio
from config import settings

router = APIRouter(prefix="/api/projects/{project_id}/sentences", tags=["sentences"])

# ── 撤回系统 ──

MAX_UNDO = 20


def _undo_dir(project_id: str) -> Path:
    return settings.data_dir / "undo" / project_id


async def _save_undo_snapshot(project_id: str, session: AsyncSession):
    """保存当前句子状态快照（用于撤回）。"""
    result = await session.execute(
        select(Sentence).where(Sentence.project_id == project_id)
    )
    rows = []
    for s in result.scalars().all():
        rows.append({
            "id": s.id,
            "project_id": s.project_id,
            "speaker_id": s.speaker_id,
            "text": s.text,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "emotion_happy": s.emotion_happy,
            "emotion_angry": s.emotion_angry,
            "emotion_sad": s.emotion_sad,
            "emotion_fear": s.emotion_fear,
            "emotion_hate": s.emotion_hate,
            "emotion_low": s.emotion_low,
            "emotion_surprise": s.emotion_surprise,
            "emotion_neutral": s.emotion_neutral,
            "sort_order": s.sort_order,
            "is_deleted": s.is_deleted,
            "tts_status": s.tts_status,
            "generated_audio": s.generated_audio,
        })

    dir_path = _undo_dir(project_id)
    dir_path.mkdir(parents=True, exist_ok=True)
    ts = str(int(time.time() * 1000))
    snapshot_path = dir_path / f"{ts}.json"
    snapshot_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    # 清理超出上限的旧快照
    all_snapshots = sorted(dir_path.glob("*.json"))
    for old in all_snapshots[:-MAX_UNDO]:
        old.unlink(missing_ok=True)


async def _restore_last_snapshot(project_id: str, session: AsyncSession) -> bool:
    """恢复最近一次快照。返回是否成功。"""
    dir_path = _undo_dir(project_id)
    if not dir_path.exists():
        return False
    all_snapshots = sorted(dir_path.glob("*.json"))
    if not all_snapshots:
        return False

    latest = all_snapshots[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))

    # 删除当前所有句子
    await session.execute(
        sql_delete(Sentence).where(Sentence.project_id == project_id)
    )

    # 重新插入快照中的句子
    for row in data:
        s = Sentence(
            id=row["id"],
            project_id=row["project_id"],
            speaker_id=row.get("speaker_id"),
            text=row["text"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            emotion_happy=row.get("emotion_happy", 50),
            emotion_angry=row.get("emotion_angry", 0),
            emotion_sad=row.get("emotion_sad", 0),
            emotion_fear=row.get("emotion_fear", 0),
            emotion_hate=row.get("emotion_hate", 0),
            emotion_low=row.get("emotion_low", 0),
            emotion_surprise=row.get("emotion_surprise", 0),
            emotion_neutral=row.get("emotion_neutral", 50),
            sort_order=row["sort_order"],
            is_deleted=row.get("is_deleted", False),
            tts_status=row.get("tts_status", "pending"),
            generated_audio=row.get("generated_audio"),
        )
        session.add(s)

    # 删除已使用的快照
    latest.unlink(missing_ok=True)
    return True


def _get_runninghub_service() -> RunningHubService:
    """从用户设置中读取 RunningHub 密钥和工作流 ID，fallback 到 config。"""
    import json
    _settings_file = settings.data_dir / "settings.json"
    api_key = settings.runninghub_api_key
    workflow_id = settings.runninghub_workflow_id
    if _settings_file.exists():
        try:
            data = json.loads(_settings_file.read_text(encoding="utf-8"))
            if data.get("runninghub_api_key"):
                api_key = data["runninghub_api_key"]
            if data.get("runninghub_workflow_id"):
                workflow_id = data["runninghub_workflow_id"]
        except Exception:
            pass
    return RunningHubService(api_key, workflow_id)


@router.put("/{sentence_id}")
async def update_sentence(
    project_id: str,
    sentence_id: str,
    body: SentenceUpdate,
    session: AsyncSession = Depends(get_session),
):
    """更新句子文本/情感/说话人。"""
    result = await session.execute(
        select(Sentence).where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence:
        raise HTTPException(status_code=404, detail="句子不存在")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(sentence, key, value)
    await session.commit()
    await session.refresh(sentence)
    return {"message": "已更新", "sentence_id": sentence_id}


@router.delete("/{sentence_id}")
async def delete_sentence(
    project_id: str,
    sentence_id: str,
    session: AsyncSession = Depends(get_session),
):
    """删除句子 — 物理删除，并调整后续句子的 sort_order。"""
    result = await session.execute(
        select(Sentence).where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence:
        raise HTTPException(status_code=404, detail="句子不存在")

    deleted_order = sentence.sort_order

    await _save_undo_snapshot(project_id, session)
    await session.delete(sentence)

    # 前移后续句子的 sort_order
    from sqlalchemy import update as sql_update
    await session.execute(
        sql_update(Sentence)
        .where(Sentence.project_id == project_id, Sentence.sort_order > deleted_order)
        .values(sort_order=Sentence.sort_order - 1)
    )
    await session.commit()
    return {"message": "已删除", "sentence_id": sentence_id}


@router.post("/{sentence_id}/restore")
async def restore_sentence(
    project_id: str,
    sentence_id: str,
    session: AsyncSession = Depends(get_session),
):
    """恢复被软删除的句子。"""
    from sqlalchemy import update as sql_update

    result = await session.execute(
        select(Sentence).where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence:
        raise HTTPException(status_code=404, detail="句子不存在")
    if not sentence.is_deleted:
        raise HTTPException(status_code=400, detail="该句子未被删除")

    await session.execute(
        sql_update(Sentence)
        .where(Sentence.id == sentence_id)
        .values(is_deleted=False)
    )
    await session.commit()
    return {"message": "已恢复", "sentence_id": sentence_id}


@router.post("/{sentence_id}/split")
async def split_sentence(
    project_id: str,
    sentence_id: str,
    body: SentenceSplitRequest,
    session: AsyncSession = Depends(get_session),
):
    """在指定时间点切割句子，同步运行 ASR 识别两段文字后返回。"""
    from sqlalchemy import update as sql_update
    from services.audio_service import extract_sentence_audio

    # 1. 获取原句子 + 项目原始音频
    result = await session.execute(
        select(Sentence).where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence:
        raise HTTPException(status_code=404, detail="句子不存在")

    proj_result = await session.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    original_audio = project.original_audio if project else None

    split_time = body.split_time
    if split_time <= sentence.start_time or split_time >= sentence.end_time:
        raise HTTPException(status_code=400, detail="切割时间点必须在句子时间范围内")

    # 2. 提取两段音频并同步运行 ASR 识别文字
    first_text = sentence.text  # 默认保留原标题
    second_text = sentence.text

    if original_audio:
        # 提取前半段音频
        clip_dir = settings.data_dir / "output" / project_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip1 = clip_dir / f"{sentence_id}_a.wav"
        clip2 = clip_dir / f"{sentence_id}_b.wav"

        extract_sentence_audio(original_audio, sentence.start_time, split_time, str(clip1))
        extract_sentence_audio(original_audio, split_time, sentence.end_time, str(clip2))

        # 同步运行 ASR（在线程池中执行，避免阻塞事件循环）
        loop = asyncio.get_event_loop()
        text1, text2 = await loop.run_in_executor(
            None, _split_asr_sync, str(clip1), str(clip2)
        )
        if text1:
            first_text = text1
        if text2:
            second_text = text2

        # ASR 未识别到文字时，按时间比例切分原文作为 fallback
        if not text1 and not text2:
            ratio = (split_time - sentence.start_time) / (sentence.end_time - sentence.start_time)
            first_text, second_text = _split_text_by_ratio(sentence.text, ratio)

        # 清理临时文件
        _safe_remove_file(str(clip1))
        _safe_remove_file(str(clip2))
    else:
        # 无原始音频，按时间比例切分原文
        ratio = (split_time - sentence.start_time) / (sentence.end_time - sentence.start_time)
        first_text, second_text = _split_text_by_ratio(sentence.text, ratio)

    # 3. 保存撤回快照 + 后移所有 sort_order > 当前句子的句子
    await _save_undo_snapshot(project_id, session)
    await session.execute(
        sql_update(Sentence)
        .where(Sentence.project_id == project_id, Sentence.sort_order > sentence.sort_order)
        .values(sort_order=Sentence.sort_order + 1)
    )

    # 4. 创建后半句
    import uuid
    second_half_id = uuid.uuid4().hex[:12]
    second_half = Sentence(
        id=second_half_id,
        project_id=project_id,
        speaker_id=sentence.speaker_id,
        text=second_text,
        start_time=split_time,
        end_time=sentence.end_time,
        emotion_happy=sentence.emotion_happy,
        emotion_angry=sentence.emotion_angry,
        emotion_sad=sentence.emotion_sad,
        emotion_fear=sentence.emotion_fear,
        emotion_hate=sentence.emotion_hate,
        emotion_low=sentence.emotion_low,
        emotion_surprise=sentence.emotion_surprise,
        emotion_neutral=sentence.emotion_neutral,
        sort_order=sentence.sort_order + 1,
    )
    session.add(second_half)

    # 5. 原句子变为前半句
    sentence.end_time = split_time
    sentence.text = first_text

    await session.commit()

    return {
        "message": "已切割",
        "sentence_id": sentence.id,
        "new_sentence_id": second_half_id,
        "first_text": first_text,
        "second_text": second_text,
    }


@router.post("/add-from-region")
async def add_sentence_from_region(
    project_id: str,
    body: RegionAddRequest,
    session: AsyncSession = Depends(get_session),
):
    """从框选的音频区域识别文字，插入为新句子（保持台词列表时间顺序）。"""
    from sqlalchemy import update as sql_update
    from services.audio_service import extract_sentence_audio

    proj_result = await session.execute(
        select(Project).options(selectinload(Project.sentences))
        .where(Project.id == project_id)
    )
    project = proj_result.scalar_one_or_none()
    if not project or not project.original_audio:
        raise HTTPException(status_code=404, detail="原始音频不存在")

    start_time = body.start_time
    end_time = body.end_time
    if start_time < 0 or end_time <= start_time:
        raise HTTPException(status_code=400, detail="无效的时间范围")

    # 1. 提取音频片段
    clip_dir = settings.data_dir / "output" / project_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    clip_name = uuid.uuid4().hex[:12]
    clip_path = clip_dir / f"region_{clip_name}.wav"

    extract_sentence_audio(project.original_audio, start_time, end_time, str(clip_path))

    # 2. 同步运行 ASR 识别
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, _recognize_region_sync, str(clip_path))
    _safe_remove_file(str(clip_path))

    asr_used = bool(text)
    if not text:
        text = "[手动输入文字]"

    # 3. 确定 sort_order（按时间顺序插入到正确位置）
    sentences = sorted(project.sentences, key=lambda s: s.sort_order)

    # 找到 start_time 之后的第一句，插入到它前面
    insert_sort = len(sentences)
    for i, s in enumerate(sentences):
        if s.start_time > start_time:
            insert_sort = s.sort_order
            break

    # 保存撤回快照
    await _save_undo_snapshot(project_id, session)

    if insert_sort < len(sentences):
        # 后移后续句子的 sort_order
        await session.execute(
            sql_update(Sentence)
            .where(Sentence.project_id == project_id, Sentence.sort_order >= insert_sort)
            .values(sort_order=Sentence.sort_order + 1)
        )

    # 4. 自动匹配最近的说话人
    best_speaker_id = None
    best_gap = float("inf")
    for s in project.sentences:
        if not s.speaker_id:
            continue
        # 计算到新句子中点的距离
        mid = (start_time + end_time) / 2
        s_mid = (s.start_time + s.end_time) / 2
        gap = abs(mid - s_mid)
        if gap < best_gap:
            best_gap = gap
            best_speaker_id = s.speaker_id

    # 5. 创建新句子
    new_id = uuid.uuid4().hex[:12]
    new_sentence = Sentence(
        id=new_id,
        project_id=project_id,
        speaker_id=best_speaker_id,
        text=text,
        start_time=start_time,
        end_time=end_time,
        sort_order=insert_sort,
    )
    session.add(new_sentence)
    await session.commit()

    return {
        "message": "已识别并插入" if asr_used else "已插入（未识别到语音，请手动编辑文字）",
        "sentence_id": new_id,
        "text": text,
        "start_time": start_time,
        "end_time": end_time,
        "sort_order": insert_sort,
        "asr_used": asr_used,
    }


@router.post("/undo")
async def undo_last_action(
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    """撤回上一次操作（删除/切割/框选识别）。"""
    restored = await _restore_last_snapshot(project_id, session)
    if not restored:
        raise HTTPException(status_code=400, detail="没有可撤回的操作")
    await session.commit()
    return {"message": "已撤回"}


@router.post("/{sentence_id}/generate")
async def generate_single(
    project_id: str,
    sentence_id: str,
    session: AsyncSession = Depends(get_session),
):
    """生成单句 TTS。

    后台任务尝试调用 RunningHub API：
    - 成功 → 立即开始生成
    - 并发/频率超限 → 自动重试（每 5 秒一次）
    - 永久错误 → 标记失败
    """
    result = await session.execute(
        select(Sentence)
        .options(selectinload(Sentence.speaker))
        .where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence:
        raise HTTPException(status_code=404, detail="句子不存在")
    if not sentence.speaker or not sentence.speaker.reference_audio:
        raise HTTPException(status_code=400, detail="该句子缺少参考音频，请确保 ASR 处理已完成")

    sentence.tts_status = "generating"
    await session.commit()

    text = sentence.text
    ref_audio = sentence.speaker.reference_audio
    emotions = {k: getattr(sentence, f"emotion_{k}") for k in
                ["happy", "angry", "sad", "fear", "hate", "low", "surprise", "neutral"]}

    print(f"[api] creating async task: sentence={sentence_id}")
    asyncio.create_task(_generate_tts_task(project_id, sentence_id, text, ref_audio, emotions))

    return {"message": "开始生成", "sentence_id": sentence_id}


@router.post("/generate-all")
async def generate_all(
    project_id: str,
    body: TTSGenerateRequest,
    session: AsyncSession = Depends(get_session),
):
    """批量生成 TTS（逐句排队，Semaphore 保证同时最多 1 个 RunningHub 任务）。"""
    if not body.sentence_ids:
        raise HTTPException(status_code=400, detail="未选择句子")

    result = await session.execute(
        select(Sentence)
        .options(selectinload(Sentence.speaker))
        .where(Sentence.project_id == project_id, Sentence.id.in_(body.sentence_ids))
    )
    sentences = result.scalars().all()

    if not sentences:
        raise HTTPException(status_code=400, detail="未找到有效句子")

    for s in sentences:
        if not s.speaker or not s.speaker.reference_audio:
            raise HTTPException(status_code=400, detail=f"句子 {s.sort_order + 1} 缺少参考音频")
        s.tts_status = "generating"

    await session.commit()

    # 每个句子独立后台任务（自动重试 + RunningHub 自然限流）
    for s in sentences:
        emotions = {k: getattr(s, f"emotion_{k}") for k in
                    ["happy", "angry", "sad", "fear", "hate", "low", "surprise", "neutral"]}
        print(f"[api] creating async task: sentence={s.id}")
        asyncio.create_task(_generate_tts_task(
            project_id, s.id, s.text, s.speaker.reference_audio, emotions
        ))

    return {"message": f"开始生成 {len(sentences)} 句", "count": len(sentences)}


@router.get("/{sentence_id}/audio")
async def get_sentence_audio(
    project_id: str, sentence_id: str, session: AsyncSession = Depends(get_session)
):
    """获取生成的 TTS 音频。"""
    result = await session.execute(
        select(Sentence).where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence or not sentence.generated_audio:
        raise HTTPException(status_code=404, detail="音频不存在")
    return FileResponse(sentence.generated_audio, media_type="audio/wav")


@router.get("/{sentence_id}/original")
async def get_original_audio(
    project_id: str, sentence_id: str, session: AsyncSession = Depends(get_session)
):
    """获取原始音频片段。"""
    result = await session.execute(
        select(Sentence).where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence:
        raise HTTPException(status_code=404, detail="句子不存在")

    # 从原始音频截取
    proj_result = await session.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    if not project or not project.original_audio:
        raise HTTPException(status_code=404, detail="原始音频不存在")

    clip_path = settings.data_dir / "output" / project_id / f"{sentence_id}_original.wav"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    extract_sentence_audio(project.original_audio, sentence.start_time, sentence.end_time, str(clip_path))
    return FileResponse(str(clip_path), media_type="audio/wav")


async def _generate_tts_task(project_id: str, sentence_id: str, text: str,
                              ref_audio_path: str, emotions: dict):
    """TTS 生成任务。

    参考音频上传一次，提交遇到并发限制自动等待重试（排队效果），其他错误标记失败。
    多个句子同时点击：各自在提交阶段排队，第一个成功提交的立即执行，后续的等待重试。
    """
    from database import async_session
    from sqlalchemy import update as sql_update

    print(f"[tts] task start: sentence={sentence_id}")
    async with async_session() as session:
        output_dir = settings.data_dir / "output" / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{sentence_id}.wav"

        try:
            rh = _get_runninghub_service()
            success, is_retryable = await rh.generate_and_download(
                text=text,
                reference_audio_path=ref_audio_path,
                emotions=emotions,
                output_path=str(output_path),
            )

            if success:
                await session.execute(
                    sql_update(Sentence)
                    .where(Sentence.id == sentence_id)
                    .values(tts_status="done", generated_audio=str(output_path))
                )
            else:
                await session.execute(
                    sql_update(Sentence)
                    .where(Sentence.id == sentence_id)
                    .values(tts_status="failed")
                )
        except Exception as e:
            print(f"[tts] error: {e}")
            await session.execute(
                sql_update(Sentence)
                .where(Sentence.id == sentence_id)
                .values(tts_status="failed")
            )
        await session.commit()
        print(f"[tts] task end: sentence={sentence_id}")


def _safe_remove_file(path: str):
    """安全删除文件，忽略不存在的文件。"""
    from pathlib import Path
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _split_asr_sync(clip1: str, clip2: str) -> tuple:
    """同步函数：一次加载 FunASR 模型，识别两个音频片段，返回 (text1, text2)。"""
    try:
        from funasr import AutoModel

        print(f"[split-asr] loading FunASR paraformer model...")
        model = AutoModel(
            model="iic/speech_paraformer-large_asr_nat-zh-cn",
            disable_update=True,
            device="cpu",
        )
        print(f"[split-asr] model loaded, recognizing...")

        def _recognize(audio_path: str) -> str:
            try:
                result = model.generate(input=audio_path)
                if result and len(result) > 0:
                    item = result[0]
                    if isinstance(item, dict):
                        return item.get("text", "").strip()
                    return str(item).strip()
            except Exception as e:
                print(f"[split-asr] recognize error: {e}")
            return ""

        text1 = _recognize(clip1)
        text2 = _recognize(clip2)
        print(f"[split-asr] result: '{text1}' | '{text2}'")
        return text1, text2

    except Exception as e:
        print(f"[split-asr] model load error: {e}")
        return "", ""


def _split_text_by_ratio(text: str, ratio: float) -> tuple:
    """按时间比例切分文字，尽量在中日文标点/空格处断开。

    ratio 为前半段占比（0~1），返回 (first_half, second_half)。
    """
    if not text or len(text) <= 1:
        return text, text

    # 目标切割位置
    target = max(1, min(len(text) - 1, int(len(text) * ratio)))

    # 在 target 附近寻找最佳断点（优先标点符号之后）
    break_chars = set("，。、；：？！…—·,.;:!? \t\n\r")
    search_window = max(3, len(text) // 6)

    best = target
    # 向右搜索（优先在 target 稍后的位置断开）
    for offset in range(0, search_window):
        pos = target + offset
        if pos < len(text) and text[pos] in break_chars:
            best = pos + 1  # 标点归属前半句
            break
        # 也检查左侧
        pos = target - offset
        if offset > 0 and pos > 0 and text[pos] in break_chars:
            best = pos + 1
            break

    # 确保 best 在合理范围
    best = max(1, min(len(text) - 1, best))

    return text[:best].strip(), text[best:].strip()


def _recognize_region_sync(clip: str) -> str:
    """同步函数：加载 FunASR 综合模型（与主流程一致，确保模型已缓存），识别单个音频片段。"""
    try:
        from funasr import AutoModel

        print(f"[region-asr] loading FunASR model (same as main pipeline)...")
        model = AutoModel(
            model="iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn",
            disable_update=True,
            device="cpu",
        )
        print(f"[region-asr] model loaded, recognizing...")
        result = model.generate(input=clip)
        if result and len(result) > 0:
            item = result[0]
            if isinstance(item, dict):
                text = item.get("text", "").strip()
            else:
                text = str(item).strip()
            # 去掉空格（中文分词产生的）
            text = text.replace(" ", "")
            print(f"[region-asr] result: '{text}'")
            return text
    except Exception as e:
        import traceback
        print(f"[region-asr] error: {e}")
        traceback.print_exc()
    return ""
