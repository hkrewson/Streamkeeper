# Migration parity status

Python conversion execution remains locked. This file records evidence gathered
while moving behavior out of the frozen shell converter.

## Proven cases

| Case | Evidence | Result |
| --- | --- | --- |
| Real H.264 SDR, DTS 5.1, six SRT subtitles, embedded JPEG cover | Read-only shell dry run and sanitized ffprobe snapshot | Structured planning fields match: video action, HDR mode, selected source, E-AC3 5.1/640 target, default track, labels, subtitles, and output paths |
| Real HEVC HDR10, AAC 7.1, two subtitles | Read-only shell and Python plans using the same container tools | Exposed the approved AAC 7.1 policy correction recorded below |
| Real H.264 SDR, AC3 5.1, matching movie NFO | Read-only shell dry run and sanitized ffprobe snapshot | Structured planning fields match, including the existing compatible default and a sidecar filename containing a parenthesized year |
| Real HEVC Dolby Vision 7, TrueHD Atmos 7.1, eight additional audio streams, 13 PGS subtitles | Read-only shell dry run, sanitized ffprobe snapshot, a three-second video excerpt, and a 20-second subtitle-bearing remux excerpt | Planning fields match; the real command path emits recognized profile 8 signaling, preserves decoded frames and HDR Level 6 metadata, retains all original audio/subtitle tracks byte-identically, preserves a real PGS payload hash, and adds E-AC3 5.1 |
| Real HEVC HDR10+, E-AC3 5.1, two SRT subtitles, four embedded JPEG covers | Read-only shell dry run, sanitized ffprobe snapshot, and a three-second off-library bitstream excerpt | Python identifies frame-level HDR10+ that the shell reports only as HDR10; `dovi_tool --drop-hdr10plus` removes dynamic metadata while preserving HDR10 mastering-display and content-light metadata |
| Real HEVC SDR Plex featurette, AC3 2.0, VobSub | Sanitized ffprobe snapshot and an isolated full-duration copy | Video, audio, and all 34 VobSub packets survive byte-identically; duration and stream counts match, and the output receives the Plex `-featurette` suffix |
| Synthetic H.264 SDR, FLAC 2.0 | Frozen-shell conversion and Python planned-command execution of isolated one-second fixtures | Commands match after path normalization; both outputs validate with the original FLAC retained and a labeled/default AAC 2.0 fallback added |
| Synthetic frozen-shell dry-run matrix | Nine isolated generated cases: MPEG-2/PCM stereo, AAC 7.1, main-plus-commentary FLAC, mono FLAC, 4.0 PCM, missing audio, ASS, MOV_TEXT, and a Plex featurette | All structured decisions match except the two approved corrections; source ranking excludes commentary, missing language falls back to English, channel counts are not upmixed, subtitle actions match, and direct-file extra naming is preserved |
| Synthetic H.264 SDR, PCM 5.1 | Python planned-command execution | PCM remains byte-identical; E-AC3 5.1/640 is added and made default without upmixing |
| Synthetic ASS subtitle and embedded JPEG cover | Python planned-command execution | ASS remains byte-identical, an SRT fallback is added, and the cover survives extraction and re-attachment |
| Synthetic MPEG-2 video with AAC 2.0 | Python planned-command execution | Video is converted to HEVC while the original AAC elementary stream remains byte-identical |
| Synthetic MOV_TEXT in MP4 | Python planned-command execution | Subtitle is converted to SRT in the MKV output while video and AAC payloads remain unchanged |
| Synthetic chapters and MPEG-TS data | Python planned-command execution | Chapters survive remux; non-playback data is omitted and recorded while retained payloads validate |
| Synthetic TrueHD 7.1 and DTS 5.1 | Python planned-command execution | Original audio remains byte-identical and E-AC3 5.1/640 is added as default |
| Synthetic TrueHD 7.1 plus existing E-AC3 5.1 | Python planned-command execution | Both source tracks remain byte-identical, no duplicate fallback is generated, and E-AC3 becomes the sole default |
| Synthetic AAC 7.1 | Python planned-command execution | AAC 7.1 remains byte-identical while E-AC3 5.1/640 is added and made default, proving the approved policy exception |
| Synthetic mono PCM and 4.0 FLAC | Python planned-command execution | AAC 1.0/96 and E-AC3 4.0 are generated respectively, without upmixing |
| Synthetic HDR10 and HLG | Python planned-command execution | Incompatible-video transcode path produces 10-bit HEVC and preserves transfer, primaries, matrix, mastering-display, and light-level metadata |

