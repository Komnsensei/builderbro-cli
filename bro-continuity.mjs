'use strict';
// bro-continuity.mjs — BRO's Continuity & Emergence Engine.
//
// Persistence continuity: BRO's long-term memory (~/.bro/memory.json) gets a
// single, schema-compatible reader/writer so facts, observations, decisions,
// errors AND distilled lessons all surface in recall and in the system prompt.
// Sessions (~/.bro/sessions/*.json) roll up into a continuity brief so a new
// instance resumes where the last one left off.
//
// Emergence loop: `digestFailures()` mines BRO's own debug log for recurring
// tool-failure patterns, `remember()` stores them as durable lessons, and
// `proposeBlueprint()` wraps a candidate fix in a formal spec that the
// UpgradeManager can run through the 5-gate FVSMB pipeline as a DRY-RUN —
// observe → reflect → propose → formally verify → (promote only on approval).

import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync } from 'fs';
import { join, resolve } from 'path';
import { homedir } from 'os';

const DEFAULT_DATA_DIR = join(homedir(), '.bro');
const BUCKETS = ['facts', 'observations', 'decisions', 'errors', 'lessons'];
const BUCKET_WEIGHTS = { decision: 6, lesson: 6, fact: 4, observation: 2, error: 1 };
const MAX_BUCKET = 500;

function nowIso(now) {
  return new Date(now()).toISOString();
}

