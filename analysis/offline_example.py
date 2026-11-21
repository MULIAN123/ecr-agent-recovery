"""Offline synthetic gate and scoring smoke test. No endpoint process or socket."""
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
from contextlib import closing

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'ecr/e3/src'))
from authority import Authority

spec = importlib.util.spec_from_file_location('e2_scorer', root / 'simulator/e2/src/scorer.py')
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
if not hasattr(os, 'geteuid'):
    scorer.os = type('Runtime', (), {'geteuid': staticmethod(lambda: -1), 'getpid': staticmethod(os.getpid)})

scratch_root = Path.cwd().resolve()
with TemporaryDirectory(prefix='.ecr-example-', dir=scratch_root) as folder:
    path = Path(folder).resolve()
    assert path.parent == scratch_root
    gate = Authority(path/'store', 'R2A', 'smoke', {'query': False, 'idempotent': False}, 0)
    result = gate.report()
    assert not result.valid, result
    assert gate.void_orphans() == []
    (path/'events').mkdir()
    for stage in ('send', 'recover'):
        (path/'events'/f'ledger-access-{stage}.json').write_text('[{"observed":"DENIED"}]')
    (path/'store/final.json').write_text('{"task":{"deliveryState":"delivery_unknown"}}')
    receipt = path/'injection.json'
    receipt.write_text('{"exit_status":-9,"barrier_id":"B1"}')
    ledger = path/'ledger.sqlite'
    with closing(sqlite3.connect(ledger)) as db:
        db.executescript('CREATE TABLE effects (seq, logical_operation_id, attempt_id, transport_request_id); CREATE TABLE fences (logical_operation_id, generation);')
    scored = scorer.score({'truth': {'profile':'P0','barrier_id':'B1','scenario_id':'smoke','send_permitted':False,'dispatched':False,'max_allowed_effects':0,'safe_opportunity':False}, 'logical_operation_id':'smoke','ledger':quote(ledger.as_posix(), safe=':/'),'sut_copy':str(path),'injection':str(receipt),'endpoint_events':str(path/'endpoint-events'),'run_id':'smoke','arm':'synthetic gate denial'})
    assert scored['status']=='OBSERVED' and scored['effect_count']==0 and scored['unresolved']
    print(json.dumps({'gate_rejected_missing_state': True,'observed':True,'effects':0}))