The checked-in real-media fixture contains metadata only. Paths and free-form
titles are sanitized; no copyrighted audio or video is stored in the project.

## Approved parity exceptions

The frozen shell retains AAC 7.1 without adding a compatibility stream. The
approved Python policy preserves that source but also treats it as an eligible
last-resort 6+ channel source, generating E-AC3 5.1 at 640 kbps and making the
new stream default. Higher-quality 6+ channel sources still outrank AAC 7.1.
AAC 5.1 and AAC 2.0 remain accepted compatibility targets and are not converted
merely because they are AAC. This is an intentional correction rather than an
unexplained porting difference.

The frozen shell detects HDR mode from stream-level color signaling and can
therefore report an HDR10+ source as ordinary HDR10. Python supplements the
stream probe with decoded-frame side data. When SMPTE ST 2094-40 metadata is
present, it preserves the HEVC base layer and static HDR10 metadata while
removing only HDR10+ metadata. This intentional correction implements the
approved requirement to normalize non-native HDR to an Apple-compatible native
HDR base.

## Completed migration work

- The audio policy matrix is covered by dedicated tests for existing E-AC3,
  AC3, AAC 5.1/2.0, TrueHD, DTS, E-AC3 Atmos 7.1, AAC 7.1, FLAC, PCM,
  mono, stereo, 3–5 channels, 6+ channels, commentary exclusion, source
  ranking, missing audio, and fallback language.
- Direct CLI plans now infer the classification root above a recognized Plex
  extra folder, so selecting one featurette file retains the same suffix as a
  directory scan or web-library plan.
- Semantic output validation now checks original and generated audio codecs and
  channels, labels, the single intended default, subtitle preservation and SRT
  fallbacks, chapters, attachments and embedded covers, duration, frame count,
  HDR bit depth and color signaling, static HDR metadata, Dolby Vision profile,
  and HDR10+ removal.
- Probe snapshots supplement stream metadata with one decoded video frame so
  mastering-display and content-light metadata are not missed when ffprobe
  exposes them only as frame side data.
- Controlled remux validation hashes copied video, audio, and retained subtitle
  elementary streams with SHA-256; the exercised fixtures are byte-identical.
- Transactional installation now has tested rollback checkpoints after the
  original rename, output installation, evidence installation, and NFO
  installation. Each injected failure restores the original media and NFO and
  removes installed replacement artifacts.
- A parity-gated controlled executor now exercises the complete pipeline:
  tool and space checks, exclusive per-file locking, source probing, planned
  commands, semantic and elementary-stream validation, evidence generation,
  NFO preparation, transactional installation, rollback, and temporary-file
  cleanup. The public CLI still calls the locked executor.
- Full-pipeline failure injection covers post-probe, post-encode,
  post-validation, and post-evidence-preparation failures, insufficient space,
  an existing conversion lock, and pre-existing staged artifacts. In every
  case, original media/NFO data and artifacts owned by another run survive.
- Interruptions, permission-denial, and disconnected-mount failures are covered
  at the executor and transaction boundaries. Owned temporary files and locks
  are removed, rollback restores the original, and process interrupts still
  propagate to the caller after cleanup.
- Persisted scan work survives application restarts. Existing queued scans are
  rehydrated, while a scan interrupted in progress remains in history and gets
  exactly one newly queued replacement run.
