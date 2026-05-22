"""mynozi 配置中心。所有可配置项集中管理，后续可切环境变量。"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 应用
    app_name: str = "mynozi"
    app_version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000

    # 数据目录
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"

    # SQLite（一行配置切 PostgreSQL）
    database_url: str = ""

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = self.data_dir / "mynozi.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    # RunningHub 云端 API
    runninghub_api_key: str = ""
    runninghub_workflow_id: str = ""
    runninghub_timeout: int = 600  # 单次生成超时(秒)
    runninghub_poll_interval: float = 5.0  # 轮询间隔(秒)

    # ASR (WhisperX)
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"  # auto / cpu / cuda / mps（mps 仅 macOS）
    whisper_compute_type: str = "auto"  # auto / float16 / float32 / int8
    hf_token: str = ""  # HuggingFace token（pyannote diarization 需要）

    # 上传限制
    max_upload_size_mb: int = 2000  # 2GB

    # 未来扩展
    task_queue_type: str = "memory"  # memory / redis

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "allow"  # 允许 .env 中的 HF_ENDPOINT 等环境变量


settings = Settings()
