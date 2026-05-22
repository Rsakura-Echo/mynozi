"""ComfyUI API 客户端 — 动态构建工作流并从云端生成 TTS。"""

import json
import time
import random
from pathlib import Path
from typing import Optional

import httpx

from config import settings


class ComfyUIService:
    """封装 ComfyUI / RunningHub API：上传参考音频 → 提交工作流 → 轮询 → 下载。"""

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._workflow_template: Optional[dict] = None

    def _headers(self) -> dict:
        """构建请求头，如果配置了 api_key 则加入 Bearer 鉴权。"""
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _load_template(self) -> dict:
        if self._workflow_template is None:
            template_path = Path(__file__).resolve().parent.parent.parent / "rhapi" / "indexTTS2 最强语音克隆支持多人 10人对话_api.json"
            if template_path.exists():
                with open(template_path, "r", encoding="utf-8") as f:
                    self._workflow_template = json.load(f)
            else:
                raise FileNotFoundError(f"ComfyUI workflow template not found: {template_path}")
        return dict(self._workflow_template)

    async def upload_reference_audio(self, audio_path: str) -> str:
        """上传参考音频到 ComfyUI，返回服务器端的文件名。"""
        url = f"{self.base_url}/upload/image"
        async with httpx.AsyncClient(timeout=settings.comfyui_timeout) as client:
            with open(audio_path, "rb") as f:
                files = {"image": (Path(audio_path).name, f, "audio/wav")}
                resp = await client.post(url, files=files, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            return data.get("name", "")

    async def _submit_workflow(self, workflow: dict) -> str:
        """提交工作流，返回 prompt_id。"""
        url = f"{self.base_url}/prompt"
        payload = {"prompt": workflow, "client_id": "mynozi"}
        async with httpx.AsyncClient(timeout=settings.comfyui_timeout) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            prompt_id = data.get("prompt_id", "")
            if not prompt_id:
                raise RuntimeError(f"ComfyUI 返回异常: {data}")
            return prompt_id

    async def _wait_for_result(self, prompt_id: str) -> Optional[dict]:
        """轮询历史记录，直到任务完成。"""
        url = f"{self.base_url}/history/{prompt_id}"
        async with httpx.AsyncClient(timeout=settings.comfyui_timeout) as client:
            start = time.time()
            while time.time() - start < settings.comfyui_timeout:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
                if prompt_id in data:
                    return data[prompt_id]
                await self._sleep(settings.comfyui_poll_interval)
        return None

    async def _download_output(self, filename: str, subfolder: str, output_type: str, save_path: str) -> bool:
        """下载生成结果。"""
        params = {"filename": filename, "subfolder": subfolder, "type": output_type}
        url = f"{self.base_url}/view"
        async with httpx.AsyncClient(timeout=settings.comfyui_timeout) as client:
            resp = await client.get(url, params=params, headers=self._headers())
            resp.raise_for_status()
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(resp.content)
        return True

    async def _sleep(self, seconds: float):
        """异步等待，避免 time.sleep 阻塞。"""
        import asyncio
        await asyncio.sleep(seconds)

    async def generate_and_download(
        self,
        text: str,
        reference_audio_path: str,
        emotions: dict,
        output_path: str,
    ) -> bool:
        """一站式 TTS 生成：上传参考音频 → 构建工作流 → 提交 → 等待 → 下载。

        Args:
            text: 要合成的文本
            reference_audio_path: 本地参考音频路径
            emotions: {"happy": 0, "angry": 0, ...} 8 个情感值 0-100
            output_path: 输出音频保存路径

        Returns:
            是否成功
        """
        try:
            # 1. 上传参考音频
            ref_filename = await self.upload_reference_audio(reference_audio_path)

            # 2. 构建工作流
            workflow = self._load_template()
            seed = random.randint(0, 2 ** 31 - 1)

            # 替换动态参数
            for node_id, node in workflow.items():
                class_type = node.get("class_type", "")

                if class_type == "PrimitiveStringMultiline":
                    # 节点 87: 文本输入
                    node["inputs"]["value"] = text

                elif class_type == "LoadAudio":
                    # 节点 159: 参考音频
                    node["inputs"]["audio"] = ref_filename

                elif class_type == "easy indexTTSEmotionVector":
                    # 节点 100: 情感向量
                    emotion_map = {
                        "Happy": "happy", "Angry": "angry", "Sad": "sad",
                        "Fear": "fear", "Hate": "hate", "Low": "low",
                        "Surprise": "surprise", "Neutral": "neutral"
                    }
                    for comfy_key, our_key in emotion_map.items():
                        node["inputs"][comfy_key] = emotions.get(our_key, 0)

                elif class_type == "easy indexTTSGenerateSimple":
                    # 节点 152: 种子
                    node["inputs"]["seed"] = seed

            # 3. 提交工作流
            prompt_id = await self._submit_workflow(workflow)

            # 4. 等待结果
            history = await self._wait_for_result(prompt_id)
            if not history:
                print(f"[comfyui] Timeout for prompt_id={prompt_id}")
                return False

            # 5. 解析输出
            outputs = history.get("outputs", {})
            for node_id, node_output in outputs.items():
                images = node_output.get("images", [])
                if images:
                    img = images[0]
                    return await self._download_output(
                        filename=img["filename"],
                        subfolder=img.get("subfolder", ""),
                        output_type=img.get("type", "output"),
                        save_path=output_path,
                    )

            print(f"[comfyui] No audio output found in history for prompt_id={prompt_id}")
            return False

        except Exception as e:
            print(f"[comfyui] generate_and_download error: {e}")
            return False
