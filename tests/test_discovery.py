from pathlib import Path

from streamkeeper.discovery import classify, discover
from streamkeeper.models import LibraryType


def test_discovery_is_sorted_and_skips_backups(tmp_path: Path):
    (tmp_path / "B.mkv").write_bytes(b"b")
    (tmp_path / "a.mp4").write_bytes(b"a")
    (tmp_path / "a Original.mkv").write_bytes(b"backup")
    (tmp_path / "._a.mkv").write_bytes(b"appledouble")
    assert [item.relative_path for item in discover(tmp_path, LibraryType.MIXED)] == ["a.mp4", "B.mkv"]


def test_discovery_skips_hidden_directories_at_any_depth(tmp_path: Path):
    visible = tmp_path / "Movie (2024)" / "Movie (2024).mkv"
    root_hidden = tmp_path / ".deletedByTMM" / "Old Movie (2020).mkv"
    nested_hidden = tmp_path / "Movie (2024)" / ".working" / "temporary.mkv"

    visible.parent.mkdir(parents=True)
    root_hidden.parent.mkdir(parents=True)
    nested_hidden.parent.mkdir(parents=True)
    visible.touch()
    root_hidden.touch()
    nested_hidden.touch()

    assert [item.relative_path for item in discover(tmp_path, LibraryType.MOVIE)] == [
        "Movie (2024)/Movie (2024).mkv"
    ]


def test_classifies_episode_and_folder_defined_extra(tmp_path: Path):
    episode = tmp_path / "Show" / "Season 01" / "Show S01E02.mkv"
    extra = tmp_path / "Movie" / "Featurettes" / "Deleted Scene.mkv"
    episode.parent.mkdir(parents=True)
    extra.parent.mkdir(parents=True)
    episode.touch()
    extra.touch()
    assert classify(episode, tmp_path, LibraryType.MIXED) == ("episode", None)
    assert classify(extra, tmp_path, LibraryType.MOVIE) == ("extra", "featurette")
