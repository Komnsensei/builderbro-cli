import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, existsSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { UpgradeManager } from '../agent-upgrade.mjs';

function fixture() {
  const rootDir = mkdtempSync(join(tmpdir(), 'bro-upgrade-root-'));
  const dataDir = mkdtempSync(join(tmpdir(), 'bro-upgrade-data-'));
  writeFileSync(join(rootDir, 'app.mjs'), 'export const value = 1;\n');
  writeFileSync(join(rootDir, 'package.json'), '{"name":"fixture","scripts":{"test":"node --check app.mjs"}}\n');
  mkdirSync(join(rootDir, 'node_modules'), { recursive: true });
  writeFileSync(join(rootDir, 'node_modules', 'ignored.js'), 'this is not copied\n');
  return { rootDir, dataDir, cleanup: () => { rmSync(rootDir, { recursive: true, force: true }); rmSync(dataDir, { recursive: true, force: true }); } };
}

test('rejects traversal and secret paths before staging', async () => {
  const f = fixture();
  try {
    const manager = new UpgradeManager({ rootDir: f.rootDir, dataDir: f.dataDir });
    await assert.rejects(() => manager.experiment({ files: [{ path: '../outside.js', content: '' }] }), /inside the workspace/);
    await assert.rejects(() => manager.experiment({ files: [{ path: '.env', content: 'SECRET=x' }] }), /secret/);
    await assert.rejects(() => manager.experiment({ files: [{ path: 'node_modules/x.js', content: '' }] }), /cannot be upgraded/);
  } finally { f.cleanup(); }
});

test('shadow mode validates an isolated candidate and leaves the workspace unchanged', async () => {
  const f = fixture();
  try {
    const manager = new UpgradeManager({ rootDir: f.rootDir, dataDir: f.dataDir });
    const report = await manager.experiment({
      id: 'shadow-good',
      reason: 'try a candidate',
      mode: 'shadow',
      files: [{ path: 'app.mjs', content: 'export const value = 2;\n' }],
      checks: ['node --check app.mjs']
    });
    assert.equal(report.ok, true);
    assert.equal(report.status, 'shadow-passed');
    assert.equal(readFileSync(join(f.rootDir, 'app.mjs'), 'utf8'), 'export const value = 1;\n');
    assert.equal(existsSync(join(f.dataDir, 'shadow-good', 'stage', 'node_modules')), false);
    assert.deepEqual(manager.status()[0].id, 'shadow-good');
  } finally { f.cleanup(); }
});

test('failed promotion health gate does not damage the live workspace', async () => {
  const f = fixture();
  try {
    const manager = new UpgradeManager({ rootDir: f.rootDir, dataDir: f.dataDir });
    const report = await manager.experiment({
      id: 'promote-bad',
      mode: 'promote',
      files: [{ path: 'app.mjs', content: 'export const value = ;\n' }],
      checks: ['node --check app.mjs']
    });
    assert.equal(report.ok, false);
    assert.equal(report.status, 'rolled-back');
    assert.match(report.error.message, /health check failed/);
    assert.equal(readFileSync(join(f.rootDir, 'app.mjs'), 'utf8'), 'export const value = 1;\n');
  } finally { f.cleanup(); }
});

test('promotion is atomic and retains a durable rollback point', async () => {
  const f = fixture();
  try {
    const manager = new UpgradeManager({ rootDir: f.rootDir, dataDir: f.dataDir });
    const report = await manager.experiment({
      id: 'promote-good',
      mode: 'promote',
      files: [{ path: 'app.mjs', content: 'export const value = 3;\n' }],
      checks: ['node --check app.mjs']
    });
    assert.equal(report.ok, true);
    assert.equal(report.status, 'promoted');
    assert.equal(readFileSync(join(f.rootDir, 'app.mjs'), 'utf8'), 'export const value = 3;\n');
    const rollback = manager.rollback('promote-good');
    assert.deepEqual(rollback, { ok: true, id: 'promote-good', status: 'rolled-back' });
    assert.equal(readFileSync(join(f.rootDir, 'app.mjs'), 'utf8'), 'export const value = 1;\n');
  } finally { f.cleanup(); }
});
