from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def update_nfo(path: str | Path, evidence_name: str, summary: str) -> bytes:
    """Return updated XML bytes without touching disk; installation is transactional elsewhere."""
    nfo_path = Path(path)
    tree = ET.parse(nfo_path)
    root = tree.getroot()
    node = root.find("plexconvert")
    if node is None:
        node = ET.SubElement(root, "plexconvert")
    node.set("evidence", evidence_name)
    node.text = summary
    ET.indent(tree, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def validate_nfo(data: bytes) -> None:
    ET.fromstring(data)
