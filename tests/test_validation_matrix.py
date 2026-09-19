from streamkeeper.models import ProbeSnapshot
from streamkeeper.planner import build_plan
from streamkeeper.validation import validate_output


def source_snapshot(*, hdr=False):
    video = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264" if not hdr else "hevc",
        "pix_fmt": "yuv420p" if not hdr else "yuv420p10le",
        "color_transfer": "bt709" if not hdr else "smpte2084",
        "color_primaries": "bt709" if not hdr else "bt2020",
        "color_space": "bt709" if not hdr else "bt2020nc",
        "nb_frames": "240",
        "disposition": {},
        "side_data_list": (
            [
                {"side_data_type": "Mastering display metadata", "max_luminance": "1000/1"},
                {"side_data_type": "Content light level metadata", "max_content": 1000},
            ]
            if hdr
            else []
        ),
    }
    return ProbeSnapshot(
        "/media/Fixture.mkv",
        "2026-01-01T00:00:00Z",
        {"duration": "10.0"},
        [
            video,
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "flac",
                "channels": 2,
                "tags": {"language": "eng", "title": "Main"},
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "ass",
                "tags": {"language": "eng", "title": "Signs"},
                "disposition": {"forced": 1},
            },
            {
                "index": 3,
                "codec_type": "subtitle",
                "codec_name": "mov_text",
                "tags": {"language": "spa"},
                "disposition": {},
            },
            {
                "index": 4,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "disposition": {"attached_pic": 1},
            },
        ],
        chapters=[{"id": 1, "start_time": "0", "end_time": "10"}],
    )


def valid_output(source: ProbeSnapshot, *, hdr=False):
    video = dict(source.streams[0])
    video["nb_frames"] = "241"
    return ProbeSnapshot(
        "/media/Fixture.output.mkv",
        "2026-01-01T00:00:01Z",
        {"duration": "10.05"},
        [
            video,
            {
                "codec_type": "audio",
                "codec_name": "flac",
                "channels": 2,
                "tags": {"language": "eng", "title": "English FLAC 2.0"},
                "disposition": {"default": 0},
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "tags": {"language": "eng", "title": "English AAC 2.0"},
                "disposition": {"default": 1},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "ass",
                "tags": {"language": "eng", "title": "Signs"},
                "disposition": {"forced": 1},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "spa"},
                "disposition": {},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng", "title": "Signs - SRT Compatibility"},
                "disposition": {},
            },
            {
                "codec_type": "attachment",
                "codec_name": "mjpeg",
                "tags": {"filename": "cover-1.jpg", "mimetype": "image/jpeg"},
                "disposition": {},
            },
        ],
        chapters=list(source.chapters),
    )


def test_semantic_validation_accepts_complete_output():
    source = source_snapshot()
    plan = build_plan(source)
    result = validate_output(source, valid_output(source), plan)
    assert result["passed"] is True
    assert result["errors"] == []


def test_semantic_validation_rejects_audio_subtitle_chapter_and_cover_loss():
    source = source_snapshot()
    plan = build_plan(source)
    output = valid_output(source)
    output.streams[0]["nb_frames"] = "230"
    output.streams[1]["channels"] = 1
    output.streams[2]["disposition"]["default"] = 0
    output.streams = [stream for stream in output.streams if stream["codec_type"] not in {"subtitle", "attachment"}]
    output.chapters = []

    errors = validate_output(source, output, plan)["errors"]
    assert "source audio track 1 channel count changed" in errors
    assert "generated compatibility audio is missing" not in errors
    assert any("default audio tracks" in error for error in errors)
    assert any("subtitle count" in error for error in errors)
    assert "one or more chapters are missing" in errors
    assert "one or more attachments or embedded covers are missing" in errors
    assert "video frame count differs by more than one frame" in errors


def test_hdr_validation_rejects_lost_signaling_and_metadata():
    source = source_snapshot(hdr=True)
    plan = build_plan(source)
    output = valid_output(source, hdr=True)
    output_video = output.streams[0]
    output_video["pix_fmt"] = "yuv420p"
    output_video["color_transfer"] = "bt709"
    output_video["color_primaries"] = "bt709"
    output_video["color_space"] = "bt709"
    output_video["side_data_list"] = []

    errors = validate_output(source, output, plan)["errors"]
    assert "copied video pixel format changed" in errors
    assert "HDR output is not 10/12-bit" in errors
    assert "HDR transfer metadata changed" in errors
    assert "HDR color primaries changed" in errors
    assert "HDR color matrix changed" in errors
    assert "HDR mastering-display metadata changed or is missing" in errors
    assert "HDR content-light metadata changed or is missing" in errors