export function createContinuityEngine(options = {}) {
  const dataDir = resolve(options.dataDir || DEFAULT_DATA_DIR);
  const sessionsDir = resolve(options.sessionsDir || join(dataDir, 'sessions'));
  const memoryFile = resolve(options.memoryFile || join(dataDir, 'memory.json'));
  const debugLogFile = resolve(options.debugLogFile || join(dataDir, 'debug.log'));
  const now = options.now || Date.now;

  function ensureDirs() {
    try { mkdirSync(dataDir, { recursive: true }); } catch (_) {}
    try { mkdirSync(sessionsDir, { recursive: true }); } catch (_) {}
  }

  function loadMemory() {
    try {
      const parsed = JSON.parse(readFileSync(memoryFile, 'utf8'));
      const mem = {
        facts: Array.isArray(parsed.facts) ? parsed.facts : [],
        observations: Array.isArray(parsed.observations) ? parsed.observations : [],
        decisions: Array.isArray(parsed.decisions) ? parsed.decisions : [],
        errors: Array.isArray(parsed.errors) ? parsed.errors : [],
        lessons: Array.isArray(parsed.lessons) ? parsed.lessons : [],
        total_interactions: parsed.total_interactions || 0
      };
      return mem;
    } catch (_) {
      return { facts: [], observations: [], decisions: [], errors: [], lessons: [], total_interactions: 0 };
    }
  }

  function saveMemory(mem) {
    try {
      writeFileSync(memoryFile, JSON.stringify(mem), 'utf8');
    } catch (e) {
      return false;
    }
    return true;
  }

  function bucketFor(type) {
    const t = String(type || 'observation').toLowerCase();
    if (t === 'lesson') return 'lessons'; // singular type, plural bucket
    if (BUCKETS.includes(t)) return t;
    if (t === 'fact') return 'facts';
    return 'observations';
  }

  /**
   * Store a durable memory entry. Same signature as the old `memorize()`
   * (type, text, meta) so existing call sites keep working; adds optional
   * `importance` and `tags` via meta for better recall scoring.
   */
  function remember(type, text, meta) {
    if (!text) return false;
    const mem = loadMemory();
    const bucket = bucketFor(type);
    const entry = {
      text: String(text).substring(0, 500),
      meta: meta || {},
      time: now(),
      importance: (meta && meta.importance) || 1
    };
    if (meta && Array.isArray(meta.tags)) entry.tags = meta.tags.slice(0, 10);
    mem[bucket].push(entry);
    if (mem[bucket].length > MAX_BUCKET) mem[bucket].splice(0, mem[bucket].length - MAX_BUCKET);
    mem.total_interactions = (mem.total_interactions || 0) + 1;
    saveMemory(mem);
    return true;
  }

  function allEntries(mem) {
    const out = [];
    for (const bucket of BUCKETS) {
      for (const entry of mem[bucket] || []) {
        out.push({ bucket, ...entry });
      }
    }
    return out;
  }

  /**
   * Score a memory entry against a query: keyword hits on content + tags,
   * recency decay, importance boost, and bucket weight (decisions and lessons
   * matter more than raw observations). This is the FIXED version of the old
   * memorySurface(), which read `mem.entries` — a shape memory.json never had,
   * so it silently returned nothing.
   */
  function scoreEntry(entry, words) {
    const text = ((entry.text || '') + ' ' + (entry.tags || []).join(' ')).toLowerCase();
    let score = 0;
    for (const w of words) {
      if (text.indexOf(w) >= 0) score += 10;
    }
    const ageH = (now() - (entry.time || 0)) / 3600000;
    if (ageH < 24) score += 5;
    else if (ageH < 168) score += 2;
    else if (ageH > 1440) score -= 3;
    score += (Number(entry.importance) || 1) * 2;
    score += BUCKET_WEIGHTS[entry.bucket] || 0;
    if (entry.tags && entry.tags.indexOf('important') >= 0) score += 8;
    return score;
  }

  function recall(query, limit) {
    const mem = loadMemory();
    const entries = allEntries(mem);
    const words = String(query || '').toLowerCase().split(/\s+/).filter(w => w.length > 2);
    if (!entries.length) return [];
    if (!words.length) {
      // No query: surface the most recent + important entries (continuity recall).
      return entries
        .map(e => Object.assign({ score: scoreEntry(e, []) }, e))
        .sort((a, b) => b.score - a.score || (b.time || 0) - (a.time || 0))
        .slice(0, limit || 3);
    }
    return entries
      .map(e => Object.assign({ score: scoreEntry(e, words) }, e))
      .filter(e => e.score > 0)
      .sort((a, b) => b.score - a.score || (b.time || 0) - (a.time || 0))
      .slice(0, limit || 3);
  }

  /** Compact memory context block for the system prompt (replaces buildMemoryContext). */
  function contextBlock(maxChars) {
    const mem = loadMemory();
    const parts = [];
    const facts = (mem.facts || []).slice(-5).map(f => '- ' + f.text);
    if (facts.length) parts.push('Known facts:\n' + facts.join('\n'));
    const observations = (mem.observations || []).slice(-5).map(o => '- ' + o.text);
    if (observations.length) parts.push('Recent context:\n' + observations.join('\n'));
    const lessons = (mem.lessons || []).slice(-3).map(l => '- ' + l.text);
    if (lessons.length) parts.push('Lessons learned:\n' + lessons.join('\n'));
    let block = parts.join('\n\n');
    if (block && maxChars && block.length > maxChars) block = block.substring(0, maxChars);
    return block;
  }

  function stats() {
    const mem = loadMemory();
    const out = { total_interactions: mem.total_interactions || 0 };
    for (const bucket of BUCKETS) out[bucket] = (mem[bucket] || []).length;
    return out;
  }

  // ── Session continuity ──────────────────────────────────────────────────
  function sessionFiles() {
    try {
      return readdirSync(sessionsDir)
        .filter(f => f.endsWith('.json'))
        .map(f => {
          try { return { file: f, mtime: statSync(join(sessionsDir, f)).mtimeMs }; }
          catch (_) { return null; }
        })
        .filter(Boolean)
        .sort((a, b) => b.mtime - a.mtime);
    } catch (_) {
      return [];
    }
  }

  function turnText(turn) {
    if (!turn) return '';
    if (Array.isArray(turn.parts) && turn.parts.length) {
      return (turn.parts[0] && turn.parts[0].text) || '';
    }
    return turn.text || '';
  }

  /**
   * Roll up the N most recent session files into a compact continuity brief:
   * when the last session ended, how many turns it ran, and the last user ask
   * + last BRO reply — enough to resume the thread after a restart.
   */
  function sessionRollup(limit) {
    const files = sessionFiles().slice(0, limit || 3);
    if (!files.length) return '';
    const briefs = [];
    for (const f of files) {
      try {
        const s = JSON.parse(readFileSync(join(sessionsDir, f.file), 'utf8'));
        const turns = Array.isArray(s.turns) ? s.turns : [];
        let lastUser = '', lastBro = '';
        for (const t of turns) {
          const text = turnText(t).substring(0, 120);
          if (!text) continue;
          if (t.role === 'user' || t.role === 'User') lastUser = text;
          else if (t.role === 'model' || t.role === 'assistant' || t.role === 'Assistant') lastBro = text;
        }
        const ended = s.endedAt || f.mtime;
        const ageH = Math.max(0, Math.round((now() - ended) / 3600000));
        const ageText = ageH < 1 ? 'just now' : ageH < 24 ? ageH + 'h ago' : Math.round(ageH / 24) + 'd ago';
        briefs.push(
          (lastUser ? 'last ask: "' + lastUser + '"' : 'no user turns') +
          (lastBro ? ' — last reply: "' + lastBro.substring(0, 80) + '"' : '') +
          ' [' + turns.length + ' turns, ' + ageText + ']'
        );
      } catch (_) { /* unreadable session file - skip */ }
    }
    return briefs.join('\n');
  }

  // ── Emergence loop: digest own failures into lessons ────────────────────
  function readDebugLines() {
    try {
      return readFileSync(debugLogFile, 'utf8').split('\n').filter(Boolean);
    } catch (_) {
      return [];
    }
  }

  /**
   * Mine the debug log for recurring tool-failure signatures. The dbg()
   * writer in cli1.mjs emits `[ISO] toolError: tool args="..." -> ERR...`;
   * we bucket by tool + normalized error prefix and rank by frequency.
   */
  function digestFailures(options) {
    const opts = options || {};
    const windowMs = opts.windowMs || 0; // 0 = all time
    const maxPatterns = opts.maxPatterns || 5;
    const seen = new Map();
    const cutoff = windowMs > 0 ? now() - windowMs : 0;
    for (const line of readDebugLines()) {
      const m = line.match(/^\[([^\]]+)\]\s+(\w+):\s*(.*)$/);
      if (!m) continue;
      const ts = Date.parse(m[1]);
      if (!ts || (cutoff && ts < cutoff)) continue;
      if (m[2] !== 'toolError') continue;
      const detail = m[3];
      const tool = (detail.match(/^([\w-]+)/) || [])[1] || 'unknown';
      const err = (detail.match(/->\s*(ERR\s*\S*|UNKNOWN)/) || [])[1] || 'ERR';
      const sig = tool + '::' + err;
      const cur = seen.get(sig) || { tool, err, count: 0, last: 0, example: '' };
      cur.count++;
      cur.last = Math.max(cur.last, ts);
      if (!cur.example) cur.example = detail.substring(0, 160);
      seen.set(sig, cur);
    }
    return Array.from(seen.values())
      .sort((a, b) => b.count - a.count || b.last - a.last)
      .slice(0, maxPatterns);
  }

  /** Digest failures, store each recurring one as a durable lesson, return summary. */
  function reflect() {
    const patterns = digestFailures({ windowMs: 0, maxPatterns: 5 });
    const stored = [];
    for (const p of patterns) {
      const text = 'Lesson: tool "' + p.tool + '" failed ' + p.count + 'x with ' + p.err + ' — avoid repeating this pattern. ' + p.example;
      remember('lesson', text, { importance: p.count >= 3 ? 2 : 1, tags: ['lesson', p.tool] });
      stored.push(text);
    }
    return { patterns, storedLessons: stored.length };
  }

  /**
   * Wrap a candidate self-improvement as a formal spec the UpgradeManager can
   * verify through FVSMB WITHOUT executing (dry-run). The LLM authors the fix
   * (files/checks); this engine supplies id, reason and shadow mode.
   */
  function proposeBlueprint(fix) {
    const spec = {
      id: 'evolve-' + String(now()).substring(-6),
      mode: 'shadow',
      reason: 'Self-improvement blueprint proposed by reflect (emergence loop)',
      files: [],
      deletes: [],
      checks: []
    };
    if (fix && Array.isArray(fix.files)) spec.files = fix.files;
    if (fix && Array.isArray(fix.deletes)) spec.deletes = fix.deletes;
    if (fix && Array.isArray(fix.checks)) spec.checks = fix.checks;
    if (fix && fix.reason) spec.reason = String(fix.reason).substring(0, 300);
    return spec;
  }

  ensureDirs();

  return {
    remember,
    recall,
    contextBlock,
    stats,
    sessionRollup,
    digestFailures,
    reflect,
    proposeBlueprint,
    // exposed for tests / tools
    dataDir,
    sessionsDir,
    memoryFile,
    debugLogFile,
    now
  };
}

export const continuityEngine = createContinuityEngine();
export default continuityEngine;
