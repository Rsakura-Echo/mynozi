"""Pydantic 请求/响应模型。"""

from pydantic import BaseModel, Field
from typing import Optional


# ─── Project ───
class ProjectCreate(BaseModel):
    name: str = "未命名项目"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    status: str
    duration: Optional[float] = None
    last_error: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


class ProjectDetail(ProjectResponse):
    speakers: list["SpeakerResponse"] = []
    sentences: list["SentenceResponse"] = []


# ─── Speaker ───
class SpeakerResponse(BaseModel):
    id: str
    name: str
    reference_audio: Optional[str] = None

    model_config = {"from_attributes": True}


class SpeakerUpdate(BaseModel):
    name: Optional[str] = None


# ─── Sentence ───
class SentenceResponse(BaseModel):
    id: str
    speaker_id: Optional[str] = None
    speaker_name: Optional[str] = ""
    text: str
    start_time: float
    end_time: float
    emotion_happy: int
    emotion_angry: int
    emotion_sad: int
    emotion_fear: int
    emotion_hate: int
    emotion_low: int
    emotion_surprise: int
    emotion_neutral: int
    tts_status: str
    generated_audio: Optional[str] = None
    is_deleted: bool = False
    sort_order: int

    model_config = {"from_attributes": True}


class SentenceUpdate(BaseModel):
    text: Optional[str] = None
    speaker_id: Optional[str] = None
    emotion_happy: Optional[int] = None
    emotion_angry: Optional[int] = None
    emotion_sad: Optional[int] = None
    emotion_fear: Optional[int] = None
    emotion_hate: Optional[int] = None
    emotion_low: Optional[int] = None
    emotion_surprise: Optional[int] = None
    emotion_neutral: Optional[int] = None


# ─── Sentence split ───
class SentenceSplitRequest(BaseModel):
    split_time: float  # 切割时间点（秒）


class RegionAddRequest(BaseModel):
    start_time: float
    end_time: float


# ─── TTS ───
class TTSGenerateRequest(BaseModel):
    sentence_ids: list[str]


# ─── Generic ───
class MessageResponse(BaseModel):
    message: str
