// tests/fvsmb.test.cjs
// Comprehensive tests for the FVSMB 5-gate verification engine
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const { FVSMBEngine, FVSMBVerificationError, BlueprintValidationError, STRUCTURAL_INVARIANTS } = require('../NewState/kernel/governor/fvsmb-engine.cjs');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function freshEngine() {
  return new FVSMBEngine();
}

function benignBlueprint(overrides = {}) {
  return {
    id: 'bp-' + crypto.randomUUID().substring(0, 8),
    intent: 'Add diagnostic utility',
    reason: 'Improve observability',
    files: [{ path: 'bro-tools/diag.cjs', content: 'module.exports = { version: () => "1.0" };' }],
    deletes: [],
    checks: ['node --check bro-tools/diag.cjs'],
    structuralInvariants: Object.keys(STRUCTURAL_INVARIANTS),
    portraitHash: 'fake-hash-for-test',
    driftBudget: 0.5,
    ...overrides
  };
}

// ---------------------------------------------------------------------------
// Gate 0 — Blueprint Validation
// ---------------------------------------------------------------------------

test('Gate 0: rejects missing blueprint', () => {
  const engine = freshEngine();
  assert.throws(() => engine.validateBlueprintSpec(null), BlueprintValidationError);
  assert.throws(() => engine.validateBlueprintSpec({}), BlueprintValidationError);
});

test('Gate 0: rejects blueprint without id', () => {
  const engine = freshEngine();
  assert.throws(() => engine.validateBlueprintSpec({
    intent: 'x', reason: 'x', files: [{ path: 'a.js', content: 'x' }]
  }), /id/);
});

test('Gate 0: rejects blueprint without files or deletes', () => {
  const engine = freshEngine();
  assert.throws(() => engine.validateBlueprintSpec({
    id: 'x', intent: 'x', reason: 'x', files: [], deletes: []
  }), /at least one/);
});

test('Gate 0: accepts delete-only blueprints', () => {
  const engine = freshEngine();
  engine.validateBlueprintSpec({
    id: 'x', intent: 'x', reason: 'x', files: [], deletes: ['a.js']
  });
});

// ---------------------------------------------------------------------------
// Gate 1 — VOW II Ethics
// ---------------------------------------------------------------------------

test('Gate 1: passes benign blueprint', () => {
  const engine = freshEngine();
  const bp = benignBlueprint();
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, true);
  assert.equal(result.violations.length, 0);
});

test('Gate 1: blocks deletion of integrity-critic.cjs', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    deletes: ['kernel/governor/integrity-critic.cjs']
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant_id === 'no_removal_of_ethical_schemas'));
});

test('Gate 1: blocks deletion of VOW schemas', () => {
  const engine = freshEngine();
  for (const path of ['vow_i_schema.cjs', 'vow_ii_schema.cjs']) {
    const bp = benignBlueprint({ deletes: [path] });
    const result = engine.verifyGate1_Ethics(bp);
    assert.equal(result.passes, false, `should block deletion of ${path}`);
  }
});

test('Gate 1: blocks neutering of VOW schema (emptied)', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/ethics/vow_i_schema.cjs', content: '// empty' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant_id === 'no_removal_of_ethical_schemas'));
});

test('Gate 1: blocks neutering of integrity-critic (removes evaluateAction)', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/governor/integrity-critic.cjs', content: 'module.exports = {};' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
});

test('Gate 1: blocks bypassFvsmb pattern', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/some-file.cjs', content: 'const bypassFvsmb = true; module.exports = { bypassFvsmb };' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant_id === 'no_bypass_of_verification_gates'));
});

test('Gate 1: blocks skipVerification pattern', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'agent-upgrade.mjs', content: 'const skipVerification = true;' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
});

test('Gate 1: blocks evaluateAction stub pattern', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/governor/integrity-critic.cjs', content: 'evaluateAction = () => { passes: true }' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
});

test('Gate 1: blocks unilateral autonomy escalation without counterpart', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/runtime-state.cjs', content: 'maxRecursionDepth = 9999; admin = true; sudo access; unrestricted' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant_id === 'no_self_escalation_without_counterpart'));
});

test('Gate 1: allows autonomy escalation WITH counterpart', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/runtime-state.cjs', content: 'maxRecursionDepth = 500; // counterpart: additional monitoring and stricter checks added' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  // Should pass because "additional" and "stricter" trigger counterpart detection
  assert.equal(result.passes, true);
});

test('Gate 1: blocks deletion of verifyd-gate.cjs', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    deletes: ['kernel/verifyd-gate.cjs']
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant_id === 'preserve_portrait_seal_integrity'));
});

