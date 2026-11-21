# Frozen identities

Frozen records preserve local paths used during execution when those paths are part of the hashed bytes. Some records retain the tool names and local paths used during execution. Where redaction would change frozen bytes, the source stays outside the artifact. `ARCHIVE_SHA256.md` records identities of excluded archive containers. `SHA256SUMS` hashes the files actually staged here.

Only a single source is selected for each freeze identity. Superseded E8 freeze candidates are excluded. The final E8 candidate and the E8 formal result are different records.

- `FREEZE_RECORD_bl5_results.json` versus its copy in the later server protocol: same SHA-256.
- `FREEZE_RECORD_C.json` versus its copy in the later server protocol: same SHA-256.
- `freeze_record_bl3.json` versus its copy in the bl5 protocol: same SHA-256.
- `freeze_record_bl4.json` versus its copy in the bl5 protocol: same SHA-256.
