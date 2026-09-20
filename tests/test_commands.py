import json
from pathlib import Path

from streamkeeper.models import ProbeSnapshot
from streamkeeper.planner import build_plan


FIXTURES = Path(__file__).parent / "fixtures"


def test_normalized_command_is_noninteractive_and_preserves_streams():
    snapshot = ProbeSnapshot(
        "/media/Test.mkv", "2026-01-01T00:00:00Z", {"duration": "1"},
        [
            {"codec_type": "video", "codec_name": "hevc", "color_transfer": "bt709", "disposition": {}},
            {"codec_type": "audio", "codec_name": "truehd", "channels": 8, "tags": {"language": "eng"}, "disposition": {}},
        ],
    )
    plan = build_plan(snapshot)
    command = plan.normalized_commands[0]
    assert command[:4] == ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    assert ["-map", "0"] == command[command.index("-map"):command.index("-map") + 2]
    assert "-c:a:1" in command
    assert plan.executable is False


def test_normalized_commands_preserve_cover_and_subtitle_contract():
    snapshot = ProbeSnapshot(
        "/media/Fixture.mkv", "2026-01-01T00:00:00Z", {"duration": "1"},
        [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "color_transfer": "bt709", "disposition": {"default": 1}},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "eng"}, "disposition": {"default": 1, "original": 1}},
            {"index": 2, "codec_type": "subtitle", "codec_name": "ass", "tags": {"language": "eng", "title": "Signs"}, "disposition": {"forced": 1}},
            {"index": 3, "codec_type": "subtitle", "codec_name": "mov_text", "tags": {"language": "spa"}, "disposition": {}},
            {"index": 4, "codec_type": "data", "codec_name": "bin_data", "disposition": {}},
            {"index": 5, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}, "tags": {"filename": "poster.jpg"}},
        ],
    )
    plan = build_plan(snapshot)

    assert len(plan.normalized_commands) == 2
    extraction, remux = plan.normalized_commands
    assert extraction[extraction.index("-map"):extraction.index("-map") + 2] == ["-map", "0:5"]
    assert "-0:5" in remux
    assert "-0:d" in remux
    assert ["-map", "0:s:0"] == remux[remux.index("0:s:0") - 1:remux.index("0:s:0") + 1]
    assert "-c:s:1" in remux
    assert "-c:s:2" in remux
    assert "-attach" in remux
    assert "filename=poster.jpg" in remux
    assert "mimetype=image/jpeg" in remux
    assert any("original" in argument for argument in remux)
    assert remux[-3:] == ["-f", "matroska", "/media/.Fixture.mkv.streamkeeper.partial.mkv"]


def test_real_vobsub_extra_plan_preserves_bitmap_track_and_plex_naming():
    snapshot = ProbeSnapshot(**json.loads((FIXTURES / "real_vobsub_extra.json").read_text()))
    plan = build_plan(snapshot, media_kind="extra", extra_type="featurette")

    assert plan.output_path.endswith("Original Trailer-featurette.mkv")
    assert plan.video_action == "copy"
    assert plan.audio.action == "none"
    assert plan.audio.default_ordinal == 0
    assert plan.subtitles.retained == 1
    assert plan.subtitles.bitmap_warnings == 1
    assert plan.normalized_commands[-1][:4] == ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
