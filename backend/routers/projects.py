"""项目管理 API。"""

import asyncio
import shutil
import wave
import struct

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_session
from models import Project
from schemas import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectDetail
from config import settings

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse)
async def create_project(body: ProjectCreate, session: AsyncSession = Depends(get_session)):
    project = Project(name=body.name)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("", response_model=list[ProjectResponse])
async def list_projects(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Project).order_by(Project.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Project)
        .options(selectinload(Project.speakers), selectinload(Project.sentences))
        .where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 给每个句子附加 speaker_name
    speaker_map = {s.id: s.name for s in project.speakers}
    sentences = sorted(project.sentences, key=lambda s: s.sort_order)
    detail = ProjectDetail(
        id=project.id,
        name=project.name,
        status=project.status,
        duration=project.duration,
        last_error=project.last_error,
        created_at=project.created_at,
        speakers=[{"id": s.id, "name": s.name, "reference_audio": s.reference_audio} for s in project.speakers],
        sentences=[
            {
                "id": s.id,
                "speaker_id": s.speaker_id,
                "speaker_name": speaker_map.get(s.speaker_id or "", ""),
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
                "tts_status": s.tts_status,
                "generated_audio": s.generated_audio,
                "is_deleted": s.is_deleted if hasattr(s, 'is_deleted') else False,
                "sort_order": s.sort_order,
            }
            for s in sentences
        ],
    )
    return detail


@router.put("/{project_id}")
async def rename_project(project_id: str, body: ProjectUpdate, session: AsyncSession = Depends(get_session)):
    """重命名项目。"""
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    await session.execute(
        sql_update(Project).where(Project.id == project_id).values(name=body.name.strip())
    )
    await session.commit()
    return {"message": "已重命名", "project_id": project_id}


@router.delete("/{project_id}")
async def delete_project(project_id: str, session: AsyncSession = Depends(get_session)):
    """删除项目及其所有关联文件。"""
    result = await session.execute(
        select(Project).options(selectinload(Project.sentences))
        .where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 1. 清理物理文件
    # 上传的原始文件
    if project.original_file:
        _safe_remove(project.original_file)
    if project.original_audio:
        _safe_remove(project.original_audio)

    # 生成的 TTS 音频
    for s in project.sentences:
        if s.generated_audio:
            _safe_remove(s.generated_audio)

    # 整个 output 目录（含切割片段、导出等）
    output_dir = settings.data_dir / "output" / project_id
    if output_dir.exists():
        shutil.rmtree(str(output_dir))

    # 参考音频目录（ASR 提取的 speaker reference）
    ref_dir = settings.data_dir / "reference" / project_id
    if ref_dir.exists():
        shutil.rmtree(str(ref_dir))

    # 2. 删除数据库记录（cascade 自动删除 speakers 和 sentences）
    await session.delete(project)
    await session.commit()
    return {"message": "已删除"}


@router.get("/{project_id}/waveform")
async def get_waveform(project_id: str, session: AsyncSession = Depends(get_session)):
    """返回音频波形振幅数据（供前端波形图使用）。"""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project or not project.original_audio:
        raise HTTPException(status_code=404, detail="音频不存在")

    peaks = await asyncio.get_event_loop().run_in_executor(
        None, _compute_waveform_peaks, project.original_audio
    )
    return {"peaks": peaks, "duration": project.duration}


def _compute_waveform_peaks(audio_path: str, num_bars: int = 400) -> list[float]:
    """读取 WAV 文件，计算每个时间窗口的振幅峰值（0~1）。"""
    try:
        with wave.open(audio_path, 'r') as wf:
            n_frames = wf.getnframes()
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()

            if n_frames == 0:
                return []

            frames = wf.readframes(n_frames)

            # 解析采样点
            if sample_width == 2:
                fmt = f'<{n_frames * n_channels}h'
                max_val = 32767
            elif sample_width == 4:
                fmt = f'<{n_frames * n_channels}i'
                max_val = 2147483647
            elif sample_width == 1:
                fmt = f'<{n_frames * n_channels}B'
                max_val = 255
            else:
                return []

            samples = struct.unpack(fmt, frames)

            # 转单声道
            if n_channels >= 2:
                mono = [
                    sum(samples[i * n_channels:(i + 1) * n_channels]) / n_channels
                    for i in range(n_frames)
                ]
            else:
                mono = list(samples)

            # 计算每个窗口的峰值
            window_size = max(1, len(mono) // num_bars)
            peaks = []
            for i in range(num_bars):
                start = i * window_size
                end = min(start + window_size, len(mono))
                window = mono[start:end]
                if window:
                    peak = max(abs(s) for s in window) / max_val
                    # 轻微压缩动态范围，让小声段也能看到
                    peak = min(1.0, peak * 1.5)
                    peaks.append(round(peak, 4))
                else:
                    peaks.append(0.0)

            return peaks
    except Exception as e:
        print(f"[waveform] error reading audio: {e}")
        return []


def _safe_remove(path: str):
    """安全删除文件，忽略不存在的文件。"""
    from pathlib import Path
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
