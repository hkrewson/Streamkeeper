from pathlib import Path
import xml.etree.ElementTree as ET

from streamkeeper.nfo import update_nfo, validate_nfo


def test_nfo_preserves_unrelated_nodes_and_adds_evidence(tmp_path: Path):
    path = tmp_path / "movie.nfo"
    path.write_text("<movie><title>Test</title><uniqueid>abc</uniqueid></movie>")
    updated = update_nfo(path, "Test.conversion.txt", "Compatibility conversion completed")
    validate_nfo(updated)
    root = ET.fromstring(updated)
    assert root.findtext("title") == "Test"
    assert root.findtext("uniqueid") == "abc"
    assert root.find("streamkeeper").attrib["evidence"] == "Test.conversion.txt"


def test_episode_nfo_preserves_episode_structure_and_replaces_existing_evidence(tmp_path: Path):
    path = tmp_path / "Show S01E02.nfo"
    path.write_text(
        "<episodedetails><title>Second Episode</title><season>1</season><episode>2</episode>"
        "<uniqueid type=\"tvdb\">12345</uniqueid><plexconvert evidence=\"old.txt\">Old</plexconvert>"
        "</episodedetails>",
        encoding="utf-8",
    )

    updated = update_nfo(path, "Show S01E02.conversion.txt", "Validated stream conversion")
    validate_nfo(updated)
    root = ET.fromstring(updated)

    assert root.tag == "episodedetails"
    assert root.findtext("title") == "Second Episode"
    assert root.findtext("season") == "1"
    assert root.findtext("episode") == "2"
    assert root.find("uniqueid").attrib["type"] == "tvdb"
    assert root.find("streamkeeper").attrib["evidence"] == "Show S01E02.conversion.txt"
    assert root.findtext("streamkeeper") == "Validated stream conversion"
    assert len(root.findall("streamkeeper")) == 1
    assert root.find("plexconvert") is None
