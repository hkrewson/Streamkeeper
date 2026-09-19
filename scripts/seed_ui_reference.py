from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from streamkeeper.database import Database, utcnow
from streamkeeper.discovery import DiscoveryExclusion
from streamkeeper.models import CompatibilityFinding, LibraryType, MediaAsset, ProbeSnapshot, ScanRun


def probe(path: str, video: str, audio: str, channels: int, *, subtitle: str | None = None, peak: int | None = None) -> ProbeSnapshot:
    streams = [
        {"codec_type": "video", "codec_name": video, "width": 3840 if video == "hevc" else 1920, "height": 2160 if video == "hevc" else 1080, "color_transfer": "smpte2084" if video == "hevc" else "bt709", "disposition": {}},
        {"codec_type": "audio", "codec_name": audio, "channels": channels, "tags": {"language": "eng"}, "disposition": {"default": 1}},
    ]
    if subtitle:
        streams.append({"codec_type": "subtitle", "codec_name": subtitle, "tags": {"language": "eng"}, "disposition": {}})
    return ProbeSnapshot(path, utcnow(), {"duration": "7200.0"}, streams, peak_bitrate_bps=peak, tool_version="ffprobe 8.0")


def main() -> None:
    destination = ROOT / "data" / "ui-reference.sqlite3"
    destination.unlink(missing_ok=True)
    db = Database(destination)
    movies = db.add_library("Movies", "/media/movies", "movie")
    television = db.add_library("Television", "/media/television", "tv")
    scan_one = db.create_scan(ScanRun(None, movies, "/media/movies", LibraryType.MOVIE, True, status="completed_with_errors", phase="complete", total_files=1842, processed_files=1842, failed_files=3, started_at="2026-09-17T02:00:00+00:00", finished_at="2026-09-17T05:18:00+00:00", message="Scanned 1,842 files; 3 failed"))
    scan_two = db.create_scan(ScanRun(None, television, "/media/television", LibraryType.TV, False, status="completed", phase="complete", total_files=575, processed_files=575, failed_files=0, started_at="2026-09-18T02:00:00+00:00", finished_at="2026-09-18T02:54:00+00:00", message="Scanned 575 files; 0 failed"))
    scan_three = db.create_scan(ScanRun(None, movies, "/media/movies", LibraryType.MOVIE, True, status="running", phase="deep analysis", total_files=1842, processed_files=1204, failed_files=3, started_at="2026-09-18T14:31:00+00:00", message="Charlie's Angels (2000).mkv"))
    db.update_scan(scan_three, excluded_paths=2)
    db.save_scan_exclusions(scan_three, [
        DiscoveryExclusion(".deletedByTMM", "directory", "Hidden directory", ".*"),
        DiscoveryExclusion("Movie/Samples", "directory", "Directory pattern", "Samples"),
    ])
    samples = [
        (movies, "100 Yards (2024)/100 Yards (2024).mkv", "hevc", "eac3", 6, None, 78_000_000, []),
        (movies, "Howling, The (1981)/Howling, The (1981).mkv", "hevc", "truehd", 8, "hdmv_pgs_subtitle", 121_000_000, [CompatibilityFinding("audio.fallback", "audio", "action", "Apple audio fallback needed", "TrueHD is preserved; an E-AC3 5.1 fallback is planned.", "English E-AC3 5.1"), CompatibilityFinding("subtitle.bitmap", "subtitle", "warning", "Image-based subtitles", "One PGS track may trigger video transcoding.")]),
        (movies, "Casino Royale (1967)/Casino Royale (1967).avi", "mpeg4", "dts", 6, None, 36_000_000, [CompatibilityFinding("video.compatibility", "video", "action", "Video compatibility conversion", "MPEG-4 Part 2 is not a preferred Apple playback format.", "Transcode to HEVC"), CompatibilityFinding("audio.fallback", "audio", "action", "Apple audio fallback needed", "DTS is preserved; an E-AC3 5.1 fallback is planned.", "English E-AC3 5.1")]),
        (movies, "Charlie's Angels (2000)/Charlie's Angels (2000).mkv", "h264", "ac3", 6, "hdmv_pgs_subtitle", 64_000_000, [CompatibilityFinding("subtitle.bitmap", "subtitle", "warning", "Image-based subtitles", "Eighteen PGS tracks are preserved but may trigger transcoding.")]),
        (movies, "Arrival (2016)/Arrival (2016).mkv", "hevc", "eac3", 6, None, 89_000_000, []),
        (television, "Severance/Season 01/Severance S01E01.mkv", "h264", "aac", 6, None, 22_000_000, []),
    ]
    for index, (library, relative, video_codec, audio_codec, channels, subtitle, peak, findings) in enumerate(samples):
        path = f"{'/media/movies' if library == movies else '/media/television'}/{relative}"
        asset = MediaAsset(path, relative, LibraryType.MOVIE if library == movies else LibraryType.TV, 8_000_000_000 - index * 400_000_000, index + 1, "episode" if library == television else "movie", Path(path).stem)
        db.save_asset(library, asset, probe(path, video_codec, audio_codec, channels, subtitle=subtitle, peak=peak), findings, scan_three if library == movies else scan_two)
    db.set_settings({"schedule": "weekly", "network_ceiling_bps": 900_000_000, "retention_days": 90, "fallback_language": "eng"})
    print(destination)


if __name__ == "__main__":
    main()
