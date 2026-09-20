# Container validation

The scan-only image was rebuilt and exercised on 2026-09-18 using Docker's
Linux ARM64 runtime. The same Dockerfile uses multi-architecture Debian and
Python base packages for x86-64 Synology models.

## Verified behavior

- `docker compose config` expands successfully with media mounted at `/media`
  read-only and application data mounted at `/data` read-write.
- The image builds without project files outside the declared build context.
- The service starts as the unprivileged `streamkeeper` user (UID 1026).
- SQLite can create and migrate its database in `/data`.
- A write attempt through the `/media` mount is rejected.
- FFprobe is installed in the runtime image.
- `/health` responds successfully and Docker reports the container as healthy.
- The image-only Synology Compose file expands without a local build context.
- Replacing the container while reusing its named data volume preserves the
  initialized SQLite database and returns to a healthy state.
- The temporary verification container was removed after the check.

## Synology volume permissions

The host folder mapped to `/data` must be writable by UID 1026. The library
folder mapped to `/media` only needs read and directory-traversal permission for
that user. Container Manager should keep the library mount read-only for web
version 1. Conversion testing belongs in a separate explicitly writable test
mount after the CLI parity gate is accepted.

The image bundles `dovi_tool` 2.3.4 from the project's official static Linux
releases. AMD64 and ARM64 archives are selected by the container target
architecture and verified against pinned SHA-256 digests before installation.
Both binaries have been executed on their target architecture, and the ARM64
image has passed the application health and `/api/tools` readiness checks.

`dovi_tool` remains a conditional conversion dependency rather than an
application startup dependency. Its absence from a non-container installation
is reported on the Settings page without disabling inventory, FFprobe analysis,
findings, or ordinary dry-run planning.
