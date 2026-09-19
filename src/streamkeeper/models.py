from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class LibraryType(StrEnum):
    MOVIE = "movie"
    TV = "tv"
    MIXED = "mixed"


class FindingStatus(StrEnum):
    OPEN = "open"
    IGNORED = "ignored"
    RESOLVED = "resolved"


@dataclass(slots=True)
class ProbeSnapshot:
    path: str
    captured_at: str
    format: dict[str, Any]
    streams: list[dict[str, Any]]
    chapters: list[dict[str, Any]] = field(default_factory=list)
    peak_bitrate_bps: int | None = None
    tool_version: str = "unknown"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MediaAsset:
    path: str
    relative_path: str
    library_type: LibraryType
    size_bytes: int
    modified_ns: int
    media_kind: str
    title: str
    extra_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["library_type"] = self.library_type.value
        return data


@dataclass(slots=True)
class CompatibilityFinding:
    rule_id: str
    category: str
    severity: str
    title: str
    detail: str
    recommended_action: str | None = None
    status: FindingStatus = FindingStatus.OPEN

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(slots=True)
class AudioPlan:
    action: str
    source_ordinal: int | None = None
    codec: str | None = None
    channels: int | None = None
    bitrate: int | None = None
    language: str | None = None
    label: str | None = None
    default_ordinal: int | None = None
    reason: str = ""


@dataclass(slots=True)
class SubtitlePlan:
    retained: int = 0
    ass_srt_fallbacks: int = 0
    mov_text_conversions: int = 0
    bitmap_warnings: int = 0


@dataclass(slots=True)
class ConversionPlan:
    source_path: str
    output_path: str
    backup_path: str
    evidence_path: str
    nfo_path: str | None
    video_action: str
    video_reason: str
    hdr_mode: str
    audio: AudioPlan
    audio_labels: list[dict[str, Any]]
    subtitles: SubtitlePlan
    findings: list[CompatibilityFinding]
    warnings: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=lambda: ["ffmpeg", "ffprobe"])
    normalized_commands: list[list[str]] = field(default_factory=list)
    executable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ConversionReceipt:
    source_path: str
    output_path: str
    status: str
    started_at: str
    finished_at: str | None
    source_sha256_before: str | None
    source_sha256_after: str | None
    plan: ConversionPlan
    validation: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScanRun:
    id: int | None
    library_id: int | None
    path: str
    library_type: LibraryType
    deep: bool
    trigger: str = "manual"
    status: str = "queued"
    phase: str = "metadata"
    total_files: int = 0
    processed_files: int = 0
    failed_files: int = 0
    new_files: int = 0
    changed_files: int = 0
    unchanged_files: int = 0
    removed_files: int = 0
    probed_files: int = 0
    reused_probes: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""


def primary_video(snapshot: ProbeSnapshot) -> dict[str, Any] | None:
    return next(
        (
            stream
            for stream in snapshot.streams
            if stream.get("codec_type") == "video"
            and not int(stream.get("disposition", {}).get("attached_pic", 0) or 0)
        ),
        None,
    )


def audio_streams(snapshot: ProbeSnapshot) -> list[dict[str, Any]]:
    return [stream for stream in snapshot.streams if stream.get("codec_type") == "audio"]


def subtitle_streams(snapshot: ProbeSnapshot) -> list[dict[str, Any]]:
    return [stream for stream in snapshot.streams if stream.get("codec_type") == "subtitle"]


def filename_stem(path: str) -> str:
    return Path(path).stem
