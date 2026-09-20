from __future__ import annotations

import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import ConversionPlan


@dataclass(slots=True)
class PlanningDecision:
    video_action: str | None = None
    video_reason: str | None = None
    hdr_mode: str | None = None
    existing_audio_tracks: int | None = None
    existing_subtitle_tracks: int = 0
    ass_srt_fallbacks: int = 0
    mov_text_conversions: int = 0
    bitmap_warnings: int = 0
    data_streams_omitted: int = 0
    new_audio_label: str | None = None
    new_audio_bitrate_kbps: int | None = None
    new_audio_source_track: int | None = None
    new_audio_reason: str | None = None
    default_audio: str | None = None
    backup_name: str | None = None
    output_name: str | None = None
    evidence_name: str | None = None
    nfo_name: str | None = None
    audio_labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_legacy_dry_run(output: str) -> PlanningDecision:
    decision = PlanningDecision()
    patterns: list[tuple[str, str, Any]] = [
        ("video_action", r"^\s*Video action:\s*(\S+)\s*\((.*)\)$", str),
        ("existing_audio_tracks", r"^\s*Existing audio tracks:\s*(\d+)", int),
        ("existing_subtitle_tracks", r"^\s*Existing subtitle tracks:\s*(\d+)", int),
        ("ass_srt_fallbacks", r"^\s*New subtitle fallbacks:\s*(\d+)", int),
        ("mov_text_conversions", r"^\s*Subtitle conversion:\s*(\d+)", int),
        ("bitmap_warnings", r"^\s*Subtitle warning:\s*(\d+)", int),
        ("data_streams_omitted", r"^\s*Container data:\s*(\d+)", int),
        ("default_audio", r"^\s*Default audio:\s*(.*)$", str),
        ("backup_name", r"^\s*Backup name:\s*(.*)$", str),
        ("output_name", r"^\s*Output name:\s*(.*)$", str),
        ("evidence_name", r"^\s*Evidence file:\s*(.*)$", str),
    ]
    for line in output.splitlines():
        video = re.match(r"^\s*Video:\s*[^,]+,\s*[^,]+,\s*(.*)$", line)
        if video:
            decision.hdr_mode = video.group(1)
        audio = re.match(r"^\s*New audio:\s*(.*?)\s+at\s+(\d+)\s+kbps, sourced from track\s+(\d+)$", line)
        if audio:
            decision.new_audio_label = audio.group(1)
            decision.new_audio_bitrate_kbps = int(audio.group(2))
            decision.new_audio_source_track = int(audio.group(3))
        elif match := re.match(r"^\s*New audio:\s*none\s*\((.*)\)$", line):
            decision.new_audio_reason = match.group(1)
        if match := re.match(r"^\s*NFO update:\s*(.*)$", line):
            nfo_detail = match.group(1)
            if nfo_detail.startswith("none found"):
                decision.nfo_name = None
            elif nfo_match := re.match(r"^(.*?)\s+\(<[^>]+> root;", nfo_detail):
                decision.nfo_name = nfo_match.group(1)
        if match := re.match(r"^\s*Label\s+\d+:\s*(.*)$", line):
            decision.audio_labels.append(match.group(1))
        for attribute, pattern, converter in patterns:
            match = re.match(pattern, line)
            if not match:
                continue
            setattr(decision, attribute, converter(match.group(1)))
            if attribute == "default_audio" and decision.default_audio == "none":
                decision.default_audio = None
            if attribute == "video_action":
                decision.video_reason = match.group(2)
            break
    if decision.video_action is None:
        raise ValueError("legacy output does not contain a video plan")
    return decision


def normalize_python_plan(plan: ConversionPlan) -> PlanningDecision:
    audio_labels = [item["label"] for item in plan.audio_labels]
    if plan.audio.action == "transcode" and plan.audio.label:
        audio_labels.append(plan.audio.label)
    if plan.audio.action == "transcode":
        new_reason = None
    elif "matching compatibility" in plan.audio.reason:
        new_reason = "matching compatibility stream already exists"
    else:
        new_reason = "no eligible conversion source"
    default_audio = None
    if plan.audio.default_ordinal is not None:
        if plan.audio.action == "transcode" and plan.audio.default_ordinal == len(plan.audio_labels):
            default_audio = f"{plan.audio.label} (new)"
        elif plan.audio.default_ordinal < len(audio_labels):
            default_audio = audio_labels[plan.audio.default_ordinal]
    data_streams = next(
        (int(match.group(1)) for warning in plan.warnings if (match := re.match(r"(\d+) non-playback data", warning))),
        0,
    )
    return PlanningDecision(
        video_action=plan.video_action,
        video_reason=plan.video_reason,
        hdr_mode=plan.hdr_mode,
        existing_audio_tracks=len(plan.audio_labels),
        existing_subtitle_tracks=plan.subtitles.retained,
        ass_srt_fallbacks=plan.subtitles.ass_srt_fallbacks,
        mov_text_conversions=plan.subtitles.mov_text_conversions,
        bitmap_warnings=plan.subtitles.bitmap_warnings,
        data_streams_omitted=data_streams,
        new_audio_label=plan.audio.label if plan.audio.action == "transcode" else None,
        new_audio_bitrate_kbps=(plan.audio.bitrate or 0) // 1000 if plan.audio.action == "transcode" else None,
        new_audio_source_track=(plan.audio.source_ordinal or 0) + 1 if plan.audio.action == "transcode" else None,
        new_audio_reason=new_reason,
        default_audio=default_audio,
        backup_name=plan.backup_path.rsplit("/", 1)[-1],
        output_name=plan.output_path.rsplit("/", 1)[-1],
        evidence_name=plan.evidence_path.rsplit("/", 1)[-1],
        nfo_name=plan.nfo_path.rsplit("/", 1)[-1] if plan.nfo_path else None,
        audio_labels=audio_labels,
    )


def compare_decisions(reference: PlanningDecision, candidate: PlanningDecision) -> dict[str, dict[str, Any]]:
    reference_data = reference.to_dict()
    candidate_data = candidate.to_dict()
    return {
        key: {"reference": reference_data[key], "candidate": candidate_data[key]}
        for key in reference_data
        if reference_data[key] != candidate_data[key]
    }


def parse_legacy_evidence_commands(evidence: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for line in evidence.splitlines():
        match = re.match(r"^\s*Command:\s*(.*)$", line)
        if match:
            commands.append(shlex.split(match.group(1)))
    return commands


def normalize_command_vector(command: list[str], *, source_path: str) -> list[str]:
    normalized: list[str] = []
    for argument in command:
        if argument == source_path:
            normalized.append("<SOURCE>")
        elif re.search(r"/\.[^/]+\.convert\.[^/]+$", argument) or argument.endswith(".streamkeeper.partial.mkv"):
            normalized.append("<OUTPUT>")
        else:
            normalized.append(argument)
    return normalized


def compare_command_vectors(
    reference: list[list[str]],
    candidate: list[list[str]],
    *,
    reference_source: str,
    candidate_source: str,
) -> dict[str, Any]:
    normalized_reference = [
        normalize_command_vector(command, source_path=reference_source) for command in reference
    ]
    normalized_candidate = [
        normalize_command_vector(command, source_path=candidate_source) for command in candidate
    ]
    if normalized_reference == normalized_candidate:
        return {}
    return {"reference": normalized_reference, "candidate": normalized_candidate}
