# Voided laptop runs

Supplementary Table S17 (Round 29) identifies three runs stopped by a CPython `type_traverse` assertion. The corresponding qualifications were also voided.

| Batch | Stop | Archive SHA-256 |
|---|---|---|
| bl1 (`ecr-blx-20261113-r1`; qualification `ecr-blq-20261113-r1`) | 57/128 | `9d5cca230e77bbe58d6147e276c343de669a8db5488c232bb6afca146d8ad4aa` |
| bl3 | Unit 402 | No unique archive identified in the local records |
| bl4 (`ecr-bl4x-codex-b1`; qualification `ecr-bl4q-codex-b1`) | 410/1512 | `db22722e08bf40b7dc6d23062d577c8cf5a90a7634bae9a3c3e0559c66836015` |

The VMware 26.0.1 preflight warning and VMware 25.0.0 kernel Oops are documented separately in the bl1 reports. The later bl6 c1 storage incident is documented separately from the laptop stops. Its archive hashes are in [the archive register](../../../freeze/ARCHIVE_SHA256.md).

The bl3 evidence was retained on the experiment guest disk. The available records list no separate export of this batch.
