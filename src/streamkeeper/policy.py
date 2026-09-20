from __future__ import annotations

import re
from typing import Any

from .models import (
    AudioPlan,
    CompatibilityFinding,
    ProbeSnapshot,
    SubtitlePlan,
    audio_streams,
    primary_video,
    subtitle_streams,
)

EXCLUDED_AUDIO = re.compile(r"commentary|descriptive|description|narration", re.I)
LANGUAGE_NAMES = {
    "en": "English", "eng": "English", "es": "Spanish", "spa": "Spanish",
    "fr": "French", "fre": "French", "fra": "French", "de": "German",
    "ger": "German", "deu": "German", "it": "Italian", "ita": "Italian",
    "ja": "Japanese", "jpn": "Japanese", "ko": "Korean", "kor": "Korean",
    "zh": "Chinese", "chi": "Chinese", "zho": "Chinese", "pt": "Portuguese",
    "por": "Portuguese", "ru": "Russian", "rus": "Russian", "ar": "Arabic",
    "ara": "Arabic", "hi": "Hindi", "hin": "Hindi", "nl": "Dutch",
    "dut": "Dutch", "nld": "Dutch", "sv": "Swedish", "swe": "Swedish",
    "no": "Norwegian", "nor": "Norwegian", "da": "Danish", "dan": "Danish",
    "fi": "Finnish", "fin": "Finnish", "pl": "Polish", "pol": "Polish",
    "tr": "Turkish", "tur": "Turkish", "cs": "Czech", "cze": "Czech",
    "ces": "Czech", "hu": "Hungarian", "hun": "Hungarian", "he": "Hebrew",
    "heb": "Hebrew", "th": "Thai", "tha": "Thai", "vi": "Vietnamese",
    "vie": "Vietnamese", "id": "Indonesian", "ind": "Indonesian",
    "und": "English",
}
SPECIAL_AUDIO_TITLE = re.compile(r"commentary|descriptive|description|original|dub|director|isolated|narration|alternate", re.I)


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def audio_excluded(stream: dict[str, Any]) -> bool:
    title = str(stream.get("tags", {}).get("title", ""))
    return bool(EXCLUDED_AUDIO.search(title)) or int_value(stream.get("disposition", {}).get("visual_impaired")) == 1


def audio_quality(stream: dict[str, Any]) -> int:
    codec = str(stream.get("codec_name", "unknown"))
    profile_title = f"{stream.get('profile', '')} {stream.get('tags', {}).get('title', '')}"
    if codec in {"truehd", "mlp"}:
        return 600
    if codec == "dts" and re.search(r"master|\bma\b|dts[: -]*x", profile_title, re.I):
        return 550
    if codec == "flac":
        return 525
    if codec.startswith("pcm_"):
        return 500
    if codec == "eac3" and int_value(stream.get("channels")) > 6:
        return 450
    if codec == "dts":
        return 400
    if codec == "aac" and int_value(stream.get("channels")) > 6:
        # AAC 7.1 is preserved, but it is not one of the project's dependable
        # compatibility targets. It may supply an E-AC3 5.1 fallback when no
        # higher-quality 6+ channel source is available.
        return 250
    if codec not in {"aac", "ac3", "eac3", "mp3", "alac", "unknown"}:
        return 300
    return 0


def channel_class(stream: dict[str, Any]) -> int:
    channels = int_value(stream.get("channels"))
    return 3 if channels >= 6 else 2 if channels >= 3 else 1


def best_audio_source(streams: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    eligible = [(index, stream) for index, stream in enumerate(streams) if not audio_excluded(stream) and audio_quality(stream)]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: (
            channel_class(item[1]),
            audio_quality(item[1]),
            int_value(item[1].get("channels")),
            int_value(item[1].get("bit_rate")),
            int_value(item[1].get("disposition", {}).get("default")),
            -item[0],
        ),
    )


def resolved_language(stream: dict[str, Any], fallback: str = "eng") -> str:
    language = str(stream.get("tags", {}).get("language", "und") or "und").lower()
    return fallback if language in {"", "und"} else language


def channel_label(channels: int) -> str:
    return {1: "1.0", 2: "2.0", 3: "3.0", 4: "4.0", 5: "5.0", 6: "5.1", 8: "7.1"}.get(channels, f"{channels}ch")


def codec_label(codec: str) -> str:
    return {"eac3": "E-AC3", "ac3": "AC3", "aac": "AAC", "truehd": "TrueHD", "dts": "DTS", "flac": "FLAC"}.get(codec, codec.upper())


