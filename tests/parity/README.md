# Differential parity environment

The frozen shell converter currently resolves an obsolete Intel-only `jq` on
the development Mac. This disposable image supplies a compatible `jq` without
changing the host or modifying the reference script.

Build it after the application image:

```zsh
docker build -t streamkeeper:0.1.0 .
docker build -f tests/parity/Dockerfile -t streamkeeper-parity:0.1.0 .
```

Mount source media read-only and use only `--dry-run` during differential
planning tests. Python plans must be generated against the same captured probe
fixture and tool versions before a result is considered comparable.

Sanitized probe snapshots and the matching normalized shell output live in
`tests/fixtures`. They retain policy-relevant stream metadata while replacing
file paths and free-form titles. Full media files are never copied into the
repository.

Run the checked-in DTS case with:

```zsh
.venv/bin/python scripts/check-planning-parity.py \
  --probe tests/fixtures/real_dts_5_1.json \
  --legacy-output tests/fixtures/real_dts_5_1.legacy.txt
```

The command exits successfully only when every normalized planning field
matches. Its JSON output names each differing field and shows both values.
