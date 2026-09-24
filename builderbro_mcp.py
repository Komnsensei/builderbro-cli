#!/usr/bin/env python3
"""builderbro_mcp.py — BuilderBro's verification stack, served over MCP.

WHY THIS EXISTS
---------------
BuilderBro began as a *model-driven loop*: a hosted chat model planned steps,
emitted tool directives, and the loop checked them. Measured live, the loop's
weakest link was never the checking — it was the planner. Across the recorded
unseeded runs the hosted hop answered from priors instead of acting (asserting
`0.75` where the file said `0.25`) and never called a tool at all in the
majority of attempts (`live-unseeded-summary.json`, `live-unseeded-transcript.jsonl`).

The parts that *do* work are the parts about verification: the adversarial
verifier (`verifier.py`, A3), provenance-gated memory (`memory.py`, A2), the
evidence hygiene classifier (`evidence_hygiene.py`) and grounded retrieval
(`.agents/skills/builderbro-rag/`). So the split this server implements is:

    an agent that already acts  ->  plans, reads, writes, runs
    BuilderBro                  ->  refuses unverified claims, remembers with
                                    provenance, audits what was recorded

Freebuff is that agent. `.agents/mcp.json` in this repo registers this server
with it — `freebuff --trust-agents` walks `<cwd>/.agents`, `<cwd>/../.agents`
and `~/.agents`, opens `mcp.json` in each, and reads
`mcpServers[name].command` / `.args` / `.env`, namespacing the loaded tools as
`name__tool`. The file has to sit in one of those three directories and use only
those keys: the loader opens nothing at the repository root, and its entry schema
is strict, so a misplaced or over-decorated file is skipped in silence
(`RegistryTest` in the test module asserts exactly that).

WHAT THIS SERVER DELIBERATELY DOES NOT DO
-----------------------------------------
- **It re-implements no check.** `autonomy.parse_spec` + `autonomy.verify` for
  the expectation grammar, `verifier.confirm` for promotion, `memory.*` for
  provenance, `evidence_hygiene.classify` for stub detection, the rag skill for
  retrieval. Every rule lives in exactly one module.
- **It does not widen the verifier's policy.** The deterministic-tool set that
  gates `invariant` promotion is `verifier.DETERMINISTIC_TOOLS`; no argument to
  any tool here adds a tool to it.
- **It does not launder provenance.** `memory_record` writes at
  `memory.level_for(evidence_level)` and refuses an `invariant` write that names
  no independent check — `memory.promote`'s own rule, enforced at the seam.

THE ONE THING A CALLER MUST SUPPLY HONESTLY
-------------------------------------------
`verify_claim` can reach level `invariant` only via a re-observation, and this
server has no tools of its own, so it cannot perform one. A caller may pass
`second_output`: a *fresh* observation it obtained itself. The server does test
that against the same expectation (that part is real and is the whole reason the
check exists) but it cannot test its independence, so any verdict that used one
says so — `"independence": "caller-attested"`. An agent that re-reads the file
and passes what it saw is doing the honest thing. An agent that passes `output`
back in is defeating the check, and this response makes that legible instead of
letting it pass silently.

USAGE
-----
    python3 builderbro_mcp.py                       # serve MCP on stdio
    python3 builderbro_mcp.py --tools               # the tool schemas, as JSON
    python3 builderbro_mcp.py --call verify_claim --json '{"tool":"read_file",...}'
    python3 builderbro_mcp.py --call rag_ask --json '{"question":"..."}' --human

Nothing but JSON-RPC is ever written to stdout; diagnostics go to stderr, so a
stray `print` cannot corrupt the stream.
"""

import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import autonomy      # noqa: E402  the expectation grammar (parse_spec/verify)
import evidence_hygiene  # noqa: E402  classify() — the only stub detector
import memory        # noqa: E402  provenance levels, recall, render, audit
import verifier      # noqa: E402  confirm() — the only promotion policy

SERVER_NAME = "builderbro"
SERVER_VERSION = "1.0"
# Echoed to the client when it does not ask for a version of its own.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

