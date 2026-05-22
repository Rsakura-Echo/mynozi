# mynozi — 智能配音工坊

上传音视频 → AI 自动切分句子 → 编辑文本 → 批量配音 → 导出。

## 前置依赖

| 依赖 | Windows | macOS |
|------|---------|-------|
| Python 3.10-3.12（推荐） | [python.org](https://python.org) | `brew install python` |
> Python 3.13/3.14+ 也支持，但首次安装 PyTorch 较慢（需 nightly 版 ~2.7GB），推荐用 3.12 加速。


| Node.js 20+ | [nodejs.org](https://nodejs.org) | `brew install node` |
| ffmpeg | `winget install ffmpeg` 或 [ffmpeg.org](https://ffmpeg.org) | `brew install ffmpeg` |

## 快速启动

### Windows
```
双击 start.bat
```

### macOS
```
bash start.sh
```

首次启动会自动：创建虚拟环境 → 安装依赖 → 构建前端 → 启动服务。

浏览器打开 `http://localhost:8000`，同一 WiFi 下其他设备可通过 `http://你的电脑IP:8000` 访问。

## 配置

在 `backend/.env` 或系统环境变量中设置：

```env
# ComfyUI 云端 API 地址
comfyui_base_url=http://your-comfyui-server:8188

# HuggingFace Token（说话人分离功能需要）
hf_token=hf_your_token_here
```

不设 `hf_token` 时，会跳过说话人分离，所有句子归到同一说话人。

## 目录结构

```
mynozi/
├── backend/          # FastAPI 后端
├── frontend/         # React 前端
├── data/             # 运行时数据（首次运行自动创建）
│   ├── mynozi.db     # SQLite 数据库
│   ├── uploads/      # 上传文件
│   ├── references/   # 说话人参考音频
│   └── output/       # 生成的配音
├── start.bat         # Windows 启动
├── start.sh          # macOS 启动
└── rhapi/            # ComfyUI 工作流模板
```
