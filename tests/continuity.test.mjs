import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createContinuityEngine } from '../bro-continuity.mjs';
import { UpgradeManager } from '../agent-upgrade.mjs';

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'bro-continuity-'));
  mkdirSync(join(dir, 'sessions'), { recursive: true });
  let tick = 0;
  const engine = createContinuityEngine({
    dataDir: dir,
    now: () => 1_800_000_000_000 + (tick++ * 1000) // fixed clock, advances per call
  });
  return {
    dir,
    engine,
    cleanup: () => rmSync(dir, { recursive: true, force: true })
  };
}

test('remember/recall round-trips and stays schema-compatible with existing memory.json', () => {
  const f = fixture();
  try {
    // Pre-seed a memory.json in the OLD shape (facts/observations/decisions/errors)
    // to prove the engine reads what cli1.mjs's memorize() has always written.
    writeFileSync(join(f.dir, 'memory.json'), JSON.stringify({
      facts: [{ text: 'User deploys to Vercel', meta: {}, time: 1_790_000_000_000 }],
      observations: [],
      decisions: [],
      errors: [],
      total_interactions: 1
    }));

    f.engine.remember('fact', 'The build must pass tests before deploy', { tags: ['deploy'] });
    f.engine.remember('lesson', 'Never repeat the identical failing tool call', { importance: 2 });
    f.engine.remember('decision', 'Use shadow upgrades by default', { tags: ['upgrade'] });

    const mem = JSON.parse(readFileSync(join(f.dir, 'memory.json'), 'utf8'));
    assert.ok(Array.isArray(mem.facts));
    assert.ok(Array.isArray(mem.lessons), 'lessons bucket added without breaking old shape');
    assert.equal(mem.facts.length, 2, 'pre-seeded fact preserved');
    assert.ok(mem.total_interactions >= 3);

    const hits = f.engine.recall('deploy', 5);
    assert.ok(hits.length >= 2, 'recall finds deploy-related entries from every bucket');
    assert.ok(hits.some(h => h.text.includes('Vercel')), 'recall finds pre-seeded legacy facts');
    assert.ok(hits.some(h => h.bucket === 'lessons'), 'recall surfaces lessons');
    assert.ok(hits[0].score >= hits[hits.length - 1].score, 'recall returns score-descending');
  } finally { f.cleanup(); }
});

test('recall scores importance and recency above stale low-value entries', () => {
  const f = fixture();
  try {
    f.engine.remember('observation', 'Quick note about pizza', {});
    f.engine.remember('lesson', 'CRITICAL: never rm -rf the database', { importance: 3, tags: ['important'] });
    const hits = f.engine.recall('', 2);
    assert.equal(hits[0].text.includes('database'), true, 'high-importance lesson ranks first');
    assert.ok(hits[0].score > hits[1].score);
  } finally { f.cleanup(); }
});

test('contextBlock includes facts, observations and lessons', () => {
  const f = fixture();
  try {
    f.engine.remember('fact', 'Project uses Node 26', {});
    f.engine.remember('lesson', 'Tests run with node --test', {});
    const ctx = f.engine.contextBlock();
    assert.match(ctx, /Project uses Node 26/);
    assert.match(ctx, /node --test/);
    assert.match(ctx, /Lessons learned/);
  } finally { f.cleanup(); }
});

test('sessionRollup builds a continuity brief from previous sessions', () => {
  const f = fixture();
  try {
    const sessionFile = join(f.dir, 'sessions', '1234-999.json');
    writeFileSync(sessionFile, JSON.stringify({
      endedAt: 1_799_000_000_000,
      pid: 1234,
      turns: [
        { role: 'user', parts: [{ text: 'Fix the login bug' }] },
        { role: 'model', parts: [{ text: 'Found the JWT expiry issue, patched it.' }] }
      ]
    }));
    // Also tolerate the { role, text } turn shape older writers produced.
    writeFileSync(join(f.dir, 'sessions', '2222-888.json'), JSON.stringify({
      endedAt: 1_700_000_000_000,
      turns: [{ role: 'user', text: 'Old shape ask' }, { role: 'assistant', text: 'Old shape reply' }]
    }));
    const brief = f.engine.sessionRollup(2);
    assert.match(brief, /Fix the login bug/);
    assert.match(brief, /JWT expiry/);
    assert.match(brief, /Old shape ask/);
    assert.match(brief, /2 turns/);
  } finally { f.cleanup(); }
});

test('digestFailures mines the debug log and ranks recurring tool errors', () => {
  const f = fixture();
  try {
    const log = join(f.dir, 'debug.log');
    writeFileSync(log, [
      '[2026-01-01T00:00:00.000Z] toolError: exec args="nope" -> ERR 127: command not found',
      '[2026-01-01T00:00:01.000Z] toolError: exec args="nope2" -> ERR 127: command not found',
      '[2026-01-01T00:00:02.000Z] toolError: read args="x" -> ERR ENOENT: no such file',
      '[2026-01-01T00:00:03.000Z] brainFallback: groq failed -> base44: timeout'
    ].join('\n'));
    const patterns = f.engine.digestFailures({ maxPatterns: 5 });
    assert.equal(patterns.length, 2, 'only toolError lines count');
    assert.equal(patterns[0].tool, 'exec');
    assert.equal(patterns[0].count, 2);
    assert.equal(patterns[1].count, 1);
  } finally { f.cleanup(); }
});

test('reflect stores recurring failures as durable lessons', () => {
  const f = fixture();
  try {
    const log = join(f.dir, 'debug.log');
    writeFileSync(log, [
      '[2026-01-01T00:00:00.000Z] toolError: web args="q" -> ERR fetch failed',
      '[2026-01-01T00:00:01.000Z] toolError: web args="q2" -> ERR fetch failed'
    ].join('\n'));
    const result = f.engine.reflect();
    assert.equal(result.patterns.length, 1);
    assert.ok(result.storedLessons >= 1);
    const hits = f.engine.recall('web', 5);
    assert.ok(hits.some(h => h.bucket === 'lessons'), 'reflection lesson is recallable');
  } finally { f.cleanup(); }
});

test('proposeBlueprint wraps a fix as a shadow spec the UpgradeManager can FVSMB-verify (dry-run)', async () => {
  const f = fixture();
  try {
    const rootDir = mkdtempSync(join(tmpdir(), 'bro-continuity-root-'));
    const dataDir = mkdtempSync(join(tmpdir(), 'bro-continuity-data-'));
    try {
      const manager = new UpgradeManager({ rootDir, dataDir });
      const blueprint = f.engine.proposeBlueprint({
        reason: 'fix recurring tool error',
        files: [{ path: 'bro-tools/diag.cjs', content: 'module.exports = { version: () => "1.0" };' }],
        checks: ['node --check bro-tools/diag.cjs']
      });
      assert.equal(blueprint.mode, 'shadow', 'blueprints are always shadow (never auto-promote)');
      assert.match(blueprint.id, /^evolve-/);

      const res = await manager.verify(blueprint);
      assert.equal(res.ok, true, 'FVSMB dry-run completes: ' + JSON.stringify(res.error || ''));
      assert.equal(res.verified, true, 'benign blueprint passes all 5 gates');
      assert.ok(res.report && res.report.gatesPassed === 5, 'report confirms all 5 gates passed');
      assert.equal(res.report.readyForUpgrade, true, 'verified blueprint is ready but NOT executed');
    } finally {
      rmSync(rootDir, { recursive: true, force: true });
      rmSync(dataDir, { recursive: true, force: true });
    }
  } finally { f.cleanup(); }
});
