from pathlib import Path

from streamkeeper.planner import matching_nfo


def test_matching_nfo_prefers_valid_same_stem_sidecar(tmp_path: Path):
    media = tmp_path / "Movie (2026).mkv"
    media.touch()
    sidecar = tmp_path / "Movie (2026).nfo"
    sidecar.write_text("<movie><title>Movie</title></movie>")

    assert matching_nfo(media) == sidecar


def test_matching_nfo_accepts_episode_sidecar_but_never_tvshow_nfo(tmp_path: Path):
    episode = tmp_path / "Show S01E01.mkv"
    episode.touch()
    tvshow = tmp_path / "tvshow.nfo"
    tvshow.write_text("<tvshow><title>Show</title></tvshow>")

    assert matching_nfo(episode) is None

    episode_nfo = tmp_path / "Show S01E01.nfo"
    episode_nfo.write_text("<episodedetails><title>Episode</title></episodedetails>")
    assert matching_nfo(episode) == episode_nfo


def test_folder_movie_nfo_requires_exactly_one_media_file(tmp_path: Path):
    primary = tmp_path / "Feature.mkv"
    primary.touch()
    movie_nfo = tmp_path / "movie.nfo"
    movie_nfo.write_text("<movie><title>Movie</title></movie>")
    assert matching_nfo(primary) == movie_nfo

    (tmp_path / "Alternate.mp4").touch()
    assert matching_nfo(primary) is None


def test_matching_nfo_rejects_invalid_or_wrong_root_sidecars(tmp_path: Path):
    media = tmp_path / "Movie.mkv"
    media.touch()
    sidecar = tmp_path / "Movie.nfo"
    sidecar.write_text("not xml")
    assert matching_nfo(media) is None

    sidecar.write_text("<tvshow><title>Wrong scope</title></tvshow>")
    assert matching_nfo(media) is None


def test_matching_nfo_supports_renamed_extra_sidecar(tmp_path: Path):
    media = tmp_path / "Interview.mp4"
    media.touch()
    renamed = tmp_path / "Interview-interview.nfo"
    renamed.write_text("<movie><title>Interview</title></movie>")

    assert matching_nfo(media, "Interview-interview") == renamed