def detailed_codec_label(stream: dict[str, Any]) -> str:
    codec = str(stream.get("codec_name", "unknown"))
    profile = str(stream.get("profile", ""))
    title = str(stream.get("tags", {}).get("title", ""))
    detail = f"{profile} {title}"
    if codec in {"truehd", "mlp"}:
        return "TrueHD Atmos" if re.search("atmos", detail, re.I) else "TrueHD"
    if codec == "dts":
        if re.search(r"dts[: -]*x", detail, re.I):
            return "DTS:X"
        if re.search(r"master|\bma\b", profile, re.I):
            return "DTS-HD MA"
        if re.search(r"high.resolution|hra", profile, re.I):
            return "DTS-HD HRA"
        return "DTS"
    if codec == "eac3":
        return "E-AC3 Atmos" if re.search(r"atmos|joc", detail, re.I) else "E-AC3"
    if codec.startswith("pcm_") or codec == "pcm":
        return "PCM"
    return codec_label(codec)


def planned_audio_labels(snapshot: ProbeSnapshot, fallback_language: str = "eng") -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for ordinal, stream in enumerate(audio_streams(snapshot)):
        language = resolved_language(stream, fallback_language)
        language_name = LANGUAGE_NAMES.get(language, language)
        base = f"{language_name} {detailed_codec_label(stream)} {channel_label(int_value(stream.get('channels')))}"
        old_title = str(stream.get("tags", {}).get("title", ""))
        label = f"{base} - {old_title}" if old_title and old_title != base and SPECIAL_AUDIO_TITLE.search(old_title) else base
        labels.append({"ordinal": ordinal, "language": language, "label": label})
    return labels


def has_target(streams: list[dict[str, Any]], codec: str, channels: int) -> bool:
    return any(
        stream.get("codec_name") == codec
        and int_value(stream.get("channels")) == channels
        and not audio_excluded(stream)
        for stream in streams
    )


def default_audio_ordinal(streams: list[dict[str, Any]]) -> int | None:
    eligible = [(i, s) for i, s in enumerate(streams) if not audio_excluded(s)]
    precedence = [("eac3", 6), ("ac3", 6), ("aac", 6), ("aac", 2)]
    for codec, channels in precedence:
        match = next((i for i, stream in eligible if stream.get("codec_name") == codec and int_value(stream.get("channels")) == channels), None)
        if match is not None:
            return match
    default = next((i for i, stream in eligible if int_value(stream.get("disposition", {}).get("default")) == 1), None)
    return default if default is not None else eligible[0][0] if eligible else None


def plan_audio(snapshot: ProbeSnapshot, fallback_language: str = "eng") -> AudioPlan:
    streams = audio_streams(snapshot)
    chosen = best_audio_source(streams)
    existing_default = default_audio_ordinal(streams)
    if not chosen:
        compatible_exists = any(
            stream.get("codec_name") == "aac"
            and int_value(stream.get("channels")) in {2, 6}
            and not audio_excluded(stream)
            for stream in streams
        )
        reason = (
            "matching compatibility stream already exists"
            if compatible_exists
            else "no eligible conversion source"
        )
        return AudioPlan(action="none", default_ordinal=existing_default, reason=reason)
    source_ordinal, source = chosen
    source_channels = int_value(source.get("channels"))
    if source_channels >= 6:
        codec, channels, bitrate = "eac3", 6, 640_000
    elif source_channels >= 3:
        codec, channels = "eac3", source_channels
        source_bitrate = int_value(source.get("bit_rate"))
        bitrate = source_bitrate if 0 < source_bitrate < 640_000 else 640_000
    elif source_channels == 2:
        codec, channels, bitrate = "aac", 2, 192_000
    elif source_channels == 1:
        codec, channels, bitrate = "aac", 1, 96_000
    else:
        return AudioPlan(action="none", default_ordinal=existing_default, reason="source has no usable channel count")
    if has_target(streams, codec, channels):
        return AudioPlan(action="none", source_ordinal=source_ordinal, default_ordinal=existing_default, reason="matching compatibility stream already exists")
    language = resolved_language(source, fallback_language)
    label = f"{LANGUAGE_NAMES.get(language, language)} {codec_label(codec)} {channel_label(channels)}"
    generated_ordinal = len(streams)
    compatible_surround_exists = any(
        stream.get("codec_name") in {"eac3", "ac3", "aac"}
        and int_value(stream.get("channels")) == 6
        and not audio_excluded(stream)
        for stream in streams
    )
    make_default = codec == "eac3" and channels == 6 or not compatible_surround_exists
    return AudioPlan(
        action="transcode",
        source_ordinal=source_ordinal,
        codec=codec,
        channels=channels,
        bitrate=bitrate,
        language=language,
        label=label,
        default_ordinal=generated_ordinal if make_default else existing_default,
        reason="create Apple-compatible fallback without removing source audio",
    )


