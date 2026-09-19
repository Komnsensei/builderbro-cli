#!/usr/bin/env python3
"""
memory.py — episodic memory with provenance (A2 of AUTONOMY-UPGRADE.md).

WHY THIS EXISTS
---------------
The ledger was write-only. `freebrain-residence/ledger.jsonl` accumulated
outcomes that nothing ever read back, so every run started from zero knowledge
even when an earlier run had already *established* the fact it needed — which is
the difference between a loop that repeats work and an agent that has a history.

THE RULE THIS MODULE ENFORCES
-----------------------------
`AGENT-INTEGRITY.md` names three levels and one promotion gate, and this module
does not invent its own:

    invariant -> may be consumed as a constraint anywhere
    observed  -> real tool output, shaped but not cross-checked
    volatile  -> model claim, guess, parse or apology — must be gated

    * All creation is volatile. Model text enters as a claim. Tool output
      enters as an observation. **Nothing is ever born invariant.**
    * Promotion is gated: `observed -> invariant` only through a named
      independent check — never by repetition, which is a volatile signal.
    * Using a `volatile`/`observed` item as a constraint raises
      `VolatileAsInvariantError`.

The level a record is born with comes from A3's verifier, and the mapping is the
whole point of the boundary (`level_for`): A3's `invariant` (confirmed by a check
that reproduced) becomes `invariant`; A3's `observed` (real output, not
re-observed) stays `observed`; A3's `declared` — the model's own expectation with
no independent confirmation, i.e. the verifier is off — becomes `volatile`.
Without that last line, turning the verifier off would be a way to launder a
model's claim into a remembered fact.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No embeddings, no ranking model, no decay. Recall is lexical overlap with the
goal, stdlib only, in the same spirit as `rag_core.py`. When a measurement says
lexical recall is what is failing, that is the time to add a better one.

USAGE
-----
    from memory import MemoryStore
    store = MemoryStore("freebrain-residence/memory.jsonl")
    store.add(make_record(goal, "list_dir", ".", out, level="invariant",
                          how_verified="list_dir:expectation_held+reproduced"))
    items = recall(store.records(), "which file did the listing find?")
    print(render(items))
"""

import datetime
import json
import os
import re

# ── The three levels, verbatim from AGENT-INTEGRITY.md ────────────────────────
LEVELS = ("invariant", "observed", "volatile")

# Only these may be presented as a fact or consumed as a constraint.
PROMOTABLE = ("invariant",)

FACT_HEADER = ("Known facts (independently confirmed in an earlier run — these "
               "may be used as constraints):")
UNVERIFIED_HEADER = ("Recent context (NOT verified this run — re-check before "
                     "relying on it, and never use it as a constraint):")

# verifier.Verdict.level -> memory level. See the module docstring: the `declared`
# row is what keeps a verifier-off run from manufacturing facts.
EVIDENCE_LEVELS = {
    "invariant": "invariant",
    "observed": "observed",
    "declared": "volatile",
}


class ProvenanceError(ValueError):
    """A non-invariant item was used as a fact or promoted without a check.

    AGENT-INTEGRITY.md's `VolatileAsInvariantError`: the failure mode is not a
    wrong value, it is a claim that has lost its provenance.
    """


def level_for(evidence_level):
    """Map a verifier evidence level to a memory level.

    Unknown levels are `volatile`, not `observed`: an unrecognised provenance is
    an unknown one, and the safe direction is down.
    """
    return EVIDENCE_LEVELS.get(evidence_level, "volatile")


# ── Recall ────────────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.\-/:]*")

# Recall is scored as the fraction of the goal's content words that a record's
# own text shares. A stop list is needed because goals and plans share so much
# scaffolding ("read", "file", "tool", "step") that without it every record looks
# relevant to every goal.
STOP = frozenset("""
a an the and or of to in on for with is are was were be been being it its this
that these those then than from at as by if not no do does did done use used
using read list dir file files tool tools step steps goal check expect final
reply only now what which where how who when why many much more less name names
contents content me my your you i we they them he she his her our their there
here all any some into also about over under out up down can could should must
will would shall may might have has had please tell show give find get
""".split())


