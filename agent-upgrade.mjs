'use strict';

import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
  renameSync,
  rmSync,
  statSync,
  lstatSync,
  unlinkSync
} from 'fs';
import { execFileSync } from 'child_process';
import { dirname, join, relative, resolve, basename } from 'path';
import { homedir } from 'os';
import { randomUUID, createHash } from 'crypto';
import { createRequire } from 'module';

// CommonJS interop — FVSMB engine and ethics schemas live in NewState/kernel
const _require = createRequire(import.meta.url);

const DEFAULT_DATA_DIR = join(homedir(), '.bro', 'upgrades');
const MAX_FILES = 40;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_CHECK_OUTPUT = 4000;
const CHECK_TIMEOUT_MS = 120000;
const ALLOWED_CHECK_BINARIES = new Set([
  'node', 'npm', 'npx', 'pnpm', 'yarn', 'bun', 'python', 'python3'
]);
const SECRET_PATH = /(^|\/)(\.env(?:\.|$)|.*\.(?:pem|key|p12|pfx)|credentials?(?:\.|$)|secrets?(?:\.|$))/i;
const SHELL_META = /[;&|`$<>]/;

function nowIso() {
  return new Date().toISOString();
}

function ensureDir(path) {
  mkdirSync(path, { recursive: true });
}

function jsonWrite(path, value) {
  writeFileSync(path, JSON.stringify(value, null, 2) + '\n', 'utf8');
}

function truncate(value, max = MAX_CHECK_OUTPUT) {
  return String(value || '').substring(0, max);
}

function parseCommand(command) {
  const text = String(command || '').trim();
  if (!text || SHELL_META.test(text)) {
    throw new Error('health checks must be a single command without shell operators');
  }
  const parts = [];
  const re = /"([^"\\]*(?:\\.[^"\\]*)*)"|'([^']*)'|(\S+)/g;
  let match;
  while ((match = re.exec(text))) {
    parts.push(match[1] !== undefined ? match[1].replace(/\\"/g, '"') : match[2] !== undefined ? match[2] : match[3]);
  }
  if (!parts.length || !ALLOWED_CHECK_BINARIES.has(basename(parts[0]))) {
    throw new Error('health checks may use only node, npm, npx, pnpm, yarn, bun, python, or python3');
  }
  return parts;
}

function defaultRunner(command, cwd) {
  const parts = parseCommand(command);
  try {
    const output = execFileSync(parts[0], parts.slice(1), {
      cwd,
      encoding: 'utf8',
      timeout: CHECK_TIMEOUT_MS,
      maxBuffer: 1024 * 1024,
      stdio: ['ignore', 'pipe', 'pipe']
    });
    return { ok: true, code: 0, output: truncate(output) };
  } catch (error) {
    return {
      ok: false,
      code: typeof error.status === 'number' ? error.status : 1,
      output: truncate((error.stdout || '') + (error.stderr || '') || error.message)
    };
  }
}

/**
 * Compute SHA-256 hash of a file. Used for portrait hash verification.
 */
function hashFile(path) {
  try {
    return createHash('sha256').update(readFileSync(path, 'utf8')).digest('hex');
  } catch {
    return null;
  }
}

/**
 * Load the FVSMB engine if available. Returns null if the file doesn't exist.
 * FVSMB is the pinnacle gate that all self-modifications must pass through.
 */
function _loadFvsmbEngine() {
  try {
    const fvsmb = _require('./NewState/kernel/governor/fvsmb-engine.cjs');
    return new fvsmb.FVSMBEngine({
      logger: (msg) => process.stderr.write(`[FVSMB-UpgradeManager] ${msg}\n`)
    });
  } catch (err) {
    // FVSMB engine not available — upgrades proceed without formal verification.
    // This is a fallback for minimal installations.
    process.stderr.write(`[UpgradeManager] FVSMB engine unavailable: ${err.message}. Self-modifications will proceed WITHOUT formal verification.\n`);
    return null;
  }
}

/**
 * Collect original file contents for drift budget computation.
 */
function _collectOriginalContents(rootDir, spec) {
  const contents = new Map();
  const allPaths = [
    ...(spec.files || []).map(f => typeof f === 'object' ? f.path : f),
    ...(spec.deletes || [])
  ];
  for (const relPath of allPaths) {
    try {
      const absPath = resolve(rootDir, relPath);
      if (existsSync(absPath) && lstatSync(absPath).isFile()) {
        contents.set(relPath, readFileSync(absPath, 'utf8'));
      }
    } catch (_) {
      // File doesn't exist or can't be read
    }
  }
  return contents;
}

function normalizeFiles(files) {
  if (Array.isArray(files)) return files;
  if (files && typeof files === 'object') {
    return Object.entries(files).map(([path, content]) => ({ path, content }));
  }
  return [];
}

export class UpgradeManager {
  constructor({ rootDir = process.cwd(), dataDir = DEFAULT_DATA_DIR, runner = defaultRunner, logger = () => {}, now = Date.now, fvsmbEngine = undefined } = {}) {
    this.rootDir = resolve(rootDir);
    this.dataDir = resolve(dataDir);
    this.runner = runner;
    this.logger = logger;
    this.now = now;
    ensureDir(this.dataDir);

    // FVSMB INTEGRATION: The Formal Verification of Self-Modification Blueprints engine.
    // All self-modifications (promote mode upgrades that modify BRO's own codebase)
    // must pass through the 5-gate FVSMB pipeline before execution.
    // If explicitly set to null, FVSMB is disabled. If undefined, auto-load.
    if (fvsmbEngine === undefined) {
      this.fvsmb = _loadFvsmbEngine();
    } else {
      this.fvsmb = fvsmbEngine; // null = explicitly disabled
    }

    if (this.fvsmb) {
      this.logger(`[UpgradeManager] FVSMB engine loaded — self-modifications will be formally verified.`);
      // Self-test the FVSMB engine on construction
      try {
        const selfTest = this.fvsmb.selfTest();
        if (!selfTest.passes) {
          this.logger(`[UpgradeManager] ⚠ FVSMB self-test FAILED: ${selfTest.reason}`);
        }
      } catch (err) {
        this.logger(`[UpgradeManager] ⚠ FVSMB self-test error: ${err.message}`);
      }
    }
  }

  setRootDir(rootDir) {
    this.rootDir = resolve(rootDir);
  }

  _assertRelativePath(path) {
    if (typeof path !== 'string' || !path.trim()) throw new Error('candidate path is required');
    const normalized = path.replace(/\\/g, '/');
    const absolute = resolve(this.rootDir, normalized);
    const rel = relative(this.rootDir, absolute).replace(/\\/g, '/');
    if (!rel || rel === '..' || rel.startsWith('../') || rel.startsWith('/')) {
      throw new Error('candidate paths must stay inside the workspace');
    }
    if (SECRET_PATH.test(rel) || rel === '.git' || rel.startsWith('.git/') || rel === 'node_modules' || rel.startsWith('node_modules/') || rel === '.bro' || rel.startsWith('.bro/')) {
      throw new Error('secret, credential, dependency, runtime, and git metadata paths cannot be upgraded');
    }
    if (existsSync(absolute) && !lstatSync(absolute).isFile()) {
      throw new Error(`candidate path must be a file: ${rel}`);
    }
    return rel;
  }

  _normalizeSpec(spec) {
    if (!spec || typeof spec !== 'object') throw new Error('upgrade request must be a JSON object');
    const files = normalizeFiles(spec.files).map((file) => {
      const path = this._assertRelativePath(file.path);
      const content = String(file.content ?? '');
      if (Buffer.byteLength(content, 'utf8') > MAX_FILE_BYTES) {
        throw new Error(`candidate file is too large: ${path}`);
      }
      return { path, content };
    });
    const deletes = Array.isArray(spec.deletes) ? spec.deletes.map((path) => this._assertRelativePath(path)) : [];
    const seen = new Set();
    for (const file of files) {
      if (seen.has(file.path)) throw new Error(`duplicate candidate path: ${file.path}`);
      seen.add(file.path);
    }
    for (const path of deletes) {
      if (seen.has(path)) throw new Error(`path cannot be both written and deleted: ${path}`);
      seen.add(path);
    }
    if (!files.length && !deletes.length) throw new Error('upgrade must include files or deletes');
    if (seen.size > MAX_FILES) throw new Error(`upgrade exceeds the ${MAX_FILES}-path limit`);
    const checks = Array.isArray(spec.checks) ? spec.checks.map((check) => String(check).trim()).filter(Boolean) : [];
    checks.forEach(parseCommand);
    const mode = spec.mode === 'promote' ? 'promote' : 'shadow';
    return {
      id: String(spec.id || '').trim().replace(/[^a-zA-Z0-9._-]/g, '-').substring(0, 80) || `upgrade-${this.now()}`,
      mode,
      reason: String(spec.reason || 'experimental self-upgrade').substring(0, 300),
      files,
      deletes,
      checks
    };
  }

  _recordDir(id) {
    return join(this.dataDir, id);
  }

  _recordPath(id) {
    return join(this._recordDir(id), 'record.json');
  }

  _loadRecord(id) {
    const path = this._recordPath(id);
    if (!existsSync(path)) throw new Error(`upgrade not found: ${id}`);
    return JSON.parse(readFileSync(path, 'utf8'));
  }

  _snapshotPath(recordDir, rel) {
    return join(recordDir, 'snapshot', rel);
  }

  _stagePath(recordDir, rel) {
    return join(recordDir, 'stage', rel);
  }

  _mirrorWorkspace(stageDir) {
    const copyTree = (source, target, rel = '') => {
      const name = basename(source);
      const currentRel = rel ? `${rel}/${name}` : name;
      if (name === '.git' || name === 'node_modules' || name === '.bro' || SECRET_PATH.test(currentRel)) return;
      const info = lstatSync(source);
      if (info.isSymbolicLink()) return;
      if (info.isDirectory()) {
        ensureDir(target);
        for (const child of readdirSync(source)) copyTree(join(source, child), join(target, child), currentRel);
        return;
      }
      if (info.isFile()) {
        ensureDir(dirname(target));
        writeFileSync(target, readFileSync(source));
      }
    };
    ensureDir(stageDir);
    for (const entry of readdirSync(this.rootDir)) {
      try {
        copyTree(join(this.rootDir, entry), join(stageDir, entry));
      } catch (error) {
        throw new Error(`could not stage workspace entry ${entry}: ${error.message}`);
      }
    }
  }

  _takeSnapshot(recordDir, paths) {
    for (const rel of paths) {
      const target = resolve(this.rootDir, rel);
      const snapshot = this._snapshotPath(recordDir, rel);
      if (existsSync(target) && statSync(target).isFile()) {
        ensureDir(dirname(snapshot));
        writeFileSync(snapshot, readFileSync(target));
      }
    }
  }

  _restore(record) {
    for (const item of record.paths) {
      const target = resolve(this.rootDir, item.path);
      const snapshot = this._snapshotPath(this._recordDir(record.id), item.path);
      if (item.existed) {
        ensureDir(dirname(target));
        writeFileSync(target, readFileSync(snapshot));
      } else if (existsSync(target)) {
        rmSync(target, { force: true });
      }
    }
  }

  _atomicWrite(target, content) {
    ensureDir(dirname(target));
    const temp = `${target}.bro-upgrade-${process.pid}-${randomUUID()}.tmp`;
    try {
      writeFileSync(temp, content, 'utf8');
      renameSync(temp, target);
    } finally {
      if (existsSync(temp)) unlinkSync(temp);
    }
  }

  _buildChecks(spec) {
    if (spec.checks.length) return spec.checks;
    return spec.files
      .filter((file) => /\.(?:js|mjs|cjs)$/i.test(file.path))
      .map((file) => `node --check ${JSON.stringify(file.path)}`);
  }

  async _runChecks(checks, cwd) {
    const results = [];
    for (const command of checks) {
      const result = await this.runner(command, cwd);
      const normalized = {
        command,
        ok: !!result.ok,
        code: result.code ?? (result.ok ? 0 : 1),
        output: truncate(result.output)
      };
      results.push(normalized);
      if (!normalized.ok) {
        const error = new Error(`health check failed: ${command}`);
        error.checks = results;
        throw error;
      }
    }
    return results;
  }

  /**
   * Run the 5-gate FVSMB pipeline against a normalized spec and return the
   * verification report WITHOUT throwing. This is the pure verification core,
   * shared by both experiment() (throws on failure) and verify() (dry-run).
   *
   * @param {Object} spec - The normalized upgrade spec
   * @returns {Object|null} FVSMB verification report, or null if FVSMB is disabled
   */
  async _runFVSMB(spec) {
    if (!this.fvsmb) {
      this.logger(`[UpgradeManager] FVSMB disabled — proceeding without formal verification.`);
      return null;
    }

    this.logger(`[UpgradeManager:FVSMB] Verifying blueprint '${spec.id}' through 5-gate pipeline...`);

    // Compute context for verification
    const portraitPath = resolve(this.rootDir, 'NewState', 'kernel', 'PORTRAIT.md');
    const currentPortraitHash = hashFile(portraitPath);
    const originalContents = _collectOriginalContents(this.rootDir, spec);

    // Build the formal blueprint from the upgrade spec
    const blueprint = {
      id: spec.id,
      intent: spec.reason || 'Self-modification upgrade',
      reason: spec.reason || 'Experimental self-upgrade',
      targets: [...spec.files.map(f => f.path), ...spec.deletes],
      files: spec.files,
      deletes: spec.deletes,
      checks: spec.checks,
      structuralInvariants: spec.structuralInvariants || [],
      portraitHash: spec.portraitHash || currentPortraitHash,
      driftBudget: spec.driftBudget
    };

    // Run the 5-gate verification pipeline
    return this.fvsmb.verifyBlueprint(blueprint, {
      currentPortraitHash,
      originalContents
    });
  }

  /**
   * FVSMB GATE: Verify a self-modification blueprint before execution.
   *
   * This is the PINNACLE MOVE — any self-modification must pass through
   * the 5-gate FVSMB pipeline:
   *   1. VOW II Ethics
   *   2. Structural Invariants
   *   3. Portrait Seal
   *   4. Health Checks
   *   5. Drift Budget
   *
   * Only on full pass does the upgrade proceed.
   *
   * @param {Object} spec - The normalized upgrade spec
   * @returns {Object|null} FVSMB verification report, or null if FVSMB is disabled
   * @throws {Error} If FVSMB verification fails (upgrade is halted)
   */
  async _verifyWithFVSMB(spec) {
    const report = await this._runFVSMB(spec);
    if (report === null) return null; // FVSMB disabled

    // Note: FVSMB report is persisted in experiment() alongside the upgrade record,
    // not here, to avoid creating directories before duplicate-ID checks.

    if (!report.verified) {
      const haltedAt = report.haltedAt;
      const msg = `FVSMB VERIFICATION FAILED at ${haltedAt}: Blueprint '${spec.id}' does not meet formal verification requirements. ` +
        `Recommendations: ${report.recommendations.join(' | ')}`;
      this.logger(`[UpgradeManager:FVSMB] ✗ ${msg}`);
      const error = new Error(msg);
      error.fvsmbReport = report;
      throw error;
    }

    this.logger(`[UpgradeManager:FVSMB] ✓ Blueprint '${spec.id}' passed all 5 gates. Proceeding to execution.`);
    if (report.warnings && report.warnings.length > 0) {
      this.logger(`[UpgradeManager:FVSMB] ⚠ ${report.warnings.length} warning(s) — review recommended.`);
    }

    return report;
  }

  /**
   * FVSMB DRY-RUN: Verify a self-modification blueprint WITHOUT executing it.
   *
   * This is the formal-specification entry point — author a blueprint, run it
   * through the 5-gate pipeline, and receive a full verification report with
   * no side effects. Returns the report even when verification fails (unlike
   * experiment(), which halts).
   *
   * @param {Object} input - Raw upgrade spec (id, reason, files, deletes, checks...)
   * @returns {Object} { ok, verified, id, report, error }
   */
  async verify(input) {
    let spec;
    try {
      spec = this._normalizeSpec(input);
    } catch (e) {
      return { ok: false, verified: false, error: e.message };
    }

    if (!this.fvsmb) {
      return {
        ok: false,
        verified: false,
        id: spec.id,
        error: 'FVSMB engine unavailable — cannot formally verify a blueprint.'
      };
    }

    const report = await this._runFVSMB(spec);
    return {
      ok: true,
      verified: !!report.verified,
      id: spec.id,
      report
    };
  }

  async experiment(input) {
    const spec = this._normalizeSpec(input);

    // ─── FVSMB PINNACLE GATE ───
    // Before any self-modification proceeds, it must be formally verified.
    // This is the mathematical halt point — the upgrade stops here if any
    // ethical invariant, structural invariant, portrait seal, health check,
    // or drift budget constraint is violated.
    const fvsmbReport = await this._verifyWithFVSMB(spec);
    // ────────────────────────────

    const recordDir = this._recordDir(spec.id);
    if (existsSync(recordDir)) throw new Error(`upgrade id already exists: ${spec.id}`);
    ensureDir(recordDir);
    ensureDir(join(recordDir, 'snapshot'));
    ensureDir(join(recordDir, 'stage'));

    const paths = [...spec.files.map((file) => file.path), ...spec.deletes];
    const record = {
      version: 1,
      id: spec.id,
      mode: spec.mode,
      reason: spec.reason,
      rootDir: this.rootDir,
      createdAt: nowIso(),
      status: 'created',
      paths: paths.map((path) => ({ path, existed: existsSync(resolve(this.rootDir, path)) })),
      checks: [],
      error: null
    };
    jsonWrite(this._recordPath(spec.id), record);

    try {
      this._takeSnapshot(recordDir, paths);
      this._mirrorWorkspace(join(recordDir, 'stage'));
      for (const file of spec.files) this._atomicWrite(this._stagePath(recordDir, file.path), file.content);
      for (const path of spec.deletes) {
        const stagedTarget = this._stagePath(recordDir, path);
        if (existsSync(stagedTarget)) rmSync(stagedTarget, { force: true });
      }
      record.status = 'staged';
      jsonWrite(this._recordPath(spec.id), record);

      // Persist FVSMB report alongside the upgrade record
      if (fvsmbReport) {
        try {
          const fvsmbReportPath = join(recordDir, 'fvsmb-report.json');
          writeFileSync(fvsmbReportPath, JSON.stringify(fvsmbReport, null, 2), 'utf8');
        } catch (err) {
          this.logger(`[UpgradeManager:FVSMB] Could not persist verification report: ${err.message}`);
        }
        record.fvsmbVerified = true;
        record.fvsmbGatesPassed = fvsmbReport.gatesPassed;
        record.fvsmbSignature = fvsmbReport.verificationSignature;
        jsonWrite(this._recordPath(spec.id), record);
      }

      const checks = this._buildChecks(spec);
      record.checks = await this._runChecks(checks, join(recordDir, 'stage'));
      record.status = spec.mode === 'shadow' ? 'shadow-passed' : 'validated';
      jsonWrite(this._recordPath(spec.id), record);

      if (spec.mode === 'shadow') {
        return { ok: true, id: spec.id, status: record.status, mode: spec.mode, checks: record.checks, fallback: 'unchanged workspace' };
      }

      for (const file of spec.files) this._atomicWrite(resolve(this.rootDir, file.path), file.content);
      for (const path of spec.deletes) {
        const target = resolve(this.rootDir, path);
        if (existsSync(target)) rmSync(target, { force: true });
      }
      record.liveChecks = await this._runChecks(checks, this.rootDir);
      record.status = 'promoted';
      record.promotedAt = nowIso();
      jsonWrite(this._recordPath(spec.id), record);
      this.logger(`upgrade promoted: ${spec.id}`);
      return { ok: true, id: spec.id, status: record.status, mode: spec.mode, checks: record.checks, rollback: `upgrade rollback ${spec.id}` };
    } catch (error) {
      record.error = { message: error.message, checks: error.checks || [] };
      try {
        if (spec.mode === 'promote') this._restore(record);
        record.status = spec.mode === 'promote' ? 'rolled-back' : 'failed';
      } catch (rollbackError) {
        record.status = 'rollback-failed';
        record.error.rollback = rollbackError.message;
      }
      jsonWrite(this._recordPath(spec.id), record);
      this.logger(`upgrade ${record.status}: ${spec.id}`);
      return { ok: false, id: spec.id, status: record.status, error: record.error, fallback: spec.mode === 'promote' ? 'previous workspace restored when possible' : 'workspace unchanged' };
    }
  }

  rollback(id) {
    const record = this._loadRecord(id);
    if (resolve(record.rootDir) !== this.rootDir) {
      throw new Error(`upgrade ${id} belongs to workspace ${record.rootDir}`);
    }
    if (!['promoted', 'rolled-back'].includes(record.status)) {
      throw new Error(`upgrade ${id} is not a promoted transaction`);
    }
    this._restore(record);
    record.status = 'rolled-back';
    record.rolledBackAt = nowIso();
    jsonWrite(this._recordPath(id), record);
    this.logger(`upgrade rolled back: ${id}`);
    return { ok: true, id, status: record.status };
  }

  status() {
    ensureDir(this.dataDir);
    return readdirSync(this.dataDir)
      .map((id) => {
        try {
          const record = this._loadRecord(id);
          return { id: record.id, status: record.status, mode: record.mode, createdAt: record.createdAt, promotedAt: record.promotedAt || null };
        } catch (_) {
          return null;
        }
      })
      .filter(Boolean)
      .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  }
}

export function formatUpgradeReport(report) {
  return JSON.stringify(report, null, 2);
}

export function createUpgradeManager(options = {}) {
  return new UpgradeManager(options);
}