RAG_SKILL = os.path.join(HERE, ".agents", "skills", "builderbro-rag", "rag_skill.py")
DEFAULT_RESIDENCE = "freebrain-residence"
DEFAULT_MEMORY_STORE = os.path.join(DEFAULT_RESIDENCE, "memory.jsonl")


# ── Result constructors ───────────────────────────────────────────────────────
#
# One convention, inherited from the rag skill: `ok: False` always carries
# `error` (a stable code to branch on) and `reason` (what a human reads). A
# *refusal* is `ok: True` with `refused: True` — refusing is a correct outcome.

def fail(error, reason, **extra):
    out = {"ok": False, "error": error, "reason": reason}
    out.update(extra)
    return out


def _residence(env):
    """Where the residence-relative defaults resolve.

    Anchored at this file's own directory, never at the process cwd. Freebuff
    spawns this server with an inherited cwd (`cwd` is not a key its registry
    schema allows) and a session may be started from a subdirectory of the
    repository, so a relative default quietly audits a log that is not there:
    measured, `evidence_audit` with no `paths` returned `missing_log` from a
    cwd that was not the repository.

    An explicit `DRIVE_RESIDENCE` still wins — that is how the suites point
    themselves at a throwaway home — and a relative one is read as relative to
    this repository, so every tool means the same thing wherever the session was
    started.
    """
    given = (env.get("DRIVE_RESIDENCE") or "").strip()
    if not given:
        return os.path.join(HERE, DEFAULT_RESIDENCE)
    return given if os.path.isabs(given) else os.path.join(HERE, given)


def _memory_store_path(args, env):
    """Explicit `store` wins; otherwise the residence's own memory log."""
    given = (args.get("store") or "").strip()
    if given:
        return given
    return env.get("BRO_MEMORY_STORE") or os.path.join(_residence(env), "memory.jsonl")


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── Tools: verification ───────────────────────────────────────────────────────

def tool_verify_claim(args, env):
    """Independently confirm one claim about a tool's output (A3).

    The claim is re-derived, never read: `expectation_held` is recomputed from
    the expectation and the raw output, and a failed call is never a verified
    observation. `second_output` is the caller's own fresh re-observation.
    """
    tool = (args.get("tool") or "").strip()
    if not tool:
        return fail("bad_request", "`tool` is required — the claim must name what produced the output")
    if "output" not in args:
        return fail("bad_request", "`output` is required — pass the raw output, not a summary of it")

    output = args.get("output") or ""
    spec = autonomy.parse_spec(args.get("expect"))
    second = args.get("second_output")

    rerun = None
    if second is not None:
        rerun = lambda _arg, _sample=second: _sample  # noqa: E731 - the check calls it with `arg`

    verdict = verifier.confirm(
        {"tool": tool, "arg": args.get("arg") or "", "output": output, "expect": spec},
        rerun=rerun,
    )

    out = verdict.as_dict()
    out["ok"] = verdict.ok
    # `ok` is the verifier's own vocabulary: it answers "was this claim
    # confirmed", so a refusal is `ok: False` there. At the transport level a
    # refusal is a *working* tool returning a decision, so it is flagged
    # separately and does not set `isError` (see `call_tool`).
    out["refused"] = not verdict.ok
    out["claim"] = {"tool": tool, "arg": args.get("arg") or "",
                    "expect": autonomy.format_spec(spec)}
    out["spec_strength"] = autonomy.spec_strength(spec) if spec is not None else None
    out["deterministic_tools"] = list(verifier.DETERMINISTIC_TOOLS)
    out["independence"] = "caller-attested" if second is not None else "none"
    if verdict.level == "invariant" and second is not None:
        out["note"] = ("`invariant` required a re-observation. This server supplied the "
                       "sample you passed as `second_output` and tested it against the same "
                       "expectation; it cannot attest that you obtained it independently.")
    if not verdict.ok:
        out["reason"] = "refused by the %s check" % (verdict.refused_by or "verifier",)
    return out


