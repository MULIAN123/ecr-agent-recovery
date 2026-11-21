# Verification summary

| Check | Status | Expected | Recomputed |
|---|---|---|---|
| E2 Reference | PASS | (13, 52) | (13, 52) |
| E2 ECR-E | PASS | (0, 29, 38) | (0, 29, 38) |
| E3 F7 invalid sends | PASS | (12, 0, 0) | (12, 0, 0) |
| E5 safety | PASS | (0, 256) | (0, 256) |
| E6 post-effect duplicates | PASS | ((36, 66), (0, 66), (0, 66)) | ((36, 66), (0, 66), (0, 66)) |
| E7 b1 ECR-GA R2A | PASS | (72, 72, 0, 104) | (72, 72, 0, 104) |
| E7 b1 ECR-GA R2B | PASS | (72, 72, 0, 104) | (72, 72, 0, 104) |
| E7 b1 Reference | PASS | (32, 104) | (32, 104) |
| E7 c2 ECR-GA R2A | PASS | (72, 72, 0, 104) | (72, 72, 0, 104) |
| E7 c2 ECR-GA R2B | PASS | (72, 72, 0, 104) | (72, 72, 0, 104) |
| E7 c2 Reference | PASS | (32, 104) | (32, 104) |
| E8 b1 | PASS | (182, 4, 254, 269) | (182, 4, 254, 269) |
| E8 c1 | PASS | (182, 4, 254, 269) | (182, 4, 254, 269) |

Offline synthetic example: PASS.

Per-unit checks apply to the staged files. Full guest and real-model experiments were not rerun.
