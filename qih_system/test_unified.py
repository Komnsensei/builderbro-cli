import tempfile, json
from pathlib import Path
from .unified import run_cycle, append_ledger, chain_hash

def test_cycle_closes():
    r=run_cycle(cycle=1,seed=7,threshold=0.01)
    assert r.stages[:5]==["bulk","screen","observation","coherence","collapse"]
    assert r.ok
    assert r.experience_norm >= 0
    assert chain_hash(r)

def test_ledger_round_trip():
    with tempfile.TemporaryDirectory() as d:
        r=run_cycle(2,9,threshold=0.01)
        append_ledger(d,r)
        rows=(Path(d)/"ledger.jsonl").read_text().splitlines()
        assert len(rows)==1
        assert json.loads(rows[0])["event"]=="qih_unified_cycle"
