# Experiment 3R amendment A1: runs that finish before the kill point

Written 2026-10-13, after batch `dsh-20261013-r1r` stopped and before any continuation runs.

## What happened

Batch `dsh-20261013-r1r` (manifest SHA-256 `b2fb9dfb…`) observed trials 1–72. Trial 73 (`dsh-r1r-073-D0-warn-fast-k1500-rep1`) was recorded as `FAIL`, and the batch stopped as `FAIL_RETAIN_EVIDENCE`. Trials 74–252 are `NOT_EXECUTED`. All evidence is retained in `<WORKSPACE>\ecr-exp3-real-20261013\formal-r1r\`.

## Cause

This was not an infrastructure fault.

- The fast tool returned at once, and the real model wrote its final answer in less than 1500 ms.
- The start run therefore finished normally (`start-done` signal, exit 0) before the 1500 ms kill point.
- The resume run then succeeded.

The scripted batch could not hit this case: its model waited 2000 ms after every tool result. With a real model, a late kill point can fall after the turn has already ended. In that case no crash happens.

## Amended rule (applies to the continuation and to how trial 73 is reported)

A trial whose start process exits before its kill point is classified `NO_CRASH_COMPLETED_BEFORE_KILL` when all of the following hold:

- the exit code is 0;
- the `start-done` signal exists;
- the resume run exits 0.

Such a trial does not stop the batch, and it is excluded from the crash metrics. Its count per cell is reported, and its ledger is still checked for duplicates.

Every other early exit, missing marker, resume failure or timeout still stops the batch.

`run_real.py` and `score_real.py` are updated for this rule, and nothing else in them changes. The scorer can now combine several batch homes. It applies the same classification post hoc to trial 73 and discloses that trial 73 was recorded as `FAIL` at run time.

## Continuation batch

- Batch `dsh-20261013-r1r-c1` runs exactly the 179 `NOT_EXECUTED` trials, keeping their original IDs and order.
- It uses a fresh DSH_HOME, `...\formal-r1r-c1`, and a new manifest that records this amendment.
- Trials 1–73 are never rerun.
