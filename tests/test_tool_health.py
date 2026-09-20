import subprocess
from types import SimpleNamespace

import pytest

from streamkeeper.probe import ProbeError, ffmpeg_has_bitstream_filter, tool_status, tool_version


def test_tool_status_reports_missing_executable(monkeypatch):
    monkeypatch.setattr("streamkeeper.probe.shutil.which", lambda _name: None)

    status = tool_status("ffprobe")

    assert status == {
        "name": "ffprobe",
        "available": False,
        "path": None,
        "version": None,
        "error": "Required tool not found: ffprobe",
    }


def test_tool_status_reports_path_and_version(monkeypatch):
    monkeypatch.setattr("streamkeeper.probe.shutil.which", lambda _name: "/tools/ffprobe")
    monkeypatch.setattr(
        "streamkeeper.probe.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ffprobe version 8.0\n", stderr=""),
    )

    status = tool_status("ffprobe")

    assert status["available"] is True
    assert status["path"] == "/tools/ffprobe"
    assert status["version"] == "ffprobe version 8.0"
    assert status["error"] is None


def test_tool_version_timeout_is_bounded_and_explained(monkeypatch):
    monkeypatch.setattr("streamkeeper.probe.shutil.which", lambda _name: "/tools/ffmpeg")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["ffmpeg", "-version"], 10)

    monkeypatch.setattr("streamkeeper.probe.subprocess.run", timeout)

    with pytest.raises(ProbeError, match="version check timed out"):
        tool_version("ffmpeg")

    status = tool_status("ffmpeg")
    assert status["available"] is False
    assert status["path"] == "/tools/ffmpeg"
    assert "timed out" in str(status["error"])


def test_ffmpeg_bitstream_filter_capability_is_detected(monkeypatch):
    monkeypatch.setattr("streamkeeper.probe.shutil.which", lambda _name: "/tools/ffmpeg")
    monkeypatch.setattr(
        "streamkeeper.probe.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="Bitstream filters:\nsetts\ndovi_rpu\n", stderr=""
        ),
    )

    assert ffmpeg_has_bitstream_filter("dovi_rpu") is True
    assert ffmpeg_has_bitstream_filter("missing_filter") is False


def test_ffmpeg_bitstream_filter_capability_failure_is_explained(monkeypatch):
    monkeypatch.setattr("streamkeeper.probe.shutil.which", lambda _name: "/tools/ffmpeg")
    monkeypatch.setattr(
        "streamkeeper.probe.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="broken build"),
    )

    with pytest.raises(ProbeError, match="broken build"):
        ffmpeg_has_bitstream_filter("dovi_rpu")
