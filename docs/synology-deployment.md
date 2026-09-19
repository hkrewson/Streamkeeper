# Synology deployment

This deployment uses the published multi-architecture image. It does not build
source code on the NAS. The media library remains read-only in the scan-only
release, while Streamkeeper's database is stored separately in a writable
folder.

## Before creating the project

1. Create `/volume1/docker/streamkeeper/data` in File Station.
2. Give container UID `1026` read and write access to that folder. Give it read
   access to the media folder.
3. Copy `compose.synology.yaml` into a project folder on the NAS as
   `compose.yaml`. Copy `synology.env.example` into the same folder as `.env`.
4. Change the password and confirm the media and data paths in `.env`.

If Container Manager reports a database permission error, use an administrator
SSH session to apply ownership to the dedicated data folder only:

```zsh
sudo chown -R 1026:1026 /volume1/docker/streamkeeper/data
```

Do not apply that command to the media library.

## Create the Container Manager project

In **Container Manager → Project**, create a project from the folder containing
`compose.synology.yaml` and `.env`. The default address is:

```text
http://YOUR-SYNOLOGY-IP:8087
```

The project always checks the registry when it is created or recreated. The
default `latest` tag follows successful builds from `main`. Set
`STREAMKEEPER_TAG` to a version such as `1.2.0` when the deployment should stay
on a fixed release.

## Update

Use Container Manager's **Update** or **Build → Rebuild** action for the project
and select the option to pull the image again. From an SSH session, the
equivalent commands in the project folder are:

```zsh
docker compose --env-file .env -f compose.synology.yaml pull
docker compose --env-file .env -f compose.synology.yaml up -d
```

Confirm that the project reports **Healthy**, then open Settings → Tools and
confirm FFmpeg and FFprobe are ready. The database and settings survive image
replacement because `/data` is outside the container.

## Back up

Stop the Streamkeeper project before copying its data folder. This ensures that
SQLite has finished writing and avoids an incomplete database snapshot. Back up
the whole folder mapped by `STREAMKEEPER_DATA_PATH`, not just the main database
file.

The media library does not need to be included in this application backup.
Streamkeeper can rebuild scan data from the library, but a database backup also
preserves scan history, findings, library definitions, and settings.

## Restore

1. Stop the Streamkeeper project.
2. Move the current data folder aside; do not overwrite it until the restored
   project has been verified.
3. Restore the backed-up data folder to `STREAMKEEPER_DATA_PATH`.
4. Reapply UID `1026` ownership if the restored files have different ownership.
5. Start the project and confirm its health, libraries, and recent scan history.

## Roll back an image

Set `STREAMKEEPER_TAG` in `.env` to a previously working version or immutable
`sha-...` tag, pull again, and recreate the project. Do not delete the data
folder during rollback. Make a data backup first if the newer release has
already migrated the database.

## Mount contract

| Container path | Access | Purpose |
| --- | --- | --- |
| `/media` | Read-only | Movie, television, and mixed library roots |
| `/data` | Read-write | SQLite database and durable application state |

Web conversion remains disabled. A future conversion release will use an
explicitly writable test mount before any production library is allowed to be
modified.
