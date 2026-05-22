"""SQLAlchemy ORM 模型 — 项目 / 说话人 / 句子。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False, default="未命名项目")
    original_file = Column(String, nullable=True)
    original_audio = Column(String, nullable=True)
    duration = Column(Float, nullable=True)
    status = Column(String, default="uploading")  # uploading|processing|ready|error
    file_hash = Column(String, nullable=True)  # 上传文件的 SHA256，用于缓存检测
    last_error = Column(Text, nullable=True)  # 最近一次错误信息（供前端展示）
    created_at = Column(String, default=_now)

    speakers = relationship("Speaker", back_populates="project", cascade="all, delete-orphan")
    sentences = relationship("Sentence", back_populates="project", cascade="all, delete-orphan")


class Speaker(Base):
    __tablename__ = "speakers"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    name = Column(String, nullable=False, default="未知说话人")
    reference_audio = Column(String, nullable=True)
    created_at = Column(String, default=_now)

    project = relationship("Project", back_populates="speakers")
    sentences = relationship("Sentence", back_populates="speaker")


class Sentence(Base):
    __tablename__ = "sentences"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    speaker_id = Column(String, ForeignKey("speakers.id"), nullable=True)

    text = Column(Text, nullable=False)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)

    # 情感参数 0-100
    emotion_happy = Column(Integer, default=0)
    emotion_angry = Column(Integer, default=0)
    emotion_sad = Column(Integer, default=0)
    emotion_fear = Column(Integer, default=0)
    emotion_hate = Column(Integer, default=0)
    emotion_low = Column(Integer, default=0)
    emotion_surprise = Column(Integer, default=0)
    emotion_neutral = Column(Integer, default=50)

    generated_audio = Column(String, nullable=True)
    tts_status = Column(String, default="pending")  # pending|generating|done|failed
    is_deleted = Column(Boolean, default=False)  # 软删除标记
    sort_order = Column(Integer, default=0)
    created_at = Column(String, default=_now)

    project = relationship("Project", back_populates="sentences")
    speaker = relationship("Speaker", back_populates="sentences")
