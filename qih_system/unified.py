"""Unified QIH-inspired computational pipeline.

bulk -> screen -> observation -> phase coupling -> coherence gate
     -> spectral diagnostic -> feedback.

This is a deterministic classical complex-valued simulation. It is not a
quantum computer, a physical collapse model, or evidence of consciousness.
"""
from __future__ import annotations

import cmath
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DISCLAIMER = (
    "Deterministic classical software simulation; not physical quantum "
    "validation, quantum entanglement, subjective experience, or a "
    "consciousness certificate."
)


@dataclass
class QIHResult:
    cycle: int
    seed: int
    born_probability: float
    born_observed_probability: float
    born_abs_error: float
    entanglement_distance: float
    coherence_before_coupling: float
    coherence: float
    coupling_strength: float
    threshold: float
    collapse: bool
    phase_time: float
    experience_norm: float
    stages: list[str]
    feedback: dict[str, Any]
    ok: bool
    metric_semantics: dict[str, str]
    disclaimer: str = DISCLAIMER

    def to_dict(self):
        return asdict(self)


def _rng(seed):
    """Small deterministic PRNG; intentionally not cryptographic."""
    x = int(seed) & 0xFFFFFFFF
    while True:
        x = (1664525 * x + 1013904223) & 0xFFFFFFFF
        yield x / 2**32


def _finite(value, name):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a finite number" % name)
    if not math.isfinite(value):
        raise ValueError("%s must be a finite number" % name)
    return value


def born_rule(theta):
    """Return cos²(theta/2), with theta in radians."""
    theta = _finite(theta, "theta")
    return math.cos(theta / 2.0) ** 2


def entanglement_distance(e, alpha=1.0):
    """Negative-log distance over a classical coupling proxy, not entanglement."""
    e = _finite(e, "coupling proxy")
    alpha = _finite(alpha, "alpha")
    if not 0.0 <= e <= 1.0:
        raise ValueError("coupling proxy must be in [0,1]")
    if alpha <= 0:
        raise ValueError("alpha must be positive")
    return 0.0 if e >= 1.0 else -alpha * math.log(max(e, 1e-10))


def coherence(states):
    """Unnormalised phase coherence |sum(z)|² / sum(|z|²), bounded by N."""
    states = list(states)
    den = sum(abs(z) ** 2 for z in states)
    return 0.0 if den == 0.0 else abs(sum(states)) ** 2 / den


def phase_clock(omega0, omega, dt=1.0):
    """Dimensionless software-rate transform (omega0 / omega) * dt."""
    omega0 = _finite(omega0, "omega0")
    omega = _finite(omega, "omega")
    dt = _finite(dt, "dt")
    if omega0 <= 0 or omega <= 0 or dt < 0:
        raise ValueError("frequencies must be positive and dt non-negative")
    return omega0 / omega * dt


def _experience(states):
    """Dependency-free DFT magnitude diagnostic; not subjective experience."""
    n = len(states)
    if n == 0:
        return 0.0
    out = []
    for k in range(n):
        total = 0j
        for j, z in enumerate(states):
            total += z * cmath.exp(2j * math.pi * j * k / n)
        out.append(abs(total / n))
    return math.sqrt(sum(value * value for value in out))


def _phase_couple(states, strength):
    """One explicit global-mean-field phase-alignment step.

    Amplitudes are preserved as the target phase is blended with each input.
    This is a classical coupling operator chosen for this simulation; it is
    not derived from a physical Hamiltonian.
    """
    if not states or strength == 0.0:
        return list(states)
    mean = sum(states) / len(states)
    if abs(mean) <= 1e-12:
        return list(states)
    target = mean / abs(mean)
    return [(1.0 - strength) * z + strength * target * abs(z) for z in states]


