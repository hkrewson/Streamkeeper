from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

from .models import AudioPlan, ProbeSnapshot, audio_streams, primary_video, subtitle_streams
from .policy import LANGUAGE_NAMES, planned_audio_labels, resolved_language


def normalized_commands(
    snapshot: ProbeSnapshot,
    *,
    video_action: str,
    audio: AudioPlan,
    output_path: str,
) -> list[list[str]]:
    """Build stable argument vectors for review. They are not executable until parity is accepted."""
    source = snapshot.path
    output = Path(output_path)
    staged = str(output.with_name(f".{output.name}.streamkeeper.partial.mkv"))
    attachments = _attached_pictures(snapshot, output)
    attachment_commands = [
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", source,
            "-map", f"0:{item['index']}", "-frames:v", "1", "-c", "copy",
            "-update", "1", "-f", "image2", str(item["path"]),
        ]
        for item in attachments
    ]
    if video_action in {"dovi_convert", "dovi_convert_strip_hdr10plus", "strip_hdr10plus"}:
        base = str(output.with_name(f".{output.stem}.base.hevc"))
        normalized = str(output.with_name(f".{output.stem}.normalized.hevc"))
        dovi = ["dovi_tool"]
        if video_action in {"dovi_convert_strip_hdr10plus", "strip_hdr10plus"}:
            dovi.append("--drop-hdr10plus")
        if video_action.startswith("dovi_convert"):
            dovi += ["-m", "2", "convert", "--discard", base, "-o", normalized]
        else:
            dovi += ["convert", base, "-o", normalized]
        return [
            *attachment_commands,
            ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", source, "-map", "0:v:0", "-c:v", "copy", "-bsf:v", "hevc_mp4toannexb", "-an", "-sn", "-dn", "-f", "hevc", base],
            dovi,
            _remux_command(snapshot, normalized, staged, audio, attachments, external_video=True),
        ]
    return [
        *attachment_commands,
        _remux_command(snapshot, source, staged, audio, attachments, video_action=video_action),
    ]


def _remux_command(
    snapshot: ProbeSnapshot,
    input_path: str,
    staged: str,
    audio: AudioPlan,
    attachments: list[dict[str, object]],
    *,
    video_action: str = "copy",
    external_video: bool = False,
) -> list[str]:
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", input_path]
    if external_video:
        command += ["-i", snapshot.path, "-map", "0:v:0", "-map", "1", "-map", "-1:v:0"]
        source_input = "1"
    else:
        command += ["-map", "0"]
        source_input = "0"

    for item in attachments:
        command += ["-map", f"-{source_input}:{item['index']}"]

    data_streams = [stream for stream in snapshot.streams if stream.get("codec_type") == "data"]
    if data_streams:
        command += ["-map", f"-{source_input}:d"]

    subtitles = subtitle_streams(snapshot)
    fallback_subtitles = [
        (ordinal, stream)
        for ordinal, stream in enumerate(subtitles)
        if stream.get("codec_name") in {"ass", "ssa"}
    ]
    for ordinal, _stream in fallback_subtitles:
        command += ["-map", f"{source_input}:s:{ordinal}"]

    if audio.action == "transcode" and audio.source_ordinal is not None:
        command += ["-map", f"{source_input}:a:{audio.source_ordinal}"]

    command += ["-map_metadata", source_input, "-map_chapters", source_input]
    command += ["-c", "copy"]

    stream_ordinals: dict[str, int] = {"video": 0, "subtitle": 0, "data": 0, "attachment": 0}
    stream_specifiers = {"video": "v", "subtitle": "s", "data": "d", "attachment": "t"}
    for stream in snapshot.streams:
        stream_type = str(stream.get("codec_type", ""))
        if stream_type == "video" and int(stream.get("disposition", {}).get("attached_pic", 0) or 0) == 1:
            continue
        if stream_type not in stream_ordinals or stream_type == "data" and data_streams:
            continue
        ordinal = stream_ordinals[stream_type]
        stream_ordinals[stream_type] += 1
        disposition = _disposition(stream.get("disposition", {}))
        command += [f"-disposition:{stream_specifiers[stream_type]}:{ordinal}", disposition]

    if video_action == "transcode_hevc":
        command += _hevc_transcode_arguments(snapshot)
    if audio.action == "transcode" and audio.codec and audio.channels and audio.bitrate:
        ordinal = len(audio_streams(snapshot))
        command += [
            f"-c:a:{ordinal}", audio.codec,
            f"-b:a:{ordinal}", str(audio.bitrate),
        ]
        if audio.channels >= 6 or audio.channels <= 2:
            command += [f"-ac:a:{ordinal}", str(audio.channels)]

    for ordinal, stream in enumerate(subtitles):
        if stream.get("codec_name") == "mov_text":
            command += [f"-c:s:{ordinal}", "srt"]
    for generated, (_source_ordinal, stream) in enumerate(fallback_subtitles, start=len(subtitles)):
        language = resolved_language(stream)
        old_title = str(stream.get("tags", {}).get("title", ""))
        title = f"{old_title} - SRT Compatibility" if old_title else f"{LANGUAGE_NAMES.get(language, language)} SRT Compatibility"
        command += [
            f"-c:s:{generated}", "srt",
            f"-metadata:s:s:{generated}", f"title={title}",
            f"-metadata:s:s:{generated}", f"language={language}",
            f"-disposition:s:{generated}", "0",
        ]

    existing_attachment_count = sum(stream.get("codec_type") == "attachment" for stream in snapshot.streams)
    for offset, item in enumerate(attachments, start=existing_attachment_count):
        command += [
            "-attach", str(item["path"]),
            f"-metadata:s:t:{offset}", f"filename={item['filename']}",
            f"-metadata:s:t:{offset}", f"mimetype={item['mimetype']}",
        ]

    for label in planned_audio_labels(snapshot):
        ordinal = label["ordinal"]
        stream = audio_streams(snapshot)[ordinal]
        disposition = dict(stream.get("disposition", {}))
        disposition["default"] = 1 if audio.default_ordinal == ordinal else 0
        command += [
            f"-metadata:s:a:{ordinal}", f"title={label['label']}",
            f"-metadata:s:a:{ordinal}", f"language={label['language']}",
            f"-disposition:a:{ordinal}", _disposition(disposition),
        ]
    if audio.action == "transcode" and audio.default_ordinal is not None:
        ordinal = audio.default_ordinal
        command += [
            f"-metadata:s:a:{ordinal}", f"title={audio.label or ''}",
            f"-metadata:s:a:{ordinal}", f"language={audio.language or 'eng'}",
            f"-disposition:a:{ordinal}", "default",
        ]
    command += ["-max_muxing_queue_size", "4096", "-f", "matroska", staged]
    return command


