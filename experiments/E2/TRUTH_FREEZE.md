# Experiment 2 truth freeze record

Written 2026-10-12, before any execution of agent, planner, classifier or endpoint code for this experiment (dev smoke tests included).

- Generator: `<WORKSPACE>`
- `pkg_r1/private/cells.json` SHA-256 `b7f66c5523a7251250bbf59b65a9867466c33c0d08f5b05d07210c8f4a292869` (52 scenarios, 52 truth rows, 8 arms, 13 qualification cells with harness expectations, 416 formal cells)
- Package manifest SHA-256 `b9cc392ebaf3391fa448da6a27eb4c751d1aef357af528fc17a1ee16ef5918f2`; bundle `bundles/ecr-async-sim-r1.tar.gz` SHA-256 `45b3651bc9c491b834274158d823375564e60fb4cf36119df6871beea6059920`

Rule: if harness development finds code defects, sources may be fixed and the package rebuilt, but `private/cells.json` must stay byte-identical (same hash). Any truth change would be recorded here with its reason before execution.

Planned harness development smoke test: the 13 qualification cells only (CONV, RETRY and two EBR controls), run as one unprivileged user without identity separation. It checks plumbing only; its outcomes are not results and are not used to change scenarios or truth.