def tool_memory_recall(args, env):
    """Recall from an earlier run, with provenance, and audit the rendered block."""
    goal = (args.get("goal") or "").strip()
    if not goal:
        return fail("bad_request", "`goal` is required — recall is scored against the goal's own words")

    path = _memory_store_path(args, env)
    store = memory.open_store(path)
    records = store.records()

    limit = _as_int(args.get("limit"), 3)
    min_score = _as_float(args.get("min_score"), 0.25)
    value_chars = _as_int(args.get("value_chars"), 400)
    max_chars = _as_int(args.get("max_chars"), 2000)

    items = memory.recall(records, goal, limit=limit, min_score=min_score)
    block = memory.render(items, value_chars=value_chars, max_chars=max_chars)
    check = memory.audit(items, block)
    fact_items = memory.facts(items)
    unverified_items = memory.unverified(items)

    return {
        "ok": True,
        "store": path,
        "available": len(records),
        "recalled": len(items),
        "items": [{"tool": r.get("tool"), "arg": r.get("arg"), "level": r.get("level"),
                   "how_verified": r.get("how_verified"), "score": r.get("score"),
                   "value": r.get("value")} for r in items],
        "facts": [r.get("tool") for r in fact_items],
        "unverified": [r.get("tool") for r in unverified_items],
        "block": block,
        "audit": check,
        "rule": ("Only `invariant` records appear under the facts header. `memory.render` "
                 "builds that section from `memory.facts()` alone, so no argument here can "
                 "move a `volatile` record above the unverified header."),
    }


def tool_memory_record(args, env):
    """Append one record at the level its provenance earns — never higher.

    An `invariant` write must name the independent check that confirmed it. That
    is `memory.promote`'s own rule applied at this seam, so a caller cannot use
    this tool to turn a claim into a fact.
    """
    for key in ("goal", "tool", "arg"):
        if not (args.get(key) or "").strip():
            return fail("bad_request", "`%s` is required" % key, missing=key)
    if "value" not in args:
        return fail("bad_request", "`value` is required (pass \"\" for an empty observation)")

    evidence_level = (args.get("evidence_level") or "declared").strip().lower()
    level = memory.level_for(evidence_level)
    how_verified = (args.get("how_verified") or "").strip()

    if level in memory.PROMOTABLE and not how_verified:
        return fail(
            "provenance",
            "a %r write must name the independent check that confirmed it "
            "(`how_verified`); only %s may be presented as a fact"
            % (level, "/".join(memory.PROMOTABLE)),
            level=level, evidence_level=evidence_level,
        )

    rec = memory.make_record(
        args["goal"], args["tool"], args["arg"], args.get("value") or "",
        level=level, how_verified=how_verified or None,
        step=_as_int(args.get("step"), None) if args.get("step") is not None else None,
    )
    path = _memory_store_path(args, env)
    memory.open_store(path).add(rec)

    return {
        "ok": True,
        "store": path,
        "record": rec,
        "note": (None if level in memory.PROMOTABLE else
                 "recorded as %r — it will render under the unverified header and cannot be "
                 "used as a constraint until an independent check promotes it" % level),
    }


# ── Tools: retrieval ──────────────────────────────────────────────────────────

