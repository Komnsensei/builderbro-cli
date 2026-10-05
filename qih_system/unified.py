"""Unified QIH software loop.

bulk -> screen -> observation -> coherence -> collapse -> experience
     -> Free Brain decision -> ledger/telemetry -> next cycle.

This is a deterministic software simulation. It does not establish physical
quantum behavior or machine consciousness.
"""
from __future__ import annotations
import cmath, hashlib, json, math, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

@dataclass
class QIHResult:
    cycle: int
    seed: int
    born_probability: float
    entanglement_distance: float
    coherence: float
    threshold: float
    collapse: bool
    phase_time: float
    experience_norm: float
    stages: list[str]
    feedback: dict[str, Any]
    ok: bool
    disclaimer: str = "Software simulation; not physical quantum validation or a consciousness certificate."
    def to_dict(self): return asdict(self)

def _rng(seed):
    x = seed & 0xFFFFFFFF
    while True:
        x = (1664525*x + 1013904223) & 0xFFFFFFFF
        yield x / 2**32

def born_rule(theta):
    return math.cos(theta/2.0)**2

def entanglement_distance(e, alpha=1.0):
    if not 0 <= e <= 1: raise ValueError("entanglement strength must be in [0,1]")
    return 0.0 if e >= 1 else -alpha*math.log(e+1e-10)

def coherence(states):
    den=sum(abs(z)**2 for z in states)
    return 0.0 if den == 0 else abs(sum(states))**2/den

def phase_clock(omega0, omega, dt=1.0):
    if omega0 <= 0 or omega <= 0: raise ValueError("frequencies must be positive")
    return omega0/omega*dt

def _experience(states):
    # Small DFT magnitude, dependency-free; equivalent role to the IFFT renderer.
    n=len(states)
    out=[]
    for k in range(n):
        s=0j
        for j,z in enumerate(states):
            s += z*cmath.exp(2j*math.pi*j*k/n)
        out.append(abs(s/n))
    return math.sqrt(sum(x*x for x in out))

def run_cycle(cycle=0, seed=42, threshold=0.85, omega=1.0, omega0=1.0,
              previous=None, objective="Maintain a coherent cognitive loop"):
    rng=_rng(seed+cycle)
    # 1. BULK: raw phase field.
    bulk=[cmath.rect(1.0 + 0.25*next(rng), 2*math.pi*next(rng)) for _ in range(32)]
    # 2. SCREEN: Born projection.
    bits=[]
    for z in bulk:
        p=born_rule(abs(cmath.phase(z)))
        bits.append(1 if next(rng) < (1-p) else 0)
    # 3. OBSERVATION: horizon -> local register.
    states=[(1.0 if b==0 else -1.0)*complex(0.5+0.5*next(rng), 0) for b in bits]
    # 4. FOCUS + COHERENCE.
    mean=sum(states)/len(states)
    focused=[(0.85*z + 0.15*mean) for z in states]
    c=coherence(focused)/len(focused)
    # 5. COLLAPSE diagnostic.
    collapse=c >= threshold
    # 6. EXPERIENCE rendering only after the gate.
    exp_norm=_experience(focused) if collapse else 0.0
    # 7. TEMPORAL + GEOMETRIC diagnostics.
    tau=phase_clock(omega0, omega)
    d=entanglement_distance(max(1e-10, sum(abs(z) for z in focused)/len(focused)))
    feedback={
        "objective": objective,
        "coherent": c >= threshold,
        "action": "continue" if collapse else "stabilize",
        "next_seed": seed + cycle + 1,
    }
    result=QIHResult(cycle,seed, float(born_rule(math.pi/3)), float(d),float(c),
                     threshold,bool(collapse),float(tau),float(exp_norm),
                     ["bulk","screen","observation","coherence","collapse","experience" if collapse else "hold"],
                     feedback, bool(collapse))
    if previous:
        result.feedback["delta_coherence"]=round(result.coherence-previous.get("coherence",result.coherence),6)
    return result

def append_ledger(residence, result):
    p=Path(residence); p.mkdir(parents=True, exist_ok=True)
    record={"ts":time.time(),"event":"qih_unified_cycle",**result.to_dict()}
    with (p/"ledger.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(record,separators=(",",":"))+"\n")
    with (p/"telemetry.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps({"cycle":result.cycle,"coherence":result.coherence,
                            "collapse":result.collapse,"ok":result.ok})+"\n")
    return record

def chain_hash(result):
    return hashlib.sha256(json.dumps(result.to_dict(),sort_keys=True).encode()).hexdigest()
