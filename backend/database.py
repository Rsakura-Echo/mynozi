"""SQLAlchemy async engine + session。切 PostgreSQL 只需改 config.database_url。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import settings

engine = create_async_engine(settings.db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    """创建所有表 + 迁移已有表。首次运行时自动调用。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 迁移：为已有表添加新列
    async with engine.connect() as conn:
        migrations = [
            "ALTER TABLE sentences ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE",
            "ALTER TABLE projects ADD COLUMN file_hash VARCHAR",
        ]
        for migration in migrations:
            try:
                await conn.execute(text(migration))
            except Exception:
                pass  # 列已存在则忽略
        await conn.commit()


async def get_session() -> AsyncSession:
    """FastAPI 依赖注入用。"""
    async with async_session() as session:
        yield session