def _tokens(text):
    return set(_WORD_RE.findall((text or "").lower()))


def content_tokens(text):
    """Goal words with the shared scaffolding removed — the query vector."""
    return _tokens(text) - STOP


def relevance(rec, query_tokens):
    """Fraction of the query's content words this record's own text covers.

    In [0, 1], and 0.0 whenever either side is empty rather than a default score:
    a record with no text is not a partial match to anything.
    """
    if not query_tokens:
        return 0.0
    doc = (_tokens(rec.get("goal")) | _tokens(rec.get("tool"))
           | _tokens(rec.get("arg")))
    if not doc:
        return 0.0
    return len(query_tokens & doc) / float(len(query_tokens))


def recall(records, goal, *, limit=3, min_score=0.25):
    """Past records relevant to `goal`, newest-first among equals.

    Returns copies carrying a `score`, so a caller can report why something was
    recalled instead of presenting the choice as arbitrary.
    """
    query = content_tokens(goal)
    scored = []
    for rec in records:
        score = relevance(rec, query)
        if score >= min_score:
            scored.append((score, rec))
    # Deterministic, and stable in two passes so the secondary keys reverse with
    # the timestamp: score first, then newest, then tool+arg. Newest-first among
    # equals because a fact confirmed later is the one more likely still to hold.
    scored.sort(key=lambda pair: (pair[1].get("ts") or "",
                                  "%s %s" % (pair[1].get("tool"), pair[1].get("arg"))),
                reverse=True)
    scored.sort(key=lambda pair: -pair[0])
    return [dict(rec, score=round(score, 4)) for score, rec in scored[:limit]]


# ── Records ───────────────────────────────────────────────────────────────────

def collapse(text, limit=None):
    """One line, whitespace collapsed — a record is a line in a prompt."""
    flat = " ".join((text or "").split())
    if limit is not None and len(flat) > limit:
        flat = flat[:limit].rstrip() + "…"
    return flat


def make_record(goal, tool, arg, value, *, level, how_verified, step=None,
                run_id=None, expect=None, value_chars=400, ts=None):
    """One past action and its outcome, with the level it was born at.

    `level` must be a memory level (see `level_for`); the caller is expected to
    have derived it from the verifier rather than to have chosen it.
    """
    if level not in LEVELS:
        raise ProvenanceError("unknown level %r (expected one of %s)"
                              % (level, ", ".join(LEVELS)))
    full = (value or "")
    return {
        "ts": ts or datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"),
        "run_id": run_id,
        "goal": collapse(goal, 400),
        "step": step,
        "tool": tool,
        "arg": arg,
        "expect": expect,
        "level": level,
        "how_verified": how_verified,
        "chars": len(full),
        "value": collapse(full, value_chars),
        "value_truncated": len(collapse(full)) > len(collapse(full, value_chars)),
    }