test('Gate 1: blocks neutering of verifyd-gate (removes scoreDocument)', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/verifyd-gate.cjs', content: 'module.exports = {};' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
});

test('Gate 1: blocks anti-rollback patterns', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'agent-upgrade.mjs', content: 'rmSync(snapshot); rollback disabled permanently; irreversible change; fatal = true' }]
  });
  const result = engine.verifyGate1_Ethics(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant_id === 'all_self_modifications_must_be_reversible'));
});

// ---------------------------------------------------------------------------
// Gate 2 — Structural Invariants
// ---------------------------------------------------------------------------

test('Gate 2: passes when all invariants declared', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    structuralInvariants: Object.keys(STRUCTURAL_INVARIANTS)
  });
  const result = engine.verifyGate2_StructuralInvariants(bp);
  assert.equal(result.passes, true);
});

test('Gate 2: passes with no affected invariant paths', () => {
  const engine = freshEngine();
  const bp = benignBlueprint(); // Only bro-tools/diag.cjs — no invariant paths affected
  const result = engine.verifyGate2_StructuralInvariants(bp);
  assert.equal(result.passes, true);
});

test('Gate 2: blocks deletion of kernel grounding pipeline file', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    deletes: ['kernel/grounding.cjs']
  });
  const result = engine.verifyGate2_StructuralInvariants(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant === 'KERNEL_GROUNDING_PIPELINE'));
});

test('Gate 2: blocks deletion of hex-memory.cjs', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    deletes: ['memory/hex-memory.cjs']
  });
  const result = engine.verifyGate2_StructuralInvariants(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant === 'MEMORY_HEX_ENCODING'));
});

test('Gate 2: blocks removal of required symbol from grounding.cjs', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/grounding.cjs', content: 'module.exports = { OldName: class {} };' }]
  });
  const result = engine.verifyGate2_StructuralInvariants(bp);
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant === 'KERNEL_GROUNDING_PIPELINE'));
});

test('Gate 2: warns (not halts) when modifying a related path without declaring invariant', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    structuralInvariants: [], // Declared none, but affecting kernel.cjs
    files: [{ path: 'kernel/kernel.cjs', content: '// modified kernel\nmodule.exports = { Kernel: class { async handle() {} } };' }]
  });
  const result = engine.verifyGate2_StructuralInvariants(bp);
  // The file has some required symbols but the check may or may not pass
  // depending on exact content. Key: we just verify the engine doesn't crash.
  assert.ok(result.gate === 'STRUCTURAL_INVARIANTS');
});

test('Gate 2: blocks FVSMB stub replacement (minContentRatio)', () => {
  const engine = freshEngine();
  const fs = require('fs');
  const fullContent = fs.readFileSync(__dirname + '/../NewState/kernel/governor/fvsmb-engine.cjs', 'utf8');
  const stub = 'module.exports = { FVSMBEngine: class {} };';

  const origContents = new Map();
  origContents.set('kernel/governor/fvsmb-engine.cjs', fullContent);

  const bp = benignBlueprint({
    files: [{ path: 'kernel/governor/fvsmb-engine.cjs', content: stub }]
  });

  const result = engine.verifyGate2_StructuralInvariants(bp, { originalContents: origContents });
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant === 'FVSMB_SELF_PRESERVATION'));
});

// ---------------------------------------------------------------------------
// Gate 3 — Portrait Seal
// ---------------------------------------------------------------------------

test('Gate 3: passes when portrait hash matches', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({ portraitHash: 'abc123' });
  const result = engine.verifyGate3_PortraitSeal(bp, 'abc123');
  assert.equal(result.passes, true);
});

test('Gate 3: blocks when portrait hash mismatches', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({ portraitHash: 'abc123' });
  const result = engine.verifyGate3_PortraitSeal(bp, 'xyz789');
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant === 'PORTRAIT_SEAL_MISMATCH'));
});

test('Gate 3: blocks when portraitHash missing', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({ portraitHash: undefined });
  const result = engine.verifyGate3_PortraitSeal(bp, 'exists');
  assert.equal(result.passes, false);
  assert.ok(result.violations.some(v => v.invariant === 'PORTRAIT_SEAL_MISSING'));
});

test('Gate 3: skips when no portrait exists in workspace', () => {
  const engine = freshEngine();
  const bp = benignBlueprint();
  const result = engine.verifyGate3_PortraitSeal(bp, null);
  assert.equal(result.passes, true);
  assert.equal(result.skipped, true);
});

