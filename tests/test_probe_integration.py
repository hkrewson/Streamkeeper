from pathlib import Path
import shutil
import subprocess

import pytest

from streamkeeper.planner import build_plan
from streamkeeper.probe import probe_file


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools unavailable")
def test_generated_media_can_be_probed_and_planned(tmp_path: Path):
    media = tmp_path / "Tiny Test.mkv"
    subprocess.run(
        [
            shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=1000", "-t", "0.25", "-c:v", "libx264", "-c:a", "aac", str(media),
        ],
        check=True,
    )
    snapshot = probe_file(media)
    plan = build_plan(snapshot)
    assert snapshot.streams
    assert plan.source_path == str(media)
    assert plan.executable is False
