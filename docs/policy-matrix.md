# Conversion policy matrix

The frozen shell implementation remains the migration reference. Python conversion execution stays locked until normalized plans reach 100% parity across this matrix and controlled output tests pass.

| Area | Source condition | Required result |
| --- | --- | --- |
| Discovery | MKV, AVI, TS, M2TS, MTS, MP4, M4V | Discover recursively and deterministically |
| Discovery | AppleDouble `._*` or `Original` backup | Skip |
| Naming | Plex extra folder | Preserve folder classification and add Plex extra suffix when absent |
| Video | SDR H.264 | Copy |
| Video | HEVC without normalization need | Copy |
| Video | Dolby Vision profile 7 | Convert metadata to profile 8.1, preserving the base layer |
| Video | HDR10+ | Remove HDR10+ metadata while retaining the HDR10 base |
| Video | Other incompatible codec | Transcode to HEVC; preserve viable HDR signaling |
| Audio | Existing E-AC3 5.1 | Keep all streams; make this the compatibility default |
| Audio | Existing AC3 5.1 without E-AC3 5.1 | Keep all streams; make this the compatibility default |
| Audio | Existing AAC 5.1/2.0 without better fallback | Keep; use the best existing compatible default |
| Audio | AAC 7.1 without E-AC3 5.1 | Preserve AAC 7.1; add E-AC3 5.1 at 640 kbps and make the generated stream default |
| Audio | Lossless, DTS family, or other eligible 6+ channel source | Add E-AC3 5.1 at 640 kbps; never remove source |
| Audio | Eligible 3–5 channel source | Add same-channel E-AC3, no upmix, maximum 640 kbps |
| Audio | Eligible stereo source | Add AAC 2.0 at 192 kbps |
| Audio | Eligible mono source | Add AAC 1.0 at 96 kbps |
| Audio | Commentary, description, narration | Preserve, label, and exclude from fallback source ranking |
| Language | Missing/undefined stream language | Use English fallback |
| Subtitles | All source subtitle streams | Preserve |
| Subtitles | ASS/SSA | Preserve and add SRT fallback |
| Subtitles | MOV_TEXT | Preserve and add SRT conversion |
| Subtitles | PGS/VobSub | Preserve and record playback/transcode warning; no OCR |
| Container metadata | Chapters, attachments, cover art, tags, dispositions | Preserve unless Matroska cannot represent the source item |
| NFO | Movie or episode XML | Preserve unrelated nodes and add evidence reference |
| Evidence | Every completed conversion | Record versions, probes, normalized commands, decisions, warnings, validation, timestamps |
| Replacement | Any failure before validation | Never rename the source |
| Replacement | Failure after original rename | Restore the original name and remove incomplete staged output |

Intentional changes require separate approval and a recorded parity exception; they are never accepted as incidental port differences.