def tool_rag_ask(args, env):
    """Ask BuilderBro's own corpus, with citations — or be told it cannot answer.

    Delegates to the `builderbro-rag` skill's documented CLI. The default
    generator is `extractive` (offline, deterministic, no keys) because this
    path is meant to be usable from inside a verification step; pass
    `"generator": "live"` to spend a hosted request instead.
    """
    question = (args.get("question") or "").strip()
    if not question:
        return fail("bad_request", "`question` is required")

    if not os.path.isfile(RAG_SKILL):
        return fail("skill_missing", "the builderbro-rag skill is not at %s" % RAG_SKILL)

    argv = [sys.executable, RAG_SKILL, "ask", question]
    if args.get("k") is not None:
        argv += ["-k", str(_as_int(args.get("k"), 5))]
    argv += ["--generator", (args.get("generator") or "extractive").strip()]

    timeout = _as_int(env.get("BRO_MCP_RAG_TIMEOUT_S"), 600)
    try:
        proc = subprocess.run(argv, cwd=HERE, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return fail("timeout", "the rag skill did not finish within %ss" % timeout)

    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return fail("skill_output", "the rag skill did not return JSON on stdout",
                    exit_code=proc.returncode, stderr=proc.stderr[-2000:])
    if not isinstance(payload, dict):
        return fail("skill_output", "the rag skill returned %s, not an object"
                    % type(payload).__name__, exit_code=proc.returncode)

    out = dict(payload)
    out.setdefault("ok", True)
    out["exit_code"] = proc.returncode
    out["skill"] = "builderbro-rag"
    if proc.returncode != 0 and not out.get("reason"):
        out["reason"] = "the rag skill exited %d" % proc.returncode
    return out


# ── Tools: evidence hygiene ───────────────────────────────────────────────────

def tool_evidence_audit(args, env):
    """Count real measurements against fabricated rows in an evidence log.

    The classification is `evidence_hygiene.classify` — the single source of
    truth for what counts as a stub. This tool only aggregates its rulings into
    a machine-readable summary, because the module's own `report()` prints.
    """
    paths = args.get("paths")
    if isinstance(paths, str) or not paths:
        paths = [paths] if paths else [os.path.join(_residence(env), "evidence", "q1-evidence.jsonl")]

    if args.get("exclude_suite_artifacts"):
        default = os.path.join(_residence(env), "evidence", "q1-evidence.jsonl")
        paths = [p for p in paths if p != default]

    logs = []
    missing = []
    for path in paths:
        if not os.path.isfile(path):
            # A caller that asked for a count and silently got none is worse off
            # than one that is told the log was not there.
            missing.append(path)
            logs.append({"path": path, "ok": False, "error": "missing",
                         "reason": "no such evidence log"})
            continue
        rows, broken = evidence_hygiene.load(path)
        kinds = collections.Counter()
        markers = collections.Counter()
        by_model = collections.Counter()
        for rec, _raw, _line in rows:
            kind, why = evidence_hygiene.classify(rec)
            kinds[kind] += 1
            if kind == "artifact":
                markers[why] += 1
            else:
                by_model["%s / %s" % (rec.get("provider"), rec.get("model"))] += 1
        total = len(rows)
        logs.append({
            "path": path,
            "ok": True,
            "rows": total,
            "unparseable": len(broken),
            "measurements": kinds["measurement"],
            "artifacts": kinds["artifact"],
            "measurement_share": (round(100.0 * kinds["measurement"] / total, 1)
                                  if total else None),
            "artifact_markers": dict(markers),
            "by_provider_model": dict(by_model),
        })

    out = {"logs": logs,
           "rule": ("An artifact is a row whose provider/endpoint/model is a known stub. "
                    "Artifact rows are preserved, never deleted — they are the record of a "
                    "bug (`evidence_hygiene.split`).")}
    if missing:
        out.update(fail("missing_log", "no evidence log at %s" % ", ".join(missing),
                        paths=paths))
        return out
    out["ok"] = True
    return out


# ── The tool registry ─────────────────────────────────────────────────────────
#
# Schemas are JSON Schema, which is what MCP `tools/list` carries.

TOOLS = collections.OrderedDict([
    ("verify_claim", {
        "handler": tool_verify_claim,
        "description": (
            "Independently confirm one claim about a tool's output, and return the "
            "confirmation level it actually earned. Re-derives the expectation from the "
            "raw output (a caller's earlier verdict is never read), refuses a failed "
            "call, and refuses an expectation that would accept an empty output or the "
            "canonical failure line. Level is `invariant`, `observed` or `refused` — "
            "`refused` is a correct outcome, and `refused_by` names the check that "
            "refused it. Use this before repeating any factual claim you did not "
            "measure yourself. Pass `second_output` only if you genuinely re-observed "
            "it; the server tests consistency, not independence, and says so."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string",
                         "description": "The tool that produced the output, e.g. `read_file`."},
                "arg": {"type": "string", "description": "What the tool was called with."},
                "output": {"type": "string",
                           "description": "The raw output. Pass it verbatim, not a summary."},
                "expect": {"type": "string",
                           "description": ("The expectation to check, in BuilderBro's grammar: "
                                           "`contains:<needle>`, `absent:<needle>`, `regex:<pattern>` "
                                           "(quote it, doubling backslashes, to have escapes "
                                           "decoded), `lines:<n>`, `nonempty`, `ok`. Omit it to "
                                           "have the verifier refuse for having no expectation.")},
                "second_output": {"type": "string",
                                  "description": ("A fresh re-observation you obtained yourself. "
                                                  "Required for level `invariant`.")},
            },
            "required": ["tool", "output"],
        },
    }),
    ("memory_recall", {
        "handler": tool_memory_recall,
        "description": (
            "Recall what an earlier run established about a goal, with each item's "
            "provenance level attached. `invariant` items are independently confirmed and "
            "may be used as constraints; `observed` and `volatile` items may not, and the "
            "facts/unverified split is structural rather than advisory. Returns the "
            "rendered block and an audit of it. An empty result means nothing relevant was "
            "recorded — it does not mean the goal is impossible."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The goal to score records against."},
                "store": {"type": "string", "description": "Path to the memory JSONL store."},
                "limit": {"type": "integer", "minimum": 1, "description": "Max items (default 3)."},
                "min_score": {"type": "number", "minimum": 0,
                              "description": "Minimum recall score (default 0.25)."},
                "value_chars": {"type": "integer", "minimum": 1},
                "max_chars": {"type": "integer", "minimum": 1},
            },
            "required": ["goal"],
        },
    }),
    ("memory_record", {
        "handler": tool_memory_record,
        "description": (
            "Append one record to the memory store at the level its provenance earns. "
            "`evidence_level` is the verifier's vocabulary: `invariant` (independently "
            "re-observed), `observed` (real, but not re-observed this run), `declared` "
            "(your own claim). An `invariant` write must name the check that confirmed it "
            "in `how_verified` or it is refused — a claim cannot be written into a fact. "
            "Nothing here can promote a `volatile` record."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "tool": {"type": "string"},
                "arg": {"type": "string"},
                "value": {"type": "string", "description": "The observed output."},
                "evidence_level": {"type": "string", "enum": ["invariant", "observed", "declared"],
                                   "default": "declared"},
                "how_verified": {"type": "string",
                                 "description": "The named independent check (required for `invariant`)."},
                "step": {"type": "integer", "minimum": 0},
                "store": {"type": "string"},
            },
            "required": ["goal", "tool", "arg", "value"],
        },
    }),
    ("rag_ask", {
        "handler": tool_rag_ask,
        "description": (
            "Answer a question from this repository's own documents, with citations you can "
            "open, or be told the corpus cannot answer it. A refusal is `ok: true` with "
            "`refused: true` and is correct — do not re-ask a rephrased version hoping for a "
            "different verdict. Every `[n]` marker in `answer` resolves to `sources[n-1]`. "
            "Default generator is offline and extractive."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "description": "Chunks to retrieve."},
                "generator": {"type": "string", "enum": ["extractive", "live", "hallucinate"],
                              "default": "extractive"},
            },
            "required": ["question"],
        },
    }),
    ("evidence_audit", {
        "handler": tool_evidence_audit,
        "description": (
            "Audit evidence logs: how many rows are real measurements versus fabricated "
            "artifacts left by a test or a stub provider. Use it before citing a count from "
            "the ledger — a published total that includes stub rows is a fabricated "
            "measurement, not a rounded one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Evidence logs to audit; defaults to the residence's Q1 log."},
            },
        },
    }),
])


