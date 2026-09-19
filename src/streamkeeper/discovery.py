from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator

from .models import LibraryType, MediaAsset

SUPPORTED_EXTENSIONS = {".mkv", ".avi", ".ts", ".m2ts", ".mts", ".mp4", ".m4v"}
EXTRA_FOLDER_TYPES = {
    "behind the scenes": "behindthescenes",
    "deleted scenes": "deleted",
    "featurettes": "featurette",
    "interviews": "interview",
    "scenes": "scene",
    "shorts": "short",
    "trailers": "trailer",
    "other": "other",
}
EPISODE_PATTERN = re.compile(r"(?i)\bS\d{1,2}E\d{1,3}\b")


def is_supported(path: Path) -> bool:
    name = path.name
    return (
        path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not name.startswith("._")
        and " original." not in name.lower()
    )


def classify(path: Path, root: Path, library_type: LibraryType) -> tuple[str, str | None]:
    relative_parts = path.relative_to(root).parts
    extra_type = next(
        (EXTRA_FOLDER_TYPES[part.lower()] for part in relative_parts[:-1] if part.lower() in EXTRA_FOLDER_TYPES),
        None,
    )
    if extra_type:
        return "extra", extra_type
    if library_type == LibraryType.TV or EPISODE_PATTERN.search(path.stem):
        return "episode", None
    if library_type == LibraryType.MIXED and len(relative_parts) >= 3 and any(
        part.lower().startswith("season ") for part in relative_parts
    ):
        return "episode", None
    return "movie", None


def asset_for(path: Path, root: Path, library_type: LibraryType) -> MediaAsset:
    stat = path.stat()
    media_kind, extra_type = classify(path, root, library_type)
    return MediaAsset(
        path=str(path.resolve()),
        relative_path=str(path.relative_to(root)),
        library_type=library_type,
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        media_kind=media_kind,
        title=path.stem,
        extra_type=extra_type,
    )


def discover(path: str | Path, library_type: LibraryType) -> Iterator[MediaAsset]:
    target = Path(path).expanduser().resolve()
    if target.is_file():
        if is_supported(target):
            yield asset_for(target, target.parent, library_type)
        return
    if not target.is_dir():
        raise FileNotFoundError(target)
    found: list[Path] = []
    for directory, names, files in os.walk(target):
        names[:] = [name for name in names if not name.startswith(".")]
        names.sort(key=str.casefold)
        for filename in sorted(files, key=str.casefold):
            candidate = Path(directory, filename)
            if is_supported(candidate):
                found.append(candidate)
    for candidate in found:
        yield asset_for(candidate, target, library_type)