def _disposition(disposition: object) -> str:
    if not isinstance(disposition, dict):
        return "0"
    enabled = [str(name) for name, value in disposition.items() if int(value or 0) == 1]
    return "+".join(enabled) if enabled else "0"


def _attached_pictures(snapshot: ProbeSnapshot, output: Path) -> list[dict[str, object]]:
    pictures: list[dict[str, object]] = []
    for ordinal, stream in enumerate(
        stream
        for stream in snapshot.streams
        if stream.get("codec_type") == "video"
        and int(stream.get("disposition", {}).get("attached_pic", 0) or 0) == 1
    ):
        codec = str(stream.get("codec_name", "unknown"))
        extension, default_mimetype = {
            "mjpeg": ("jpg", "image/jpeg"),
            "png": ("png", "image/png"),
            "webp": ("webp", "image/webp"),
        }.get(codec, (codec, "application/octet-stream"))
        tags = stream.get("tags", {})
        filename = str(tags.get("filename") or tags.get("FILENAME") or f"cover-{ordinal + 1}.{extension}")
        mimetype = str(tags.get("mimetype") or tags.get("MIMETYPE") or default_mimetype)
        pictures.append(
            {
                "index": int(stream.get("index", 0)),
                "path": output.with_name(f".{output.stem}.attachment-{ordinal + 1}.{extension}"),
                "filename": Path(filename).name,
                "mimetype": mimetype,
            }
        )
    return pictures


def _hevc_transcode_arguments(snapshot: ProbeSnapshot) -> list[str]:
    video = primary_video(snapshot) or {}
    transfer = str(video.get("color_transfer", ""))
    parameters = ["repeat-headers=1"]
    arguments = ["-c:v:0", "libx265", "-preset:v:0", "slow", "-crf:v:0", "18"]
    if transfer == "smpte2084":
        parameters += ["hdr10=1", "hdr10-opt=1"]
        arguments += [
            "-pix_fmt:v:0", "yuv420p10le",
            "-color_primaries:v:0", "bt2020",
            "-color_trc:v:0", "smpte2084",
            "-colorspace:v:0", "bt2020nc",
        ]
    elif transfer == "arib-std-b67":
        parameters += ["colorprim=bt2020", "transfer=arib-std-b67", "colormatrix=bt2020nc"]
        arguments += [
            "-pix_fmt:v:0", "yuv420p10le",
            "-color_primaries:v:0", "bt2020",
            "-color_trc:v:0", "arib-std-b67",
            "-colorspace:v:0", "bt2020nc",
        ]
    else:
        arguments += ["-pix_fmt:v:0", "yuv420p"]

    mastering = _side_data(video, "mastering display")
    if mastering:
        parameters.append(f"master-display={_master_display(mastering)}")
    light = _side_data(video, "content light level")
    if light:
        parameters.append(
            f"max-cll={int(light.get('max_content', 0) or 0)},{int(light.get('max_average', 0) or 0)}"
        )
    arguments += ["-x265-params:v:0", ":".join(parameters)]
    return arguments


def _side_data(stream: dict[str, Any], marker: str) -> dict[str, Any] | None:
    marker = marker.lower()
    return next(
        (
            item
            for item in stream.get("side_data_list", []) or []
            if marker in str(item.get("side_data_type", "")).lower()
        ),
        None,
    )


def _master_display(item: dict[str, Any]) -> str:
    def scaled(name: str, scale: int) -> int:
        try:
            return round(float(Fraction(str(item.get(name, 0)))) * scale)
        except (ValueError, ZeroDivisionError):
            return 0

    return (
        f"G({scaled('green_x', 50_000)},{scaled('green_y', 50_000)})"
        f"B({scaled('blue_x', 50_000)},{scaled('blue_y', 50_000)})"
        f"R({scaled('red_x', 50_000)},{scaled('red_y', 50_000)})"
        f"WP({scaled('white_point_x', 50_000)},{scaled('white_point_y', 50_000)})"
        f"L({scaled('max_luminance', 10_000)},{scaled('min_luminance', 10_000)})"
    )
