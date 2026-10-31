# Unified QIH Runtime

## Purpose

A deterministic, dependency-free computational model of a QIH-inspired operator
sequence. It is integrated into the Free Brain cycle and records reproducible
outputs. It is **not** a quantum computer, a physical collapse model, proof of
holographic duality, or evidence of consciousness.

## Operator sequence

bulk → screen → observation → phase coupling → coherence gate → spectral
diagnostic → feedback

- **Bulk:** seeded complex-valued samples.
- **Screen:** Born-rule probabilities are computed from sample phase angles and
  used to draw deterministic outcomes.
- **Observation:** outcomes select complex register states.
- **Phase coupling:** a declared global-mean-field alignment operator is applied.
- **Coherence:** normalized phase alignment is calculated in [0,1].
- **Collapse gate:** the application checks coherence against a configurable
  threshold. The legacy field name "collapse" means only that this software
  gate passed.
- **Spectral diagnostic:** a dependency-free DFT magnitude is calculated only
  when the gate passes. It is not subjective experience.
- **Feedback:** continue or stabilize plus the next deterministic seed.

## Run the deterministic study

From the repository root:

~~~sh
python3 -m unittest qih_system.test_unified qih_system.test_study
python3 -m qih_system.study --cycles 1000 --seed 42 --output /tmp/qih-study.jsonl
~~~

The study writes a JSONL record for each cycle and a sibling
.summary.json file. It refuses to overwrite existing evidence files. The
study is deterministic for identical parameters.

## Metric semantics

- Born-rule output reports both the mean theoretical probability and the
  empirical sample frequency; finite-sample differences are expected.
- The distance field is a negative-log transform of a classical phase-coherence
  proxy. It is **not** a quantum entanglement measure.
- The phase-clock field is a dimensionless transform of supplied software
  throughput rates, not physical time dilation.
- The collapse field is a threshold decision, not physical wavefunction
  collapse.
- The experience norm is a spectral diagnostic, not a measurement of experience.

## Integration

drift_loop.py invokes run_cycle per Free Brain cycle and persists the
result and its canonical hash. The QIH feedback is copied into the next cycle's
planner context as advisory telemetry. It cannot override the immutable
objective or the dispatch allowlist.

## Validation boundary

A passing deterministic study establishes reproducibility and software
behavior only. Physical quantum claims would require a separately specified
state space, operators, observables, experimental predictions, and comparison
against independent measurements.
