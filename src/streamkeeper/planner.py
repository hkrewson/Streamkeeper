from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .commands import normalized_commands
from .discovery import SUPPORTED_EXTENSIONS
from .models import ConversionPlan, ProbeSnapshot
from .policy import findings_for, plan_audio, plan_subtitles, planned_audio_labels, video_mode


def _nfo_root(path: Path) -> str | None:
    try:
        return ET.parse(path).getroot().tag.lower()
    except (ET.ParseError, OSError):
        return None


def matching_nfo(path: Path, output_stem: str | None = None) -> Path | None:
    """Select only an unambiguous movie or episode NFO sidecar."""
    direct = [path.with_suffix(".nfo")]
    if output_stem and output_stem != path.stem:
        direct.append(path.with_name(f"{output_stem}.nfo"))
    for candidate in direct:
        if candidate.is_file() and _nfo_root(candidate) in {"movie", "episodedetails"}:
            return candidate

    if not path.parent.is_dir():
        return None
    media_files = [
        candidate for candidate in path.parent.iterdir()
        if candidate.is_file()
        and candidate.suffix.lower() in SUPPORTED_EXTENSIONS
        and not candidate.name.startswith("._")
        and " original." not in candidate.name.lower()
    ]
    movie_nfo = path.parent / "movie.nfo"
    if len(media_files) == 1 and movie_nfo.is_file() and _nfo_root(movie_nfo) == "movie":
        return movie_nfo
    return None


def desired_stem(path: Path, media_kind: str = "movie", extra_type: str | None = None) -> str:
    if media_kind == "extra" and extra_type and not path.stem.lower().endswith(f"-{extra_type}"):
        return f"{path.stem}-{extra_type}"
    return path.stem


def build_plan(
    snapshot: ProbeSnapshot,
    *,
    media_kind: str = "movie",
    extra_type: str | None = None,
    fallback_language: str = "eng",
    network_ceiling_bps: int = 900_000_000,
) -> ConversionPlan:
    source = Path(snapshot.path)
    stem = desired_stem(source, media_kind, extra_type)
    output = source.with_name(f"{stem}.mkv")
    backup = source.with_name(f"{stem} Original{source.suffix}")
    evidence = source.with_name(f"{stem}.conversion.txt")
    nfo = matching_nfo(source, stem)
    video_action, video_reason, hdr_mode, extra_tools = video_mode(snapshot)
    subtitles = plan_subtitles(snapshot)
    warnings: list[str] = []
    if subtitles.bitmap_warnings:
        warnings.append("Bitmap subtitles are retained without OCR and may trigger client transcoding.")
    data_streams = sum(stream.get("codec_type") == "data" for stream in snapshot.streams)
    if data_streams:
        warnings.append(f"{data_streams} non-playback data stream(s) will be omitted and retained in the Original file.")
    plan = ConversionPlan(
        source_path=str(source),
        output_path=str(output),
        backup_path=str(backup),
        evidence_path=str(evidence),
        nfo_path=str(nfo) if nfo else None,
        video_action=video_action,
        video_reason=video_reason,
        hdr_mode=hdr_mode,
        audio=plan_audio(snapshot, fallback_language),
        audio_labels=planned_audio_labels(snapshot, fallback_language),
        subtitles=subtitles,
        findings=findings_for(snapshot, network_ceiling_bps),
        warnings=warnings,
        required_tools=["ffmpeg", "ffprobe", *extra_tools],
        executable=False,
    )
    plan.normalized_commands = normalized_commands(
        snapshot,
        video_action=plan.video_action,
        audio=plan.audio,
        output_path=plan.output_path,
    )
    return plan


def render_text(plan: ConversionPlan) -> str:
    audio = plan.audio
    lines = [
        f"Analyze: {plan.source_path}",
        f"  Video action: {plan.video_action} ({plan.video_reason})",
        f"  HDR mode: {plan.hdr_mode}",
        f"  Existing subtitles retained: {plan.subtitles.retained}",
        f"  ASS/SSA SRT fallbacks: {plan.subtitles.ass_srt_fallbacks}",
        f"  MOV_TEXT conversions: {plan.subtitles.mov_text_conversions}",
        f"  Bitmap subtitle warnings: {plan.subtitles.bitmap_warnings}",
    ]
    if audio.action == "transcode":
        lines.append(f"  New audio: {audio.label} at {(audio.bitrate or 0) // 1000} kbps from track {(audio.source_ordinal or 0) + 1}")
    else:
        lines.append(f"  New audio: none ({audio.reason})")
    lines.extend(
        [
            f"  Backup name: {Path(plan.backup_path).name}",
            f"  Output name: {Path(plan.output_path).name}",
            f"  Evidence file: {Path(plan.evidence_path).name}",
            "  DRY RUN: no encode, remux, or rename performed.",
        ]
    )
    lines.append("  Planned command vectors:")
    lines.extend(f"    {' '.join(command)}" for command in plan.normalized_commands)
    return "\n".join(lines)
