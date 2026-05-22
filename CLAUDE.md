# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

mynozi（智能配音工坊）— 上传音视频 → WhisperX AI 自动切分句子 + 说话人分离 → 编辑文本/情感参数 → 通过 ComfyUI (IndexTTS2) 批量配音 → 导出。

## 启动命令

```bash
# 一键启动（macOS）
bash start.sh

# 一键启动（Windows）
start.bat

# 开发模式（前后端分离）
# 后端 (macOS / Linux)
cd backend && source ../.venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# 后端 (Windows cmd)
cd backend && ..\.venv\Scripts\activate.bat && python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 前端（跨平台）
cd frontend && npm run dev        # Vite dev server, port 5173, proxy /api → 8000
```

生产模式：`npm run build` 构建前端后，FastAPI 直接 serve `frontend/dist/`，访问 `http://localhost:8000`。

## 平台要求

| 依赖 | macOS | Windows | 说明 |
|------|-------|---------|------|
| Python 3.10+ | ✓ | ✓ | |
| Node.js 18+ | ✓ | ✓ | |
| ffmpeg | `brew install ffmpeg` | [下载](https://ffmpeg.org/download.html) 并加入 PATH | 音频处理必需 |
| CUDA | - | 可选，加速 WhisperX/FunASR | Windows GPU 需手动安装 PyTorch CUDA 版本 |

## 技术栈

| 层 | 技术 |
|---|------|
| 后端 | FastAPI (Python), SQLAlchemy 2.0 async + aiosqlite, pydantic v2 |
| 前端 | React 19, TypeScript 6, Vite 8, react-router-dom v7, wavesurfer.js |
| ASR | WhisperX (faster-whisper) / FunASR (阿里达摩院 paraformer) |
| TTS | ComfyUI 云端 API, IndexTTS2 工作流模板 (`rhapi/`) |
| 音频处理 | ffmpeg (提取/切割/合并) |

## 架构

### 后端分层

```
backend/
├── main.py           # FastAPI app, CORS, SPA fallback, lifespan
├── config.py         # pydantic-settings: ComfyUI URL, Whisper 参数, 上传限制
├── database.py       # SQLAlchemy async engine + session 工厂
├── models.py         # ORM: Project → Speaker → Sentence (一对多)
├── schemas.py        # Pydantic 请求/响应模型
├── routers/          # API 路由层（薄层，只做参数校验和调度）
│   ├── projects.py   # CRUD 项目
│   ├── upload.py     # 上传文件 + 触发后台 ASR
│   ├── sentences.py  # 更新句子/情感 + 触发 TTS 生成
│   ├── export.py     # 单句/批量导出
│   └── settings.py   # ASR 模型选择 + HuggingFace 缓存检测
└── services/         # 业务逻辑层（重计算，在线程池/后台任务中运行）
    ├── asr_service.py     # WhisperX: 转写 → 对齐 → 说话人分离 → 写入 DB
    ├── funasr_service.py  # FunASR: VAD → 转写 → CAM++ 说话人分离（ModelScope，国内友好）
    ├── audio_service.py   # ffmpeg: 视频提取音频/按时间戳切割/合并
    └── comfyui_service.py # ComfyUI API: 上传参考音频 → 构建工作流 → 轮询 → 下载
```

### 数据流

1. 用户创建项目 → `POST /api/projects`
2. 上传音视频 → `POST /api/projects/{id}/upload` → BackgroundTasks 触发 `asr_service.process_audio_with_asr()`
3. ASR 处理（`models.py:Project.status` = `processing`）：
   - 视频则先 `ffmpeg` 提取音频
   - WhisperX 加载模型（大小可配置，默认 medium）
   - 转写 + 时间戳对齐 + 可选说话人分离（需 `hf_token`）
   - 写入 Speaker + Sentence 记录，提取参考音频
   - 状态 → `ready`（或 `error` 并写入 `last_error`）
4. 用户编辑句子文本/情感参数 → `PUT /api/projects/{id}/sentences/{sid}`
5. TTS 生成 → `POST .../generate-all` → BackgroundTasks 逐个调用 `comfyui_service.generate_and_download()`
   - 上传参考音频到 ComfyUI → 替换工作流模板中的动态参数 → 提交 → 轮询历史 → 下载 wav
6. 导出 → `GET .../export/all` → StreamingResponse 输出 zip

### 前端路由

- `/` — ProjectList（项目卡片列表 + 新建弹窗）
- `/project/:id` — ProjectEditor（上传区 / 处理进度 / 波形图 + 句子表格 + 批量操作）

### 关键设计决策

- **数据目录** `data/` 包含 SQLite、上传文件、参考音频、生成输出，均由 `config.py` 的 `data_dir` 控制
- **ComfyUI 工作流模板**在 `rhapi/indexTTS2 最强语音克隆支持多人 10人对话_api.json`，`comfyui_service.py` 运行时动态替换文本/情感/参考音频/种子
- **情感参数**为 8 维向量（开心/愤怒/悲伤/恐惧/厌恶/低落/惊讶/中性），0-100，通过 IndexTTS2 的 EmotionVector 节点注入
- **ASR 模型大小**可在前端设置页选择（tiny ~ large-v3），通过 `data/settings.json` 持久化，支持 HuggingFace 缓存检测
- **数据库切换**：改 `config.py` 的 `database_url` 即可切 PostgreSQL（SQLAlchemy + aiosqlite → asyncpg）
- **前后端通信**：开发时 Vite proxy `/api` → `127.0.0.1:8000`；生产时 FastAPI 直接 serve 前端 dist + SPA fallback