def video_mode(snapshot: ProbeSnapshot) -> tuple[str, str, str, list[str]]:
    video = primary_video(snapshot)
    if not video:
        return "error", "no primary video stream", "unknown", []
    codec = str(video.get("codec_name", "unknown"))
    transfer = str(video.get("color_transfer", "unknown"))
    side_data = video.get("side_data_list", []) or []
    dovi = next((item for item in side_data if "DOVI" in str(item.get("side_data_type", "")).upper()), None)
    hdr10plus = any(re.search(r"HDR10\+|SMPTE2094-40", str(item.get("side_data_type", "")), re.I) for item in side_data)
    if dovi:
        dovi_profile = int_value(dovi.get("dv_profile"))
        compatibility_raw = dovi.get("dv_bl_signal_compatibility_id")
        dovi_compatibility = int_value(compatibility_raw)
        hdr_mode = f"Dolby Vision profile {dovi_profile or 'unknown'}"
        if compatibility_raw not in {None, ""}:
            hdr_mode += f" (compatibility {dovi_compatibility})"
    else:
        dovi_profile = 0
        dovi_compatibility = 0
        hdr_mode = "HDR10" if transfer == "smpte2084" else "HLG" if transfer == "arib-std-b67" else "SDR"
    if hdr10plus:
        hdr_mode += " + HDR10+"
    required: list[str] = []
    if dovi_profile == 8 and dovi_compatibility not in {1, 4}:
        return (
            "error",
            f"Dolby Vision profile 8 compatibility ID {dovi_compatibility or 'unknown'} is not safely convertible to 8.1/8.4",
            hdr_mode,
            required,
        )
    if dovi and dovi_profile not in {5, 7, 8}:
        return "error", f"unsupported Dolby Vision profile {dovi_profile or 'unknown'}", hdr_mode, required
    if codec == "hevc":
        if dovi_profile == 7:
            required.append("dovi_tool")
            action = "dovi_convert_strip_hdr10plus" if hdr10plus else "dovi_convert"
            return action, "convert Dolby Vision 7 to 8.1 without re-encoding the base layer", hdr_mode, required
        if hdr10plus:
            required.append("dovi_tool")
            return "strip_hdr10plus", "retain HDR10 base and remove HDR10+ metadata", hdr_mode, required
        return "copy", "already Apple-compatible", hdr_mode, required
    if codec == "h264" and hdr_mode == "SDR":
        return "copy", "SDR H.264 is already Apple-compatible", hdr_mode, required
    return "transcode_hevc", f"transcode {codec} to HEVC", hdr_mode, required


def plan_subtitles(snapshot: ProbeSnapshot) -> SubtitlePlan:
    streams = subtitle_streams(snapshot)
    return SubtitlePlan(
        retained=len(streams),
        ass_srt_fallbacks=sum(stream.get("codec_name") in {"ass", "ssa"} for stream in streams),
        mov_text_conversions=sum(stream.get("codec_name") == "mov_text" for stream in streams),
        bitmap_warnings=sum(stream.get("codec_name") in {"hdmv_pgs_subtitle", "dvd_subtitle"} for stream in streams),
    )


def findings_for(snapshot: ProbeSnapshot, network_ceiling_bps: int = 900_000_000) -> list[CompatibilityFinding]:
    findings: list[CompatibilityFinding] = []
    video_action, video_reason, _, _ = video_mode(snapshot)
    if video_action == "error":
        missing = video_reason == "no primary video stream"
        findings.append(CompatibilityFinding(
            "video.missing" if missing else "video.dolby_vision",
            "video", "error", "No primary video" if missing else "Unsupported Dolby Vision",
            video_reason,
        ))
    elif video_action != "copy":
        findings.append(CompatibilityFinding("video.compatibility", "video", "action", "Video compatibility conversion", video_reason, video_action))
    audio_plan = plan_audio(snapshot)
    if not audio_streams(snapshot):
        findings.append(CompatibilityFinding("audio.missing", "audio", "warning", "No audio stream", "A replacement cannot be manufactured."))
    elif audio_plan.action == "transcode":
        findings.append(CompatibilityFinding("audio.fallback", "audio", "action", "Apple audio fallback needed", audio_plan.reason, audio_plan.label))
    subtitle_plan = plan_subtitles(snapshot)
    if subtitle_plan.bitmap_warnings:
        findings.append(CompatibilityFinding("subtitle.bitmap", "subtitle", "warning", "Image-based subtitles", f"{subtitle_plan.bitmap_warnings} PGS/VobSub track(s) may trigger video transcoding."))
    if snapshot.peak_bitrate_bps is not None and snapshot.peak_bitrate_bps > network_ceiling_bps:
        findings.append(CompatibilityFinding("network.peak", "network", "action", "Peak bitrate exceeds network profile", f"Observed {snapshot.peak_bitrate_bps:,} bps; ceiling {network_ceiling_bps:,} bps."))
    return findings
