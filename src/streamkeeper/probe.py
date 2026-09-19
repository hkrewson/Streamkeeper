from __future__ import annotations

import csv
import json
import math
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .models import ProbeSnapshot


class ProbeError(RuntimeError):
    pass


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise ProbeError(f"Required tool not found: {name}")
    return executable


def tool_version(name: str, *, timeout: int = 10) -> str:
    executable = require_tool(name)
    for flag in ("-version", "--version"):
        try:
            result = subprocess.run(
                [executable, flag], capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProbeError(f"{name} version check timed out") from exc
        except OSError as exc:
            raise ProbeError(f"Unable to run {name}: {exc}") from exc
        output = (result.stdout or result.stderr).splitlines()
        if result.returncode == 0 and output:
            return output[0]
    return "unknown"


def tool_status(name: str) -> dict[str, str | bool | None]:
    """Return bounded, JSON-safe diagnostic information for one CLI tool."""
    executable = shutil.which(name)
    if not executable:
        return {
            "name": name,
            "available": False,
            "path": None,
            "version": None,
            "error": f"Required tool not found: {name}",
        }
    try:
        version = tool_version(name)
    except ProbeError as exc:
        return {
            "name": name,
            "available": False,
            "path": executable,
            "version": None,
            "error": str(exc),
        }
    return {
        "name": name,
        "available": True,
        "path": executable,
        "version": version,
        "error": None,
    }


def probe_file(path: str | Path, *, deep: bool = False, timeout: int = 300) -> ProbeSnapshot:
    media_path = Path(path).expanduser().resolve()
    executable = require_tool("ffprobe")
    captured_at = datetime.now(timezone.utc).isoformat()
    command = [
        executable,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-show_chapters",
        "-print_format",
        "json",
        str(media_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(str(exc)) from exc
    if result.returncode != 0:
        raise ProbeError((result.stderr or "ffprobe failed").strip())
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON: {exc}") from exc
    _merge_first_video_frame_side_data(
        data,
        media_path,
        executable=executable,
        timeout=timeout,
    )
    peak = measure_peak_bitrate(media_path, timeout=max(timeout, 3600)) if deep else None
    return ProbeSnapshot(
        path=str(media_path),
        captured_at=captured_at,
        format=data.get("format", {}),
        streams=data.get("streams", []),
        chapters=data.get("chapters", []),
        peak_bitrate_bps=peak,
        tool_version=tool_version("ffprobe"),
    )


def _merge_first_video_frame_side_data(
    data: dict,
    media_path: Path,
    *,
    executable: str,
    timeout: int,
) -> None:
    video = next(
        (
            stream
            for stream in data.get("streams", [])
            if stream.get("codec_type") == "video"
            and not int(stream.get("disposition", {}).get("attached_pic", 0) or 0)
        ),
        None,
    )
    if not video:
        return
    command = [
        executable,
        "-v", "error",
        "-select_streams", "v:0",
        "-read_intervals", "%+#1",
        "-show_frames",
        "-show_entries", "frame=side_data_list",
        "-print_format", "json",
        str(media_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        frame_data = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return
    frames = frame_data.get("frames", [])
    if not frames:
        return
    existing = list(video.get("side_data_list", []) or [])
    existing_types = {str(item.get("side_data_type", "")) for item in existing}
    for item in frames[0].get("side_data_list", []) or []:
        side_type = str(item.get("side_data_type", ""))
        if side_type and side_type not in existing_types:
            existing.append(item)
            existing_types.add(side_type)
    if existing:
        video["side_data_list"] = existing


def measure_peak_bitrate(path: str | Path, *, window_seconds: int = 1, timeout: int = 3600) -> int:
    """Return the largest packet-byte total in a wall-clock window as bits/second."""
    executable = require_tool("ffprobe")
    command = [
        executable,
        "-v",
        "error",
        "-show_packets",
        "-show_entries",
        "packet=pts_time,dts_time,size",
        "-of",
        "csv=p=0",
        str(Path(path).expanduser().resolve()),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    buckets: dict[int, int] = {}
    try:
        assert process.stdout is not None
        for row in csv.reader(process.stdout):
            if len(row) < 3:
                continue
            timestamp_raw = row[0] if row[0] not in {"", "N/A"} else row[1]
            try:
                timestamp = float(timestamp_raw)
                size = int(row[2])
            except (ValueError, TypeError):
                continue
            bucket = math.floor(timestamp / window_seconds)
            buckets[bucket] = buckets.get(bucket, 0) + size
        try:
            _, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise ProbeError("ffprobe packet analysis timed out") from exc
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    if process.returncode != 0:
        raise ProbeError((stderr or "ffprobe packet analysis failed").strip())
    return max(buckets.values(), default=0) * 8 // window_seconds