def tool_schemas():
    """`tools/list`'s payload — name, description, inputSchema, and nothing invented."""
    return [{"name": name,
             "description": spec["description"],
             "inputSchema": spec["inputSchema"]}
            for name, spec in TOOLS.items()]


def call_tool(name, args, env=None):
    """`(payload, is_error)`. Never raises: a broken tool is a reported failure.

    `is_error` means *the call malfunctioned* — an unknown tool, a bad argument,
    a skill that returned garbage — and never "the answer was no". Both shapes of
    no are covered: `refused: True` (the rag skill's contract, and `verify_claim`'s
    for a claim it could not confirm) and `ok: False` without it, which is a
    failure with an `error` code. Making a working refusal set `isError` would
    invite a caller to retry a check that already gave its verdict.
    """
    env = env if env is not None else os.environ
    spec = TOOLS.get(name)
    if spec is None:
        return fail("unknown_tool", "no tool named %r" % name,
                    available=list(TOOLS)), True
    if not isinstance(args, dict):
        return fail("bad_request", "`arguments` must be an object"), True
    try:
        payload = spec["handler"](args, env)
    except memory.ProvenanceError as e:
        return fail("provenance", str(e)), True
    except Exception as e:  # a tool that raises must not take the server down
        return fail("tool_error", "%s: %s" % (type(e).__name__, e)), True
    if not isinstance(payload, dict):
        return fail("tool_error", "tool returned %s, not an object" % type(payload).__name__), True
    refused = bool(payload.get("refused"))
    return payload, (not payload.get("ok", True)) and not refused