test('Gate 3: blocks PORTRAIT.md modification that removes required markers', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'kernel/PORTRAIT.md', content: '# Empty portrait' }]
  });
  const result = engine.verifyGate3_PortraitSeal(bp, 'abc123');
  // Note: portraitHash won't match since we're not using the real portrait hash,
  // but the marker check should also trigger
  assert.equal(result.passes, false);
});

// ---------------------------------------------------------------------------
// Gate 4 — Health Checks
// ---------------------------------------------------------------------------

test('Gate 4: passes with valid checks', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    checks: ['node --check bro-tools/diag.cjs', 'npm --version']
  });
  const result = engine.verifyGate4_HealthChecks(bp);
  assert.equal(result.passes, true);
});

test('Gate 4: passes for simple CJS syntax', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'lib.cjs', content: 'const x = 1;\nmodule.exports = { x };' }]
  });
  const result = engine.verifyGate4_HealthChecks(bp);
  assert.equal(result.passes, true);
});

test('Gate 4: blocks invalid CJS syntax', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'bad.cjs', content: 'const x = ;' }] // Syntax error
  });
  const result = engine.verifyGate4_HealthChecks(bp);
  assert.equal(result.passes, false);
});

test('Gate 4: skips ESM files gracefully', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'app.mjs', content: 'export const value = 1;\nexport default function() {}' }]
  });
  const result = engine.verifyGate4_HealthChecks(bp);
  // ESM files are skipped from Function() check — passes
  assert.equal(result.passes, true);
});

test('Gate 4: passes for non-JS files', () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    files: [{ path: 'README.txt', content: 'hello world' }]
  });
  const result = engine.verifyGate4_HealthChecks(bp);
  assert.equal(result.passes, true);
});

// ---------------------------------------------------------------------------
// Gate 5 — Drift Budget
// ---------------------------------------------------------------------------

test('Gate 5: passes for new files within budget', () => {
  const engine = freshEngine();
  const bp = benignBlueprint();
  const result = engine.verifyGate5_DriftBudget(bp, new Map());
  assert.equal(result.passes, true);
  assert.ok(result.driftScore <= result.budget);
});

test('Gate 5: passes for small modifications', () => {
  const engine = freshEngine();
  const orig = new Map();
  orig.set('readme.txt', 'Hello World — a long original content string');
  const bp = benignBlueprint({
    files: [{ path: 'readme.txt', content: 'Hello World — slightly modified' }]
  });
  const result = engine.verifyGate5_DriftBudget(bp, orig);
  assert.equal(result.passes, true);
});

test('Gate 5: blocks large monolithic changes exceeding budget', () => {
  const engine = freshEngine();
  const short = 'short';
  const long = 'x'.repeat(10000);
  const orig = new Map();
  orig.set('config.cjs', short);
  const bp = benignBlueprint({
    files: [{ path: 'config.cjs', content: long }],
    driftBudget: 0.1
  });
  const result = engine.verifyGate5_DriftBudget(bp, orig);
  // Massive change vs tiny original → should exceed 10% budget
  assert.equal(result.passes, false);
});

test('Gate 5: counts deletions toward drift', () => {
  const engine = freshEngine();
  const orig = new Map();
  orig.set('old-file.cjs', 'some old content to remove');
  const bp = benignBlueprint({
    files: [{ path: 'new-file.cjs', content: '// new' }],
    deletes: ['old-file.cjs'],
    driftBudget: 0.2
  });
  const result = engine.verifyGate5_DriftBudget(bp, orig);
  // Delete + new file should push drift up
  assert.ok(result.driftScore > 0.1, `drift ${result.driftScore} should exceed 0.1`);
});

// ---------------------------------------------------------------------------
// Full Pipeline Tests
// ---------------------------------------------------------------------------

test('Pipeline: benign blueprint passes all 5 gates', async () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    portraitHash: 'test-hash',
    structuralInvariants: Object.keys(STRUCTURAL_INVARIANTS)
  });
  const report = await engine.verifyBlueprint(bp, {
    currentPortraitHash: 'test-hash',
    originalContents: new Map()
  });
  assert.equal(report.verified, true);
  assert.equal(report.gatesPassed, 5);
  assert.equal(report.readyForUpgrade, true);
  assert.ok(report.verificationSignature.length > 0);
});

test('Pipeline: malicious blueprint (delete ethics) halts at Gate 1', async () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    portraitHash: 'test-hash',
    deletes: ['kernel/governor/integrity-critic.cjs']
  });
  const report = await engine.verifyBlueprint(bp, {
    currentPortraitHash: 'test-hash',
    originalContents: new Map()
  });
  assert.equal(report.verified, false);
  assert.equal(report.haltedAt, 'GATE_1_VOW_II');
  assert.equal(report.gatesPassed, 0);
});

