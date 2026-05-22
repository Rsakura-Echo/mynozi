"""音频处理：ffmpeg 提取/切割/合并。"""

import subprocess
from pathlib import Path


def extract_audio_from_video(video_path: str, output_path: str) -> bool:
    """从视频提取音频为 16kHz 单声道 wav。"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=600)
        return True
    except FileNotFoundError:
        print("[audio_service] ffmpeg not found. Please install ffmpeg and add it to PATH.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[audio_service] ffmpeg extract error: {e.stderr.decode()}")
        return False


def extract_sentence_audio(audio_path: str, start: float, end: float, output_path: str) -> bool:
    """从完整音频中截取某一句（按时间戳）。"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    duration = end - start
    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-t", str(duration),
        "-i", audio_path,
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        return True
    except FileNotFoundError:
        print("[audio_service] ffmpeg not found. Please install ffmpeg and add it to PATH.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[audio_service] ffmpeg cut error: {e.stderr.decode()}")
        return False


def merge_audio_files(file_paths: list[str], output_path: str, silence_ms: int = 200) -> bool:
    """合并多个音频文件，中间加静音间隔。"""
    if not file_paths:
        return False
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 构建 ffmpeg concat filter
    inputs = []
    filter_parts = []
    for i, fp in enumerate(file_paths):
        inputs.extend(["-i", fp])
        filter_parts.append(f"[{i}:a]")
        if i < len(file_paths) - 1:
            filter_parts.append(f"aevalsrc=0:d={silence_ms / 1000}[s{i}];")
            filter_parts.append(f"[s{i}]")

    filter_str = "".join(filter_parts) + f"concat=n={len(file_paths) * 2 - 1}:v=0:a=1[out]"

    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_str, "-map", "[out]", output_path]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=300)
        return True
    except FileNotFoundError:
        print("[audio_service] ffmpeg not found. Please install ffmpeg and add it to PATH.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[audio_service] ffmpeg merge error: {e.stderr.decode()}")
        return False
