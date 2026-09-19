from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterator

from .models import LibraryType, MediaAsset

SUPPORTED_EXTENSIONS = {".mkv", ".avi", ".ts", ".m2ts", ".mts", ".mp4", ".m4v"}
DEFAULT_EXCLUDED_DIRECTORIES = ["@eaDir", "node_modules", "__pycache__"]
DEFAULT_EXCLUDED_FILES = ["sample.mkv", "sample.mp4", "sample.m4v", "sample.avi"]
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


@dataclass(slots=True, frozen=True)
class DiscoveryExclusion:
    relative_path: str
    kind: str
    reason: str
    pattern: str | None = None


@dataclass(slots=True)
class DiscoveryResult:
    assets: list[MediaAsset] = field(default_factory=list)
    exclusions: list[DiscoveryExclusion] = field(default_factory=list)


def _matching_pattern(relative_path: str, name: str, patterns: list[str]) -> str | None:
    relative = relative_path.replace("\\", "/").casefold()
    basename = name.casefold()
    for raw_pattern in patterns:
        pattern = str(raw_pattern).strip().replace("\\", "/")
        if not pattern:
            continue
        target = relative if "/" in pattern else basename
        if fnmatchcase(target, pattern.casefold()):
            return pattern
    return None


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


def discover_with_exclusions(
    path: str | Path,
    library_type: LibraryType,
    *,
    excluded_directories: list[str] | None = None,
    excluded_files: list[str] | None = None,
) -> DiscoveryResult:
    target = Path(path).expanduser().resolve()
    directory_patterns = (
        excluded_directories if excluded_directories is not None else DEFAULT_EXCLUDED_DIRECTORIES
    )
    file_patterns = excluded_files if excluded_files is not None else DEFAULT_EXCLUDED_FILES
    result = DiscoveryResult()
    if target.is_file():
        pattern = _matching_pattern(target.name, target.name, file_patterns)
        if pattern and target.suffix.lower() in SUPPORTED_EXTENSIONS:
            result.exclusions.append(DiscoveryExclusion(target.name, "file", "Filename pattern", pattern))
        elif is_supported(target):
            result.assets.append(asset_for(target, target.parent, library_type))
        return result
    if not target.is_dir():
        raise FileNotFoundError(target)
    found: list[Path] = []
    for directory, names, files in os.walk(target):
        retained_directories: list[str] = []
        for name in sorted(names, key=str.casefold):
            candidate = Path(directory, name)
            relative = candidate.relative_to(target).as_posix()
            if name.startswith("."):
                result.exclusions.append(DiscoveryExclusion(relative, "directory", "Hidden directory", ".*"))
                continue
            pattern = _matching_pattern(relative, name, directory_patterns)
            if pattern:
                result.exclusions.append(DiscoveryExclusion(relative, "directory", "Directory pattern", pattern))
                continue
            retained_directories.append(name)
        names[:] = retained_directories
        for filename in sorted(files, key=str.casefold):
            candidate = Path(directory, filename)
            if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            relative = candidate.relative_to(target).as_posix()
            if filename.startswith("."):
                reason = "AppleDouble metadata" if filename.startswith("._") else "Hidden file"
                result.exclusions.append(DiscoveryExclusion(relative, "file", reason, ".*"))
                continue
            if " original." in filename.lower():
                result.exclusions.append(DiscoveryExclusion(relative, "file", "Original backup", "* Original.*"))
                continue
            pattern = _matching_pattern(relative, filename, file_patterns)
            if pattern:
                result.exclusions.append(DiscoveryExclusion(relative, "file", "Filename pattern", pattern))
                continue
            found.append(candidate)
    for candidate in found:
        result.assets.append(asset_for(candidate, target, library_type))
    return result


def discover(path: str | Path, library_type: LibraryType) -> Iterator[MediaAsset]:
    yield from discover_with_exclusions(path, library_type).assets
