# Streamkeeper

Streamkeeper is a Python media-analysis package, standalone CLI, and read-only
FastAPI web application intended for a Synology-hosted container. It inventories
movie and television libraries, records scan history, identifies Apple/Plex
compatibility findings, measures peak bitrate, and produces conversion plans.
Normal scans reuse successful probe data for unchanged files; deep scans always
read the media again. Configured history retention is applied at startup and
when the setting changes without removing media or current catalog records.
Each scan records new, modified, removed, unchanged, probed, reused, and failed
file counts for inspection in the web interface and API. Scan history also
records whether a run was started manually, by the daily/weekly scheduler, or
as recovery after an application restart. Repeated requests for the same
library and scan depth reuse an active run rather than queueing duplicates.
Hidden paths and configurable global or per-library glob patterns are excluded
before probing. Scan detail records each excluded path and the rule that matched
it, while standard Plex extra folders remain eligible for scanning.

Conversion execution is deliberately locked in this release. The existing shell
converter remains the behavioral reference until differential and controlled-media
tests prove parity. `streamkeeper convert --dry-run` is available; a non-dry run
returns a migration-gate error without modifying media.

## Local development

```zsh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/streamkeeper-web
```

Open <http://127.0.0.1:8080>. Data defaults to `./data/streamkeeper.sqlite3` and
can be changed with `STREAMKEEPER_DB`.

## CLI

```zsh
streamkeeper scan --path "/Volumes/video/Movies" --library-type movie
streamkeeper scan --path "/Volumes/video/Movies" --library-type movie --deep
streamkeeper plan --path "/path/to/Movie.mkv" --format json
streamkeeper convert --path "/path/to/library" --dry-run
```

Capture two read-only inventory snapshots and compare only stable,
decision-relevant fields:

```zsh
streamkeeper scan --path "/Volumes/video/Movies" --library-type movie --format json > reference.json
streamkeeper scan --path "/Volumes/video/Movies" --library-type movie --format json > candidate.json
streamkeeper compare-scans --reference reference.json --candidate candidate.json
```

The comparison reports missing, added, and classification-changed files while
ignoring absolute roots, capture times, file timestamps, and raw measurement
noise. It exits successfully only when the normalized inventories match, and
can emit machine-readable output with `--format json`.

CLI scans return a nonzero result when no supported media is found or any file
cannot be probed. JSON mode still emits valid structured output for automation,
including per-file errors.

## Container

Published releases are available for both Intel/AMD and ARM systems:

```zsh
docker pull ghcr.io/hkrewson/streamkeeper:latest
```

Every push to `main` publishes `latest` and a commit-specific image tag. A Git
tag such as `v1.2.0` also publishes `1.2.0` and `1.2`, allowing a Synology
deployment to stay on a chosen release line instead of following every change.

Copy `.env.example` to `.env`, adjust the paths and credentials, then run:

```zsh
docker compose up -d --build
```

The compose file mounts media read-only and application data read-write.
On Synology, grant UID 1026 write access to the host folder mapped to `/data`
and read access to the library mapped to `/media`. The verified container
behavior and mount contract are recorded in `docs/container-validation.md`.
For Container Manager, use the image-only `compose.synology.yaml` and follow
the update, backup, restore, and rollback guide in
`docs/synology-deployment.md`.

The web application provides Library, Scans, Findings, Reports, and Settings,
plus JSON APIs under `/api`. It intentionally has no conversion-execution API.
Set `STREAMKEEPER_USER` and `STREAMKEEPER_PASSWORD` to enable HTTP Basic
authentication. Authentication is disabled for trusted local-network evaluation
only when both are omitted. Supplying just one fails closed with a service error
instead of accidentally exposing the application, and the unauthenticated health
check reports the container as unhealthy until the pair is corrected.
`GET /api/tools` reports whether scanning, base conversion, and conditional
Dolby Vision processing are ready, with each executable path, version, and
diagnostic error. Tool checks time out rather than holding the Settings page.
The published image includes checksum-verified `dovi_tool` 2.3.4 binaries for
AMD64 and ARM64. The application still starts and ordinary scans remain usable
when that optional executable is absent from a non-container installation.

## Migration safety

The unchanged reference converter is stored at
`legacy/convert-mkv-for-apple.reference.sh`, with its source hash documented in
`legacy/README.md`. The root `convert-mkv-for-apple.sh` is a Zsh compatibility
launcher for the Python CLI. The policy contract is in `docs/policy-matrix.md`.
Current differential results and unresolved policy questions are recorded in
`docs/parity-status.md`.

Completed runs now offer **Export scan snapshot** in the Scans inspector. The
download uses the standalone CLI's stable scan format, so two exported runs can
be compared without media writes or database access:

```zsh
streamkeeper compare-scans \
  --reference streamkeeper-scan-1.json \
  --candidate streamkeeper-scan-2.json
```

The ordered implementation and deployment work is tracked in
`docs/roadmap.md`.

Python conversion execution remains locked until differential plan parity,
controlled output tests, a read-only full-library shadow scan, and selected
off-library playback tests are reviewed and accepted. The package already
includes isolated modules for command planning, validation, evidence, NFO
updates, and transactional replacement so those gates can be completed without
coupling the core to FastAPI.

## Visual review

`docs/ui-reference` contains the approved prototype and implemented screenshots
at 1440×900 for all five surfaces and 1024×1366 for Library and Settings. Re-run
the capture with an evaluation server listening on port 8087:

```zsh
.venv/bin/python scripts/capture_ui.py
```