# ── JSON-RPC / MCP plumbing ───────────────────────────────────────────────────

def _result(mid, payload):
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(message, env=None):
    """One JSON-RPC message in, one out — or `None` for a notification."""
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request: expected an object")
    method = message.get("method")
    mid = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        params = message.get("params") or {}
        # Echo the client's requested revision when it names one: negotiation is
        # the client's to lead, and a server that insists on its own version
        # breaks clients it could have served.
        version = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        payload = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION,
                           "title": "BuilderBro verification stack"},
        }
        return None if is_notification else _result(mid, payload)

    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return None if is_notification else _result(mid, {})

    if method == "tools/list":
        return None if is_notification else _result(mid, {"tools": tool_schemas()})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        payload, is_error = call_tool(name, params.get("arguments") or {}, env=env)
        text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        return None if is_notification else _result(mid, {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": is_error,
        })

    if is_notification:
        return None
    return _error(mid, -32601, "Method not found: %r" % (method,))


def serve(stdin=None, stdout=None, env=None):
    """Read newline-delimited JSON-RPC from stdin until EOF. Returns an exit code.

    MCP's stdio transport frames messages with newlines — one JSON object per
    line — so the reader is deliberately a plain line loop. A malformed line is
    answered with a parse error rather than killing the session, because the cost
    of one dropped frame is a degraded step and the cost of exiting is a client
    whose tools vanish mid-task.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as e:
            reply = _error(None, -32700, "Parse error: %s" % e)
        else:
            try:
                reply = handle(message, env=env)
            except Exception as e:  # belt: `handle` should never raise
                reply = _error(message.get("id") if isinstance(message, dict) else None,
                               -32603, "Internal error: %s: %s" % (type(e).__name__, e))
        if reply is None:
            continue
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse(argv):
    import argparse

    p = argparse.ArgumentParser(
        description="BuilderBro's verification stack over MCP (stdio), or one call from the shell.")
    p.add_argument("--tools", action="store_true",
                   help="print the tool schemas the server advertises, as JSON")
    p.add_argument("--call", metavar="NAME",
                   help="invoke one tool instead of serving, and print its payload")
    p.add_argument("--json", metavar="JSON", default="{}",
                   help="arguments for --call, as a JSON object")
    p.add_argument("--human", action="store_true",
                   help="indent the output of --call for reading")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse(argv if argv is not None else sys.argv[1:])

    if args.tools:
        print(json.dumps({"tools": tool_schemas()}, indent=2, ensure_ascii=False))
        return 0

    if args.call:
        try:
            call_args = json.loads(args.json)
        except ValueError as e:
            print(json.dumps(fail("bad_request", "--json is not valid JSON: %s" % e), indent=2))
            return 2
        payload, is_error = call_tool(args.call, call_args)
        print(json.dumps(payload, indent=2 if args.human else None, ensure_ascii=False,
                         sort_keys=True))
        return 1 if is_error else 0

    return serve()


if __name__ == "__main__":
    sys.exit(main())
