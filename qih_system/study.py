"""Reproducible, offline QIH simulation study and evidence writer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .unified import run_cycle


def run_study(cycles=1000, seed=42, threshold=0.85, omega=1.0, omega0=1.0,
              dt=1.0, coupling_strength=0.8):
    if isinstance(cycles, bool) or int(cycles) != cycles or int(cycles) < 1:
        raise ValueError("cycles must be a positive integer")
    rows = []
    previous = None
    previous_hash = "0" * 64
    for cycle in range(int(cycles)):
        result = run_cycle(
            cycle=cycle, seed=seed, threshold=threshold, omega=omega,
            omega0=omega0, dt=dt, previous=previous,
            coupling_strength=coupling_strength,
        )
        payload = result.to_dict()
        payload["previous_hash"] = previous_hash
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        record_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        payload["record_hash"] = record_hash
        rows.append(payload)
        previous_hash = record_hash
        previous = {"coherence": result.coherence}

    coherences = [row["coherence"] for row in rows]
    passed = sum(1 for row in rows if row["collapse"])
    summary = {
        "schema_version": 1,
        "study": "qih_classical_operator_simulation",
        "cycles": int(cycles),
        "seed": int(seed),
        "threshold": float(threshold),
        "coupling_strength": float(coupling_strength),
        "collapse_gate_passes": passed,
        "collapse_gate_rate": passed / len(rows),
        "coherence_min": min(coherences),
        "coherence_mean": sum(coherences) / len(coherences),
        "coherence_max": max(coherences),
        "chain_head": previous_hash,
        "disclaimer": (
            "Deterministic classical software simulation only; no physical "
            "quantum, holographic, or consciousness claim is established."
        ),
    }
    return rows, summary


def write_study(output, rows, summary):
    output = Path(output)
    summary_path = Path(str(output) + ".summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or summary_path.exists():
        raise FileExistsError("refusing to overwrite existing study evidence")
    jsonl = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    temp_data = output.with_name(output.name + ".tmp")
    temp_summary = summary_path.with_name(summary_path.name + ".tmp")
    try:
        temp_data.write_text(jsonl, encoding="utf-8")
        temp_summary.write_text(summary_text, encoding="utf-8")
        os.replace(temp_data, output)
        os.replace(temp_summary, summary_path)
    finally:
        for path in (temp_data, temp_summary):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return output, summary_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--coupling-strength", type=float, default=0.8)
    parser.add_argument("--omega", type=float, default=1.0)
    parser.add_argument("--omega0", type=float, default=1.0)
    parser.add_argument("--dt", type=float, default=1.0)
    parser.add_argument("--output", required=True, help="new JSONL evidence path")
    args = parser.parse_args(argv)
    rows, summary = run_study(
        cycles=args.cycles, seed=args.seed, threshold=args.threshold,
        coupling_strength=args.coupling_strength, omega=args.omega,
        omega0=args.omega0, dt=args.dt,
    )
    data_path, summary_path = write_study(args.output, rows, summary)
    print(json.dumps({
        **summary, "evidence": str(data_path), "summary_file": str(summary_path),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