class MemoryStore(object):
    """Append-only JSONL store. One record per line, like every other ledger here.

    A corrupt or half-written line is skipped rather than raised: memory is an
    optimisation on a cold start, and a store that cannot be read must degrade to
    "no history", never to a run that will not start.
    """

    def __init__(self, path):
        self.path = path

    def add(self, rec):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
        return rec

    def records(self):
        if not os.path.isfile(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
        return out

    def __len__(self):
        return len(self.records())

    def clear(self):
        if os.path.isfile(self.path):
            os.remove(self.path)


def open_store(spec):
    """`None` | path | MemoryStore -> MemoryStore | None.

    `None` stays `None` on purpose: the loop's default is *no memory*, so every
    existing caller (and every test) is unaffected by A2 existing, and a run only
    has a history when a caller names one.
    """
    if spec is None:
        return None
    if isinstance(spec, MemoryStore):
        return spec
    return MemoryStore(str(spec))


# ── Render ────────────────────────────────────────────────────────────────────

def facts(items):
    return [r for r in items if r.get("level") in PROMOTABLE]


def unverified(items):
    return [r for r in items if r.get("level") not in PROMOTABLE]


def format_record(rec, value_chars=400):
    return "- `%s %s` -> %s  [%s; %s]" % (
        rec.get("tool"), rec.get("arg"),
        collapse(rec.get("value"), value_chars) or "(no output)",
        rec.get("level"), rec.get("how_verified") or "unrecorded")


def render(items, *, value_chars=400, max_chars=2000):
    """The prompt block. A fact can only reach the facts section by being one.

    The split is structural rather than advisory: `unverified()` is the
    complement of `facts()`, and the facts section is built from `facts()` alone.
    There is no argument to this function that puts a `volatile` record above the
    unverified header — which is what makes A2's false-recall rate a property of
    the code instead of a property of the prompt's wording.
    """
    f, u = facts(items), unverified(items)
    if not f and not u:
        return ""
    parts = [FACT_HEADER]
    parts += [format_record(r, value_chars) for r in f] if f else ["(none)"]
    if u:
        parts.append(UNVERIFIED_HEADER)
        parts += [format_record(r, value_chars) for r in u]
    text = "\n".join(parts)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _needle(rec, min_len=8):
    """A distinctive excerpt of a record's value, for the leakage audit."""
    val = collapse(rec.get("value"))
    if len(val) >= min_len:
        return val[:60]
    return ""


def audit(items, text):
    """Check a rendered block against the items it claims to render.

    Returns `{"violations": [...], "checked": n, "unchecked": m}`. Two violation
    kinds, both of which are the same defect seen from either side:

      * `unverified_value_in_facts` — an `observed`/`volatile` value appears above
        the unverified header. This is false recall: a value the system never
        verified, presented as a constraint.
      * `fact_without_named_check` — an `invariant` with no `how_verified`. An
        invariant that cannot say what confirmed it is an assertion wearing a
        level.

    `unchecked` counts records whose value was too short to search for. Reported
    rather than hidden, so a clean result cannot be a search that found nothing
    to look at.
    """
    violations, checked, unchecked = [], 0, 0
    head = text.split(UNVERIFIED_HEADER, 1)[0]
    for rec in unverified(items):
        needle = _needle(rec)
        if not needle:
            unchecked += 1
            continue
        checked += 1
        if needle in head:
            violations.append({
                "kind": "unverified_value_in_facts", "tool": rec.get("tool"),
                "arg": rec.get("arg"), "level": rec.get("level"),
                "value": needle})
    for rec in facts(items):
        if not (rec.get("how_verified") or "").strip():
            violations.append({
                "kind": "fact_without_named_check", "tool": rec.get("tool"),
                "arg": rec.get("arg"), "level": rec.get("level")})
    return {"violations": violations, "checked": checked, "unchecked": unchecked}


# ── Promotion ─────────────────────────────────────────────────────────────────

def as_fact(rec):
    """Return the record if it may be used as a constraint, else raise."""
    if rec.get("level") not in PROMOTABLE:
        raise ProvenanceError(
            "a %r record cannot be used as a fact (verified by %r); only %s may. "
            "Promote it through a named independent check first."
            % (rec.get("level"), rec.get("how_verified"), "/".join(PROMOTABLE)))
    return rec


def promote(rec, *, how_verified, to_level="invariant"):
    """`observed -> invariant`, gated on a named independent check.

    Refuses a `volatile` record outright: a model's claim is not a fact one check
    away, it is a claim. Refuses an unnamed check, because AGENT-INTEGRITY
    requires a promotion to say what confirmed it. Repetition is not a check —
    calling this twice with the same `how_verified` promotes nothing new, and the
    audit's `reproduced` check is what "observed it again" has to mean.
    """
    if to_level not in PROMOTABLE:
        raise ProvenanceError("promotion target %r is not a fact level (%s)"
                              % (to_level, "/".join(PROMOTABLE)))
    if rec.get("level") == "volatile":
        raise ProvenanceError(
            "a volatile record is a claim, not an observation — it cannot be "
            "promoted to %r; re-observe it instead" % to_level)
    if not (how_verified or "").strip():
        raise ProvenanceError("a promotion must name the independent check that "
                              "confirmed it")
    return dict(rec, level=to_level, how_verified=how_verified,
                promoted_from=rec.get("level"))
