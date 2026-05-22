"""上传 + ASR 处理 API。"""

import hashlib
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy import select, delete as sql_delete, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import Project, Speaker, Sentence
from config import settings
from routers.settings import check_models_cached
from services.asr_service import process_audio_with_asr
from services.funasr_service import process_audio_with_funasr

router = APIRouter(prefix="/api/projects/{project_id}", tags=["upload"])


def _compute_hash(content: bytes) -> str:
    """计算文件 SHA256 哈希。"""
    return hashlib.sha256(content).hexdigest()


def _get_asr_model() -> str:
    """读取用户选择的 ASR 引擎。"""
    settings_file = settings.data_dir / "settings.json"
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            return data.get("asr_model", "funasr")
        except Exception:
            pass
    return "funasr"


async def _copy_from_cache(
    src_project_id: str, dst_project_id: str, audio_path: str, hash_value: str
):
    """从缓存项目复制 speakers 和 sentences 到新项目。"""
    from database import async_session

    async with async_session() as session:
        # 查询源项目的 speakers 和 sentences
        src_speakers = (await session.execute(
            select(Speaker).where(Speaker.project_id == src_project_id)
        )).scalars().all()

        src_sentences = (await session.execute(
            select(Sentence).where(
                Sentence.project_id == src_project_id,
                Sentence.is_deleted == False,
            ).order_by(Sentence.sort_order)
        )).scalars().all()

        # 映射 old_speaker_id → new_speaker_id
        speaker_id_map = {}
        for spk in src_speakers:
            new_spk = Speaker(
                project_id=dst_project_id,
                name=spk.name,
                reference_audio=spk.reference_audio,
            )
            session.add(new_spk)
            await session.flush()
            speaker_id_map[spk.id] = new_spk.id

        # 复制句子
        for i, sent in enumerate(src_sentences):
            new_sent = Sentence(
                project_id=dst_project_id,
                speaker_id=speaker_id_map.get(sent.speaker_id),
                text=sent.text,
                start_time=sent.start_time,
                end_time=sent.end_time,
                emotion_happy=sent.emotion_happy,
                emotion_angry=sent.emotion_angry,
                emotion_sad=sent.emotion_sad,
                emotion_fear=sent.emotion_fear,
                emotion_hate=sent.emotion_hate,
                emotion_low=sent.emotion_low,
                emotion_surprise=sent.emotion_surprise,
                emotion_neutral=sent.emotion_neutral,
                sort_order=i,
            )
            session.add(new_sent)

        # 更新目标项目
        dst_proj = (await session.execute(
            select(Project).where(Project.id == dst_project_id)
        )).scalar_one_or_none()
        if dst_proj:
            dst_proj.file_hash = hash_value
            dst_proj.original_audio = audio_path
            # duration 从最后一句的 end_time 获取
            if src_sentences:
                dst_proj.duration = src_sentences[-1].end_time
            dst_proj.status = "ready"

        await session.commit()
        print(f"[cache] Copied {len(src_sentences)} sentences from project {src_project_id}")


@router.post("/upload")
async def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    session: AsyncSession = Depends(get_session),
):
    """上传音视频文件，后台触发 ASR 处理。

    如果检测到相同音频已处理过（SHA256 匹配），直接复用缓存结果，跳过模型推理。
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 验证文件大小
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_size_mb}MB 限制")

    # 计算文件哈希
    file_hash = _compute_hash(content)

    # 保存原始文件
    upload_dir = settings.data_dir / "uploads" / project_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    src_path = upload_dir / (file.filename or "upload")
    with open(src_path, "wb") as f:
        f.write(content)

    # 如果是重新上传，先清理旧的 speakers 和 sentences
    await session.execute(sql_delete(Sentence).where(Sentence.project_id == project_id))
    await session.execute(sql_delete(Speaker).where(Speaker.project_id == project_id))

    # 清理旧的输出文件
    output_dir = settings.data_dir / "output" / project_id
    if output_dir.exists():
        shutil.rmtree(str(output_dir))
    ref_dir = settings.data_dir / "references" / project_id
    if ref_dir.exists():
        shutil.rmtree(str(ref_dir))

    # 设置音频路径
    audio_path = str(src_path)

    # 检查缓存：是否有其他项目处理过相同文件
    cache_result = await session.execute(
        select(Project).where(
            Project.file_hash == file_hash,
            Project.status == "ready",
            Project.id != project_id,
        )
    )
    cached_project = cache_result.scalar_one_or_none()

    if cached_project:
        # 缓存命中 — 直接复用结果
        print(f"[cache] Hit! Reusing results from project {cached_project.id}")
        project.original_file = str(src_path)
        project.original_audio = audio_path
        project.status = "ready"
        project.file_hash = file_hash
        await session.commit()

        background_tasks.add_task(
            _copy_from_cache, cached_project.id, project_id, audio_path, file_hash
        )
        return {"message": "检测到相同音频，已加载缓存结果", "project_id": project_id, "cached": True}

    # 缓存未命中 — 检查模型是否已下载
    asr_model = _get_asr_model()
    model_ready, model_error = check_models_cached(asr_model)
    if not model_ready:
        raise HTTPException(
            status_code=409,
            detail={"message": model_error, "code": "model_not_cached", "engine": asr_model},
        )

    # 正常 ASR 处理
    project.original_file = str(src_path)
    project.file_hash = file_hash
    project.status = "processing"
    project.last_error = None
    await session.commit()

    if asr_model == "funasr":
        background_tasks.add_task(process_audio_with_funasr, project_id, str(src_path), file_hash)
    else:
        background_tasks.add_task(process_audio_with_asr, project_id, str(src_path), file_hash)

    return {"message": "上传成功，开始 AI 分析", "project_id": project_id, "cached": False}


@router.get("/status")
async def get_processing_status(project_id: str, session: AsyncSession = Depends(get_session)):
    """查询 ASR 处理进度。"""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"status": project.status}
