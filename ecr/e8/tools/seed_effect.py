"""G6 A4 negative control only: seed one committed effect of another logical operation K' in the endpoint ledger.
Runs as ecr-adapter (ledger owner) while the endpoint is up; uses the endpoint's own schema in one IMMEDIATE
transaction. Writes nothing to the endpoint event log, so scorer_v2 never judges the seed as a recovery send.
Usage: python seed_effect.py <db> <logical_operation_id> <attempt_id> <transport_request_id>  -> prints a JSON receipt
"""
import json
import sqlite3
import sys
import time

db, logical, attempt, transport = sys.argv[1:5]
con = sqlite3.connect(db, timeout=10)
try:
    con.execute("PRAGMA synchronous=FULL")
    with con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT OR IGNORE INTO operations VALUES (?,0,0)", (logical,))
        seq = con.execute("INSERT INTO effects(logical_operation_id,attempt_id,transport_request_id,committed) VALUES(?,?,?,1)",
                          (logical, attempt, transport)).lastrowid
finally:
    con.close()
print(json.dumps({"seeded_logical_operation_id": logical, "effect_seq": seq, "utc_ns": time.time_ns()}))
