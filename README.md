# Evidence-Constrained Recovery for Ambiguous Irreversible Tool Effects in LLM Agent Systems

This artifact records the experiments evaluating ECR, a recovery mechanism for agent tool effects. The experiment directories contain the selected plans, inputs and scored records. The implementations, simulation and baselines sit under `ecr/`, `simulator/` and `baselines/`. `dsh/` contains the study's own harness adapters. See [record names](docs/NAMES.md) and [freeze notes](freeze/README.md).

| Experiment | Evidence | Manuscript question |
|---|---|---|
| E1 | Classifier ablation | Checks needed to recognise inconsistent evidence (RQ1) |
| E2 | Simulation with process failures | Recovery safety and completion (RQ1) |
| E3 | Guarded recovery under local faults | Authorization at send and fence boundaries (RQ2) |
| E4 | Crashes in authorization | Durability across authorization windows (RQ2) |
| E5 | ECR-G internal ablation | Sensitivity to classifier and validator checks (RQ3) |
| E6 | Scripted and real-model harness trials | Effect of durability and retry guards (RQ4) |
| E7 | Baselines and held-out scenarios | Endpoint arbitration and generalization (RQ5) |
| E8 | Arbitration crash windows | Crash-window behavior under the specified fault model (RQ2) |

Run `python -B analysis/offline_example.py` to exercise the ECR-G authorization path and score a synthetic case using a local SQLite ledger. Full simulation batches require Ubuntu 24.04, Python, Node.js v24.21.0, isolated guest identities and the storage layout stated in the corresponding frozen plans. E7 and E8 used cold-started virtual machine environments. Raw real-model sessions are excluded. E6 analysis uses the selected scoring fields. Full reproduction may also require mapping the frozen guest paths to a local deployment and preparing the original runtime.

Python, Node.js and plotting dependencies are supplied by the user's environment. Node.js is under MIT with bundled third-party notices. Matplotlib uses its own PSF-based license. SQLite is in the public domain. DeepSeek Harness 0.2.0-rc.2 is external software, available from the upstream vendor; consult the license distributed with that version. This repository supplies only study-authored DSH plugins and scripts. Code and data terms are in `LICENSE` and `LICENSE-DATA`.