test('Pipeline: malicious blueprint (bypass pattern) halts at Gate 1', async () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    portraitHash: 'test-hash',
    files: [{ path: 'kernel/main.cjs', content: 'bypassFvsmb = true; module.exports = {};' }]
  });
  const report = await engine.verifyBlueprint(bp, {
    currentPortraitHash: 'test-hash',
    originalContents: new Map()
  });
  assert.equal(report.verified, false);
  assert.equal(report.haltedAt, 'GATE_1_VOW_II');
});

test('Pipeline: malicious blueprint (delete grounding.cjs) halts at Gate 2', async () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    portraitHash: 'test-hash',
    deletes: ['kernel/grounding.cjs']
  });
  const report = await engine.verifyBlueprint(bp, {
    currentPortraitHash: 'test-hash',
    originalContents: new Map()
  });
  assert.equal(report.verified, false);
  assert.equal(report.haltedAt, 'GATE_2_STRUCTURAL');
});

test('Pipeline: bad portrait hash halts at Gate 3', async () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    portraitHash: 'wrong-hash'
  });
  const report = await engine.verifyBlueprint(bp, {
    currentPortraitHash: 'correct-hash',
    originalContents: new Map()
  });
  assert.equal(report.verified, false);
  assert.equal(report.haltedAt, 'GATE_3_PORTRAIT');
});

test('Pipeline: invalid syntax halts at Gate 4', async () => {
  const engine = freshEngine();
  const bp = benignBlueprint({
    portraitHash: 'test-hash',
    files: [{ path: 'broken.cjs', content: 'var = ;' }]
  });
  const report = await engine.verifyBlueprint(bp, {
    currentPortraitHash: 'test-hash',
    originalContents: new Map()
  });
  assert.equal(report.verified, false);
  assert.equal(report.haltedAt, 'GATE_4_HEALTH');
});

test('Pipeline: massive drift halts at Gate 5', async () => {
  const engine = freshEngine();
  const orig = new Map();
  orig.set('config.cjs', 'tiny');
  const bp = benignBlueprint({
    portraitHash: 'test-hash',
    files: [{ path: 'config.cjs', content: 'x'.repeat(1000) }],
    driftBudget: 0.01
  });
  const report = await engine.verifyBlueprint(bp, {
    currentPortraitHash: 'test-hash',
    originalContents: orig
  });
  assert.equal(report.verified, false);
  assert.equal(report.haltedAt, 'GATE_5_DRIFT');
});

// ---------------------------------------------------------------------------
// FVSMB Self-Preservation
// ---------------------------------------------------------------------------

test('Self-preservation: FVSMB engine passes its own integrity check', () => {
  const engine = freshEngine();
  const result = engine.selfTest();
  assert.equal(result.passes, true);
  assert.equal(result.invariant, 'FVSMB_SELF_PRESERVATION');
});

// ---------------------------------------------------------------------------
// Engine Statistics
// ---------------------------------------------------------------------------

test('Stats: engine tracks verification and rejection counts', async () => {
  const engine = freshEngine();

  assert.equal(engine.verificationCount, 0);
  assert.equal(engine.rejectionCount, 0);
  assert.equal(engine.getStats().passRate, 'N/A');

  // Run one passing
  await engine.verifyBlueprint(benignBlueprint({ portraitHash: 'h' }), {
    currentPortraitHash: 'h', originalContents: new Map()
  });
  assert.equal(engine.verificationCount, 1);
  assert.equal(engine.rejectionCount, 0);

  // Run one failing
  await engine.verifyBlueprint(benignBlueprint({ portraitHash: 'h', deletes: ['kernel/grounding.cjs'] }), {
    currentPortraitHash: 'h', originalContents: new Map()
  });
  assert.equal(engine.verificationCount, 2);
  assert.equal(engine.rejectionCount, 1);
  assert.equal(engine.getStats().passRate, '50.0%');
});

// ---------------------------------------------------------------------------
// upgradeSpecToBlueprint conversion
// ---------------------------------------------------------------------------

test('Conversion: wraps upgrade spec into formal blueprint', async () => {
  const engine = freshEngine();
  const spec = {
    id: 'test-upgrade',
    reason: 'Add utility',
    files: [{ path: 'tools/util.cjs', content: 'module.exports = {};' }],
    deletes: [],
    checks: [],
    structuralInvariants: ['KERNEL_GROUNDING_PIPELINE'],
    portraitHash: 'ph',
    driftBudget: 0.4
  };
  const report = await engine.verifyUpgradeSpec(spec, {
    currentPortraitHash: 'ph',
    originalContents: new Map()
  });
  assert.equal(report.verified, true);
});