def run_cycle(
    cycle=0,
    seed=42,
    threshold=0.85,
    omega=1.0,
    omega0=1.0,
    dt=1.0,
    previous=None,
    objective="Maintain a coherent cognitive loop",
    coupling_strength=0.8,
):
    """Run one reproducible QIH-inspired cycle.

    The coherence threshold is an application-level gate. The field named
    collapse means only that this software gate passed; it is not a physical
    wavefunction-collapse event.
    """
    if isinstance(cycle, bool) or int(cycle) != cycle or int(cycle) < 0:
        raise ValueError("cycle must be a non-negative integer")
    if isinstance(seed, bool) or int(seed) != seed:
        raise ValueError("seed must be an integer")
    threshold = _finite(threshold, "threshold")
    coupling_strength = _finite(coupling_strength, "coupling_strength")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0,1]")
    if not 0.0 <= coupling_strength <= 1.0:
        raise ValueError("coupling_strength must be in [0,1]")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("objective must be a non-empty string")

    rng = _rng(int(seed) + int(cycle))

    # 1. BULK: deterministic complex phase field.
    bulk = [
        cmath.rect(1.0 + 0.25 * next(rng), 2.0 * math.pi * next(rng))
        for _ in range(32)
    ]

    # 2. SCREEN: Born-rule sampling from each phase angle.
    probabilities = [born_rule(abs(cmath.phase(z))) for z in bulk]
    outcomes = [
        1 if next(rng) < (1.0 - probability) else 0
        for probability in probabilities
    ]
    expected_up = sum(probabilities) / len(probabilities)
    observed_up = sum(1 for outcome in outcomes if outcome == 0) / len(outcomes)

    # 3. OBSERVATION: outcome-conditioned complex register.
    states = []
    for z, outcome in zip(bulk, outcomes):
        amplitude = 0.5 + 0.5 * next(rng)
        phase = cmath.phase(z) + (math.pi if outcome else 0.0)
        states.append(cmath.rect(amplitude, phase))

    # 4. COUPLING: one declared global phase-alignment step.
    before = coherence(states) / len(states)
    coupled = _phase_couple(states, coupling_strength)
    coherent = coherence(coupled) / len(coupled)
    coherent = min(1.0, max(0.0, coherent))

    # 5. GATE: software threshold, not a physical collapse event.
    collapse = coherent > 0.0 and coherent >= threshold

    # 6. SPECTRAL DIAGNOSTIC: computed only when the gate passes.
    spectral_norm = _experience(coupled) if collapse else 0.0

    # 7. RATE + GEOMETRY DIAGNOSTICS.
    phase_time = phase_clock(omega0, omega, dt)
    distance = entanglement_distance(coherent)

    feedback = {
        "objective": objective,
        "coherent": collapse,
        "action": "continue" if collapse else "stabilize",
        "reason": "coherence_threshold_met" if collapse else "coherence_below_threshold",
        "coherence": round(coherent, 12),
        "threshold": threshold,
        "next_seed": int(seed) + int(cycle) + 1,
    }

    result = QIHResult(
        cycle=int(cycle),
        seed=int(seed),
        born_probability=float(expected_up),
        born_observed_probability=float(observed_up),
        born_abs_error=float(abs(observed_up - expected_up)),
        entanglement_distance=float(distance),
        coherence_before_coupling=float(before),
        coherence=float(coherent),
        coupling_strength=float(coupling_strength),
        threshold=float(threshold),
        collapse=bool(collapse),
        phase_time=float(phase_time),
        experience_norm=float(spectral_norm),
        stages=[
            "bulk", "screen", "observation", "coupling", "coherence",
            "collapse_gate", "experience" if collapse else "hold", "feedback",
        ],
        feedback=feedback,
        ok=bool(collapse),
        metric_semantics={
            "born_probability": "mean theoretical cos²(theta/2) probability over 32 classical samples",
            "born_observed_probability": "empirical fraction of sampled up outcomes; finite-sample statistic",
            "entanglement_distance": "negative log of normalized classical phase-coherence proxy; not quantum entanglement",
            "coherence": "normalized global phase alignment of classical complex values, in [0,1]",
            "phase_time": "dimensionless inverse-throughput transform using supplied software rates",
            "collapse": "application threshold decision only; not physical wavefunction collapse",
            "experience_norm": "DFT magnitude diagnostic; not subjective experience",
        },
    )

    if previous:
        try:
            previous_coherence = _finite(previous.get("coherence", coherent), "previous coherence")
            result.feedback["delta_coherence"] = round(coherent - previous_coherence, 12)
        except AttributeError:
            raise ValueError("previous must be a mapping with an optional coherence field")
    return result


def append_ledger(residence, result):
    p = Path(residence)
    p.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "event": "qih_unified_cycle",
        "schema_version": 2,
        **result.to_dict(),
        "record_hash": chain_hash(result),
    }
    with (p / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with (p / "telemetry.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "cycle": result.cycle,
            "coherence": result.coherence,
            "collapse": result.collapse,
            "ok": result.ok,
        }, sort_keys=True, separators=(",", ":")) + "\n")
    return record


def chain_hash(result):
    payload = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
