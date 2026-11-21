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

## 致谢

犹记得高中时，两天一小病，三天一大病的我，活着都已经浪费了我大部分气力了，  
所赖君子见机，达人知命，上了大学后，清晰地认识到了很多东西，付出了很多艰辛和汗水，途中有老师们、学长们的帮助，还有远在他乡的朋友家人（李沛轩、刘东昊、刘云鹏、毛佳雪、白景豪、王浩、陈浩博、陈乐乐、任梦博、刘源、张志远、党龙飞哥等等）的牵挂和帮助。  
我还记得当初在B站听郑益慧老师的模电课，为我种下了一颗种子，如果几年大学读下来，最后真的能够建立一套属于自己的、面对陌生问题依然知道该怎样开始的思维方式，我觉得这可能才是大学真正珍贵的收获。我以后会保持我们工科生勤勤恳恳、踏踏实实的工作态度和优良作风。

特别感谢一下陈政老师的帮助。

故余虽愚，卒获有所闻，感谢所有人一路的陪伴和帮助。
