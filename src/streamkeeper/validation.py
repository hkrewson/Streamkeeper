from __future__ import annotations

import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .models import ConversionPlan, ProbeSnapshot, audio_streams, primary_video, subtitle_streams


def validate_output(
    source: ProbeSnapshot,
    output: ProbeSnapshot,
    plan: ConversionPlan | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    source_video = primary_video(source)
    output_video = primary_video(output)
    if source_video and not output_video:
        errors.append("primary video stream is missing")
    elif source_video and output_video:
        _validate_video(source_video, output_video, plan, errors)

    source_audio = audio_streams(source)
    output_audio = audio_streams(output)
    if len(output_audio) < len(source_audio):
        errors.append("one or more source audio streams are missing")
    else:
        _validate_audio(source_audio, output_audio, plan, errors)

    source_subtitles = subtitle_streams(source)
    output_subtitles = subtitle_streams(output)
    expected_subtitles = len(source_subtitles) + (plan.subtitles.ass_srt_fallbacks if plan else 0)
    if len(output_subtitles) < expected_subtitles:
        errors.append(f"subtitle count is {len(output_subtitles)}; expected at least {expected_subtitles}")
    else:
        _validate_subtitles(source_subtitles, output_subtitles, plan, errors)

    if len(output.chapters) < len(source.chapters):
        errors.append("one or more chapters are missing")
    _validate_attachments(source, output, errors)
    source_data = sum(stream.get("codec_type") == "data" for stream in source.streams)
    output_data = sum(stream.get("codec_type") == "data" for stream in output.streams)
    if plan and source_data and output_data:
        errors.append("one or more non-playback data streams were not omitted")
    _validate_duration_and_frames(source, output, errors)

    return {
        "passed": not errors,
        "errors": errors,
        "source_duration": _float_value(source.format.get("duration")),
        "output_duration": _float_value(output.format.get("duration")),
        "source_streams": len(source.streams),
        "output_streams": len(output.streams),
        "source_audio": len(source_audio),
        "output_audio": len(output_audio),
        "source_subtitles": len(source_subtitles),
        "output_subtitles": len(output_subtitles),
        "source_chapters": len(source.chapters),
        "output_chapters": len(output.chapters),
        "source_data": source_data,
        "output_data": output_data,
    }


def validate_copied_stream_hashes(
    source_path: str | Path,
    output_path: str | Path,
    source: ProbeSnapshot,
    plan: ConversionPlan,
) -> dict[str, object]:
    """Hash copied elementary streams after container metadata is removed."""
    selectors: list[tuple[str, str | None]] = []
    source_video = primary_video(source)
    if plan.video_action == "copy" and source_video:
        video_filter = {
            "h264": "h264_mp4toannexb",
            "hevc": "hevc_mp4toannexb",
        }.get(str(source_video.get("codec_name", "")))
        selectors.append(("v:0", video_filter))
    selectors.extend(
        (
            f"a:{ordinal}",
            "aac_adtstoasc" if stream.get("codec_name") == "aac" else None,
        )
        for ordinal, stream in enumerate(audio_streams(source))
    )
    selectors.extend(
        (f"s:{ordinal}", None)
        for ordinal, stream in enumerate(subtitle_streams(source))
        if stream.get("codec_name") != "mov_text"
    )

    errors: list[str] = []
    hashes: dict[str, dict[str, str]] = {}
    for selector, bitstream_filter in selectors:
        try:
            source_hash = elementary_stream_sha256(source_path, selector, bitstream_filter)
            output_hash = elementary_stream_sha256(output_path, selector, bitstream_filter)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            errors.append(f"could not hash copied stream {selector}: {exc}")
            continue
        hashes[selector] = {"source": source_hash, "output": output_hash}
        if source_hash != output_hash:
            errors.append(f"copied stream {selector} is not byte-identical")
    return {"passed": not errors, "errors": errors, "hashes": hashes}


def elementary_stream_sha256(
    path: str | Path,
    selector: str,
    bitstream_filter: str | None = None,
) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise OSError("ffmpeg is required for elementary-stream hashing")
    command = [
        ffmpeg,
        "-v", "error",
        "-nostdin",
        "-i", str(Path(path).expanduser().resolve()),
        "-map", f"0:{selector}",
        "-c", "copy",
    ]
    if bitstream_filter:
        stream_type = selector.split(":", 1)[0]
        command += [f"-bsf:{stream_type}", bitstream_filter]
    command += ["-f", "hash", "-hash", "sha256", "-"]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError((result.stderr or "ffmpeg stream hashing failed").strip())
    match = re.search(r"SHA256=([0-9a-fA-F]{64})", result.stdout)
    if not match:
        raise ValueError("ffmpeg did not return a SHA-256 stream hash")
    return match.group(1).lower()


def _validate_video(
    source: dict[str, Any],
    output: dict[str, Any],
    plan: ConversionPlan | None,
    errors: list[str],
) -> None:
    source_codec = str(source.get("codec_name", ""))
    output_codec = str(output.get("codec_name", ""))
    expected_codec = "hevc" if plan and plan.video_action == "transcode_hevc" else source_codec
    if output_codec != expected_codec:
        errors.append(f"video codec is {output_codec or 'missing'}; expected {expected_codec}")

    action = plan.video_action if plan else "copy"
    source_transfer = str(source.get("color_transfer", ""))
    if action == "copy" and output.get("pix_fmt") != source.get("pix_fmt"):
        errors.append("copied video pixel format changed")
    if source_transfer in {"smpte2084", "arib-std-b67"}:
        output_pixel_format = str(output.get("pix_fmt", ""))
        if not re.search(r"(?:10|12)", output_pixel_format):
            errors.append("HDR output is not 10/12-bit")
        if output.get("color_transfer") != source_transfer:
            errors.append("HDR transfer metadata changed")
        for field, label in (("color_primaries", "color primaries"), ("color_space", "color matrix")):
            if source.get(field) and output.get(field) != source.get(field):
                errors.append(f"HDR {label} changed")
        for marker, label in (
            ("mastering display", "mastering-display metadata"),
            ("content light level", "content-light metadata"),
        ):
            source_side = _side_data(source, marker)
            output_side = _side_data(output, marker)
            if source_side and not _equivalent_hdr_side_data(source_side, output_side, marker):
                errors.append(f"HDR {label} changed or is missing")

    output_dovi = _side_data(output, "dovi")
    if plan and plan.video_action in {"dovi_convert", "dovi_convert_strip_hdr10plus"}:
        if not output_dovi or _int_value(output_dovi.get("dv_profile")) != 8:
            errors.append("Dolby Vision output is not profile 8")
        elif _int_value(output_dovi.get("dv_bl_signal_compatibility_id")) != 1:
            errors.append("Dolby Vision output is not compatibility profile 8.1")
    elif _side_data(source, "dovi") and action == "copy" and output_dovi != _side_data(source, "dovi"):
        errors.append("copied Dolby Vision metadata changed")
    if plan and plan.video_action in {"strip_hdr10plus", "dovi_convert_strip_hdr10plus"} and _has_hdr10plus(output):
        errors.append("HDR10+ metadata remains after normalization")


def _validate_audio(
    source: list[dict[str, Any]],
    output: list[dict[str, Any]],
    plan: ConversionPlan | None,
    errors: list[str],
) -> None:
    for ordinal, source_stream in enumerate(source):
        output_stream = output[ordinal]
        if output_stream.get("codec_name") != source_stream.get("codec_name"):
            errors.append(f"source audio track {ordinal + 1} codec changed")
        if _int_value(output_stream.get("channels")) != _int_value(source_stream.get("channels")):
            errors.append(f"source audio track {ordinal + 1} channel count changed")
        if plan and ordinal < len(plan.audio_labels):
            expected = plan.audio_labels[ordinal]
            tags = output_stream.get("tags", {})
            if tags.get("language") != expected["language"]:
                errors.append(f"source audio track {ordinal + 1} language label is incorrect")
            if tags.get("title") != expected["label"]:
                errors.append(f"source audio track {ordinal + 1} title label is incorrect")

    if plan and plan.audio.action == "transcode":
        generated_ordinal = len(source)
        if generated_ordinal >= len(output):
            errors.append("generated compatibility audio is missing")
        else:
            generated = output[generated_ordinal]
            if generated.get("codec_name") != plan.audio.codec:
                errors.append("generated compatibility audio codec is incorrect")
            if _int_value(generated.get("channels")) != plan.audio.channels:
                errors.append("generated compatibility audio channel count is incorrect")
            tags = generated.get("tags", {})
            if tags.get("language") != plan.audio.language:
                errors.append("generated compatibility audio language is incorrect")
            if tags.get("title") != plan.audio.label:
                errors.append("generated compatibility audio label is incorrect")

    if plan and plan.audio.default_ordinal is not None:
        defaults = [
            ordinal
            for ordinal, stream in enumerate(output)
            if _int_value(stream.get("disposition", {}).get("default")) == 1
        ]
        if defaults != [plan.audio.default_ordinal]:
            errors.append(f"default audio tracks are {defaults}; expected [{plan.audio.default_ordinal}]")


def _validate_subtitles(
    source: list[dict[str, Any]],
    output: list[dict[str, Any]],
    plan: ConversionPlan | None,
    errors: list[str],
) -> None:
    for ordinal, source_stream in enumerate(source):
        expected_codec = "subrip" if source_stream.get("codec_name") == "mov_text" else source_stream.get("codec_name")
        if output[ordinal].get("codec_name") != expected_codec:
            errors.append(f"source subtitle track {ordinal + 1} codec is incorrect")
        if _enabled_dispositions(output[ordinal]) != _enabled_dispositions(source_stream):
            errors.append(f"source subtitle track {ordinal + 1} dispositions changed")
    if plan and plan.subtitles.ass_srt_fallbacks:
        generated = output[len(source):len(source) + plan.subtitles.ass_srt_fallbacks]
        if any(stream.get("codec_name") != "subrip" for stream in generated):
            errors.append("one or more ASS/SSA compatibility subtitles are not SRT")


def _validate_attachments(source: ProbeSnapshot, output: ProbeSnapshot, errors: list[str]) -> None:
    source_attachments = sum(stream.get("codec_type") == "attachment" for stream in source.streams)
    source_covers = sum(
        stream.get("codec_type") == "video"
        and _int_value(stream.get("disposition", {}).get("attached_pic")) == 1
        for stream in source.streams
    )
    output_attachments = sum(stream.get("codec_type") == "attachment" for stream in output.streams)
    output_covers = sum(
        stream.get("codec_type") == "video"
        and _int_value(stream.get("disposition", {}).get("attached_pic")) == 1
        for stream in output.streams
    )
    # FFprobe commonly reports Matroska image attachments as video streams
    # with attached_pic=1, even when FFmpeg created them with -attach.
    if output_attachments + output_covers < source_attachments + source_covers:
        errors.append("one or more attachments or embedded covers are missing")


def _validate_duration_and_frames(source: ProbeSnapshot, output: ProbeSnapshot, errors: list[str]) -> None:
    source_duration = _float_value(source.format.get("duration"))
    output_duration = _float_value(output.format.get("duration"))
    if source_duration and abs(source_duration - output_duration) > 0.1:
        errors.append("output duration differs by more than 100 ms")
    source_video = primary_video(source) or {}
    output_video = primary_video(output) or {}
    source_frames = _int_value(source_video.get("nb_frames"))
    output_frames = _int_value(output_video.get("nb_frames"))
    if source_frames and output_frames and abs(source_frames - output_frames) > 1:
        errors.append("video frame count differs by more than one frame")


def _side_data(stream: dict[str, Any], marker: str) -> dict[str, Any] | None:
    marker = marker.lower()
    return next(
        (item for item in stream.get("side_data_list", []) or [] if marker in str(item.get("side_data_type", "")).lower()),
        None,
    )


def _has_hdr10plus(stream: dict[str, Any]) -> bool:
    return any(
        re.search(r"HDR10\+|SMPTE2094-40", str(item.get("side_data_type", "")), re.I)
        for item in stream.get("side_data_list", []) or []
    )


def _equivalent_hdr_side_data(
    source: dict[str, Any],
    output: dict[str, Any] | None,
    marker: str,
) -> bool:
    if not output:
        return False
    if marker == "mastering display":
        fields = (
            "red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y",
            "white_point_x", "white_point_y", "min_luminance", "max_luminance",
        )
        try:
            return all(Fraction(str(source.get(field, 0))) == Fraction(str(output.get(field, 0))) for field in fields)
        except (ValueError, ZeroDivisionError):
            return False
    if marker == "content light level":
        return all(
            _int_value(source.get(field)) == _int_value(output.get(field))
            for field in ("max_content", "max_average")
        )
    return source == output


def _enabled_dispositions(stream: dict[str, Any]) -> set[str]:
    return {
        str(name)
        for name, value in stream.get("disposition", {}).items()
        if name != "default" and _int_value(value) == 1
    }


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