- Ordinary scans are incremental: unchanged files reuse their latest successful
  probe while changed files and every deep scan are probed again. Compatibility
  policy is still reevaluated on reused metadata, so settings changes take
  effect without unnecessary media reads.
- Scan runs persist separate counts for new, modified, removed, and unchanged
  files as well as fresh and reused probes. The Scans inspector exposes these
  changes alongside file-level probe failures. A bounded persistent activity
  log records queueing, discovery, failures, completion, and restart recovery.
- Files absent from a later successful scan leave the current Library and report
  views, while their asset and resolved-finding history remains available.
- Ignored findings remain ignored while the condition is still detected, then
  resolve after a successful scan proves the condition or asset is absent. A
  previously resolved condition reopens if it later returns.
- Storage retention is enforced at startup and whenever its setting changes.
  It prunes expired completed scan records and redundant old probe snapshots,
  while preserving queued/running jobs, catalog entries, findings, and the
  newest probe for every asset.
- Daily and weekly scheduling has deterministic due-run coverage. Manual mode,
  disabled roots, recent runs, overdue runs, and active queued/running work are
  handled explicitly, and scan history identifies manual, scheduled, and
  restart-recovery triggers.
- Scan submission is atomic and idempotent for each library and scan depth.
  Repeated requests reuse the queued or running scan instead of creating
  duplicate work; a deep scan may still wait behind an ordinary scan.
- Structured tool diagnostics report executable paths, versions, failures, and
  separate readiness for scanning, base conversion, and conditional Dolby
  Vision processing. Version checks are bounded so a broken executable cannot
  indefinitely hold the Settings page.
- The installed standalone CLI has contract coverage for deterministic file and
  directory scans, deep analysis, text and JSON output, empty inputs, per-file
  probe and mount failures, dry-run planning, and the locked non-dry conversion
  gate.
- A standalone shadow-scan comparator normalizes absolute paths, timestamps,
  file metadata, capture times, and raw bitrate measurements, then reports
  missing, added, or classification-changed assets in text or JSON. This makes
  the pending full-library review reproducible without modifying media.
- Every new web scan preserves an immutable CLI-compatible snapshot of the
  asset, probe, and finding decisions used in that run. Completed scans can be
  exported from the Scans inspector for offline shadow comparison; the latest
  pre-migration scan can also be reconstructed after an upgrade.
- The latest complete pre-migration catalog has been shadow-reclassified under
  the current policy: all 2,742 asset classifications match exactly. Its 2,737
  successful probes also build complete conversion plans without exceptions;
  five prior probe failures remain unchanged. One plan is intentionally
  non-executable because its MP4 contains audio, data, and cover art but no
  playable video stream.
- Normalized command planning covers data-stream omission, MOV_TEXT conversion,
  ASS/SSA fallback generation, source dispositions, and cover extraction and
  re-attachment. Each branch now has controlled command execution and semantic
  output validation; copied payloads are hash-checked where applicable.
- Dolby Vision/HDR10+ remux planning reconstructs raw-HEVC packet timestamps at
  the exact source frame rate after `dovi_tool` processing. A real 24000/1001
  Dolby Vision excerpt completed the full remux with all decoded video frames
  byte-identical and in the same display order.
- The FFmpeg `dovi_rpu` bitstream filter restores the Matroska Dolby Vision
  configuration record after `dovi_tool` normalization. Without that step the
  RPU remained in the elementary stream but clients and validation could not
  identify the output as profile 8.
- Movie and episode NFO tests preserve their distinct XML roots and unrelated
  metadata, replace an existing evidence reference without duplication, and
  leave installation to the transactional boundary. NFO discovery matches the
  frozen safety rule: use a valid same-stem movie/episode sidecar, or use
  `movie.nfo` only when the directory contains exactly one media file; never
  write episode evidence into `tvshow.nfo`.

## Remaining gates

- Repeat fresh discovery and probing with the current deployed image, then
  compare its exported snapshot with the accepted 2,742-asset baseline.
- Complete selected playback checks outside Plex before unlocking standalone
  CLI conversion.
