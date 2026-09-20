# Streamkeeper roadmap

The deployed scan-only release is the operational baseline. Work proceeds in
the following order so published images remain testable and conversion stays
behind the established safety gates.

## 1. Continuous integration gates

Status: implemented for tests, container smoke testing, critical image
vulnerabilities, dependency review, dependency updates, and provenance.
Repository-level CodeQL, secret scanning, and push protection still require
enablement in GitHub settings. The published `latest` image is rescanned every
Monday so newly disclosed vulnerabilities are detected between releases.

- Run unit, API, policy, filesystem-safety, and synthetic FFmpeg tests before
  publishing an image.
- Validate JavaScript syntax and the GitHub workflow.
- Start the built container with a writable temporary data mount and require a
  successful health check and SQLite initialization.
- Scan repository configuration and the image for vulnerabilities. Block
  fixable critical findings; report high-severity findings for review.
- Publish the multi-architecture image only after required checks pass.
- Add build provenance and a scheduled weekly scan of the published image.

## 2. Synology deployment hardening

Status: the image-only Compose example, explicit pull policy, and documented
permission, update, backup, restore, and rollback procedures are implemented.
Restart and database-reuse behavior is covered locally; platform confirmation
on both an AMD64 and ARM64 Synology remains pending.

- Maintain an image-only Synology Compose example with explicit pull behavior.
- Document initial data-directory permissions, updates, backups, and recovery.
- Test startup with bind mounts, restarts, image replacement, and an existing
  database on both AMD64 and ARM64 images.

## 3. Discovery exclusions

Status: hidden paths are excluded by default; conservative global defaults and
per-library directory and filename patterns are configurable. Each scan stores
the excluded path, reason, and matching pattern for review in the scan detail.

- Add global and per-library excluded directory and filename patterns.
- Continue excluding hidden directories by default.
- Show exclusions in scan results so ignored files are explainable.
- Cover nested source-code resources, samples, backups, and temporary files
  without excluding legitimate Plex extras.

## 4. Dolby Vision tooling

Status: checksum-pinned `dovi_tool` 2.3.4 binaries are packaged for AMD64 and
ARM64. Both architecture-specific releases execute successfully, and the ARM64
container reports Dolby Vision readiness through the application API. Profile
and compatibility-ID validation against authorized media samples remains part
of the conversion parity gate.

- Package and report a verified `dovi_tool` for AMD64 and ARM64.
- Keep ordinary scanning operational when the optional tool is unavailable.
- Validate Dolby Vision profiles and compatibility IDs with authorized samples.

## 5. Conversion parity gates

Status: controlled synthetic conversion coverage and a normalized comparison
tool are in place. Completed web scans now preserve and export immutable,
CLI-compatible snapshots, removing direct database access from the pending
full-library shadow review. Authorized Dolby Vision, HDR10+, and bitmap
subtitle samples plus off-library playback approval remain required.

- Complete differential dry-run comparison against the frozen shell reference.
- Extend controlled output coverage for Dolby Vision, HDR10+, and bitmap
  subtitles.
- Run a read-only full-library shadow comparison and review classification
  differences.
- Complete selected off-library playback tests before unlocking standalone CLI
  conversion. Web conversion remains a later, separately approved release.

## 6. Limited deep-scan validation

Status: the standalone CLI completed read-only packet analysis over the mounted
SMB library for a 53.6-second extra and a 25.4-minute, 2.34 GB feature. The
feature completed in 104 seconds and measured a 24.73 Mb/s peak. Interrupting a
repeat run stopped the child FFprobe process; the CLI now reports cancellation
without displaying a traceback. Queued and running web scans can now be
cancelled from the Scans page; a live SMB deep probe stopped within 1.5 seconds
of its cancellation request. Broader NAS load testing remains pending.

- Measure peak bitrate on a small representative directory first.
- Confirm network reporting, runtime, cancellation, and NAS load.
- Expand deep analysis only after the limited run is accepted.

## Current operational evidence

- Synology successfully runs the published multi-architecture image.
- A 2,729-file library scan completed and a second scan reused 2,725 successful
  probes while retrying four failed files.
- FFmpeg, FFprobe, and checksum-verified `dovi_tool` 2.3.4 are available in the
  container.
- UTC timestamps are retained in storage and displayed using the configured
  browser, UTC, or fixed DST-aware time zone.
