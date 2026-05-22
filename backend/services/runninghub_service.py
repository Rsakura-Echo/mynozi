"""RunningHub API 客户端 — 上传参考音频 → 提交工作流 → 轮询 → 下载 TTS 结果。"""

import json
import time
import random
import asyncio
from pathlib import Path
from typing import Optional

import httpx

from config import settings


class RunningHubService:
    """封装 RunningHub API：
    - 上传参考音频（/task/openapi/upload）
    - 提交任务（/task/openapi/create）
    - 轮询输出（/task/openapi/outputs）
    - 下载生成结果
    """

    API_HOST = "https://www.runninghub.cn"

    def __init__(self, api_key: str, workflow_id: str):
        self.api_key = api_key
        self.workflow_id = workflow_id

    # ── 上传参考音频 ──

    async def upload_file(self, file_path: str, file_type: str = "input") -> Optional[str]:
        """上传文件到 RunningHub，返回 fileName（失败返回 None）。"""
        url = f"{self.API_HOST}/task/openapi/upload"
        data = {"apiKey": self.api_key, "fileType": file_type}
        async with httpx.AsyncClient(timeout=settings.runninghub_timeout) as client:
            with open(file_path, "rb") as f:
                files = {"file": (Path(file_path).name, f)}
                resp = await client.post(url, data=data, files=files)
                resp.raise_for_status()
                result = resp.json()
        if result.get("msg") == "success":
            return result.get("data", {}).get("fileName")
        print(f"[runninghub] upload failed: {result}")
        return None

    # ── 提交任务 ──

    async def submit_task(self, node_info_list: list[dict]) -> tuple[Optional[str], bool]:
        """提交 TTS 任务。

        Returns:
            (taskId, is_retryable)
            - 成功: (taskId, False)
            - 并发/频率限制（应排队重试）: (None, True)
            - 永久错误（不应重试）: (None, False)
        """
        url = f"{self.API_HOST}/task/openapi/create"
        payload = {
            "apiKey": self.api_key,
            "workflowId": self.workflow_id,
            "nodeInfoList": node_info_list,
        }
        async with httpx.AsyncClient(timeout=settings.runninghub_timeout) as client:
            resp = await client.post(url, json=payload)

            if resp.status_code == 429:
                print(f"[runninghub] rate limited (429), will retry")
                return None, True

            resp.raise_for_status()
            result = resp.json()

        code = result.get("code", -1)

        # 可重试错误码（并发/频率/队列限制，应自动排队重试）
        RETRYABLE_CODES = {
            421,   # TASK_QUEUE_MAXED — 任务队列已满
            1003,  # Rate limit exceeded — 请求频率超限
            1011,  # Model is currently busy — 模型负载较高
            1520,  # Concurrency limit reached — 账号并发达到上限
        }

        if code == 0:
            pass
        elif code == 813:
            # 排队中 — RunningHub 内部队列
            print(f"[runninghub] task queued on RunningHub (code=813)")
        elif code in RETRYABLE_CODES:
            print(f"[runninghub] retryable error (code={code}): {result.get('msg')}")
            return None, True
        elif code in (805,):
            print(f"[runninghub] task permanent failure: {result}")
            return None, False
        else:
            print(f"[runninghub] submit failed (code={code}): {result}")
            return None, False

        # 检查节点错误
        prompt_tips_str = result.get("data", {}).get("promptTips")
        if prompt_tips_str:
            try:
                prompt_tips = json.loads(prompt_tips_str) if isinstance(prompt_tips_str, str) else prompt_tips_str
                node_errors = prompt_tips.get("node_errors", {})
                if node_errors:
                    print(f"[runninghub] node_errors: {node_errors}")
            except Exception:
                pass

        return result["data"]["taskId"], False

    # ── 轮询输出 ──

    async def query_outputs(self, task_id: str) -> dict:
        """查询任务输出，返回 API 原始响应。"""
        url = f"{self.API_HOST}/task/openapi/outputs"
        payload = {"apiKey": self.api_key, "taskId": task_id}
        async with httpx.AsyncClient(timeout=settings.runninghub_timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    # ── 下载结果 ──

    async def _download_file(self, file_url: str, save_path: str) -> bool:
        """下载生成的音频文件。"""
        async with httpx.AsyncClient(timeout=settings.runninghub_timeout) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(resp.content)
        return True

    # ── 一站式生成 ──

    async def generate_and_download(
        self,
        text: str,
        reference_audio_path: str,
        emotions: dict,
        output_path: str,
    ) -> tuple[bool, bool]:
        """一站式 TTS 生成：上传参考音频 → 构建 nodeInfoList → 提交（可重试）→ 轮询 → 下载。

        Returns:
            (success, is_retryable)
            - (True, _): 生成成功
            - (False, True): 并发限制等临时错误，调用方应排队重试
            - (False, False): 永久错误，调用方应标记失败
        """
        try:
            # 1. 上传参考音频（只做一次）
            ref_filename = await self.upload_file(reference_audio_path)
            if not ref_filename:
                print("[runninghub] upload reference audio failed")
                return False, False

            # 2. 构建 nodeInfoList
            seed = random.randint(0, 2 ** 31 - 1)

            def _norm(key: str) -> str:
                return f"{emotions.get(key, 0) / 100.0:.2f}"

            node_info_list = [
                {"nodeId": "87", "fieldName": "value", "fieldValue": text},
                {"nodeId": "159", "fieldName": "audio", "fieldValue": ref_filename},
                {"nodeId": "100", "fieldName": "Happy",    "fieldValue": _norm("happy")},
                {"nodeId": "100", "fieldName": "Angry",    "fieldValue": _norm("angry")},
                {"nodeId": "100", "fieldName": "Sad",      "fieldValue": _norm("sad")},
                {"nodeId": "100", "fieldName": "Fear",     "fieldValue": _norm("fear")},
                {"nodeId": "100", "fieldName": "Hate",     "fieldValue": _norm("hate")},
                {"nodeId": "100", "fieldName": "Low",      "fieldValue": _norm("low")},
                {"nodeId": "100", "fieldName": "Surprise", "fieldValue": _norm("surprise")},
                {"nodeId": "100", "fieldName": "Neutral",  "fieldValue": _norm("neutral")},
                {"nodeId": "152", "fieldName": "seed",     "fieldValue": str(seed)},
            ]

            # 3. 提交任务（遇到并发限制自动等待重试）
            task_id = None
            while task_id is None:
                task_id, is_retryable = await self.submit_task(node_info_list)
                if task_id is None and is_retryable:
                    # 并发/频率限制 → 等待后重试（不把重试逻辑暴露给调用方）
                    print(f"[runninghub] waiting 5s before submit retry...")
                    await asyncio.sleep(5)
                    continue
                elif task_id is None:
                    # 永久错误
                    return False, False

            print(f"[runninghub] task submitted, taskId={task_id}")

            # 4. 轮询等待结果
            timeout = settings.runninghub_timeout
            start = time.time()
            while time.time() - start < timeout:
                outputs = await self.query_outputs(task_id)
                code = outputs.get("code")
                data = outputs.get("data")

                if code == 0 and data:
                    file_url = data[0].get("fileUrl") if isinstance(data, list) else data.get("fileUrl")
                    if file_url:
                        print(f"[runninghub] generation done, downloading...")
                        return (await self._download_file(file_url, output_path)), False

                elif code == 805:
                    failed = data.get("failedReason") if data else None
                    print(f"[runninghub] task failed: {failed}")
                    return False, False

                elif code in (804, 813):
                    pass

                else:
                    print(f"[runninghub] unknown status: code={code}")

                await asyncio.sleep(settings.runninghub_poll_interval)

            print(f"[runninghub] timeout for taskId={task_id}")
            return False, False

        except Exception as e:
            print(f"[runninghub] generate_and_download error: {e}")
            return False, False
