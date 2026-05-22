"""导出 API。"""

import zipfile
import io
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import Project, Sentence
from config import settings

router = APIRouter(prefix="/api/projects/{project_id}/export", tags=["export"])


@router.get("/{sentence_id}")
async def export_single(
    project_id: str, sentence_id: str, session: AsyncSession = Depends(get_session)
):
    """导出单句配音。"""
    result = await session.execute(
        select(Sentence).where(Sentence.id == sentence_id, Sentence.project_id == project_id)
    )
    sentence = result.scalar_one_or_none()
    if not sentence or not sentence.generated_audio:
        raise HTTPException(status_code=404, detail="配音音频不存在，请先生成")

    path = Path(sentence.generated_audio)
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件丢失，请重新生成")

    filename = f"sentence_{sentence.sort_order + 1:02d}.wav"
    return FileResponse(str(path), media_type="audio/wav", filename=filename)


@router.get("/all")
async def export_all(project_id: str, session: AsyncSession = Depends(get_session)):
    """打包导出全部已完成的配音。"""
    result = await session.execute(
        select(Sentence)
        .where(Sentence.project_id == project_id, Sentence.tts_status == "done")
        .order_by(Sentence.sort_order)
    )
    sentences = result.scalars().all()

    if not sentences:
        raise HTTPException(status_code=404, detail="没有已完成的配音")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in sentences:
            path = Path(s.generated_audio) if s.generated_audio else None
            if path and path.exists():
                speaker_name = s.speaker.name if s.speaker else "unknown"
                filename = f"{s.sort_order + 1:02d}_{speaker_name}_{s.text[:20]}.wav"
                zf.write(path, filename)

    zip_buffer.seek(0)

    # 同时查项目名称
    proj_result = await session.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    zip_name = f"{project.name}_配音导出.zip" if project else "mynozi_export.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'}
    )
