#!/usr/bin/env python3
"""live_freebuff_probe.py — M1's live evaluation of the bridge, on this box.

`bro_bridge_test.py` proves the decisions with the client replaced. This proves
what happens with the client *present* — and it is written to be runnable inside
the operator's live session without touching it. Four stages, each recorded:

1. **The real lock.** Read it, classify it, and report the verdict and owner.
   Every classification runs under a signal-recording ledger and the ledger must
   be empty (`I2`): bro does not kill pids it does not own.
2. **Refuse, then adopt read-only.** `attach` without `adopt` must be
   `E_LOCK_FOREIGN` and must not spawn; `attach adopt:true` must attach to the
   *operator's* conversation read-only, and `ask` with `post:true` into it must
   be refused. Nothing is written into the live record at any point.
3. **One verified round trip, on the real record.** Observe the last settled
   turn, then declare an expectation built from that turn's own tool block and
   let the gate re-observe it against the file on disk. A second expectation that
   no evidence could satisfy runs as the discriminating control: if it is not
   refused, the first one meant nothing.
4. **Post, on a clone.** The live record is copied to a scratch home and the
   session is taken with a stub writer that appends a **real recorded turn** when
   a prompt is written. That exercises the whole post path — watermark before the
   write, the completion rule, the gate, idempotency — against real data shapes
   without a byte reaching the operator's session. The `substituted` field on
   every frame from this stage says so, because a replayed turn is not a live one.
5. **A real spawn, isolated.** A second client is launched on a pty with a
   scratch `HOME` (core and identity symlinked, so no download) and the bridge
   is asked to reach readiness. Whatever happens is the result — including
   `E_READY_TIMEOUT`, which is what the spec's own §19.9 measurement predicts.

Every stage writes to `live-freebuff-transcript.jsonl` and the summary to
`live-freebuff-summary.json`. Nothing here is asserted into the repo's records:
the probe reports, the review interprets.

Run: python3 live_freebuff_probe.py [--stages 1,2,3,4,5] [--cwd DIR]
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bro_bridge as bridge_mod
import bro_session_measure as measure
import live_mcp_probe as mcp_probe

HERE = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPT = os.path.join(HERE, "live-freebuff-transcript.jsonl")
SUMMARY = os.path.join(HERE, "live-freebuff-summary.json")
STATUS = os.path.join(HERE, "live-fresh-mcp-status.json")

# Reader-tool spellings the client is known to use (M0 measured `read_files`).
READER_NAMES = tuple(bridge_mod.READER_TOOLS)

MCP_BRIDGE_TOOLS = tuple("bro-bridge__bridge_" + name for name in (
    "status", "attach", "observe", "ask", "capture", "stop", "shutdown"))
MCP_EXERCISE_PROMPT = (
    "This is a deterministic MCP integration test. Call every bro-bridge MCP tool "
    "exactly once, in this order, and do not call any other bro-bridge tool: "
    "bro-bridge__bridge_status with {}; bro-bridge__bridge_attach with {}; "
    "bro-bridge__bridge_observe with {turn_id:live-mcp-observe,timeout_s:5}; "
    "bro-bridge__bridge_ask with {turn_id:live-mcp-ask,task:Reply with exactly "
    "LIVE-MCP-OK,timeout_s:5}; bro-bridge__bridge_capture with {bytes:1024}; "
    "bro-bridge__bridge_stop with {graceful:true}; and finally "
    "bro-bridge__bridge_shutdown with {}. Continue after every result, including "
    "refusals and errors. Do not edit files. At the end, print each tool name and "
    "whether its result was res or err."
)


class Transcript(object):
    """One JSON line per step, and a summary assembled from the same objects."""

    def __init__(self, path=TRANSCRIPT):
        self.path = path
        self.steps = []
        self.handle = open(path, "w", encoding="utf-8")

    def step(self, stage, name, **fields):
        record = {"stage": stage, "step": name, "at": round(time.time(), 3)}
        record.update(fields)
        self.steps.append(record)
        self.handle.write(json.dumps(record, default=str) + "\n")
        self.handle.flush()
        print("  · %s: %s" % (name, _brief(record)))
        return record

    def close(self):
        self.handle.close()


def _brief(record):
    parts = []
    for key in ("verdict", "state", "code", "ok", "level", "verified", "mode",
                "requested", "answer", "seconds", "rows", "signals", "seeded",
                "bytes", "messages"):
        if key in record:
            value = record[key]
            if isinstance(value, str) and len(value) > 90:
                value = value[:87] + "…"
            parts.append("%s=%s" % (key, value))
    return " ".join(parts) or "(recorded)"


# ── helpers ──────────────────────────────────────────────────────────────────

def client_home():
    return measure.DEFAULT_HOME


def live_record(cwd):
    """The live conversation for `cwd`: `(verdict, chat_dir, detail)`.

    Resolved through the bridge's own rule, not `resolve_project`: this box lists
    `/mnt/sdcard/Download/builderbro` **and** `/sdcard/Download/builderbro` as
    recent projects, so the basename key is genuinely ambiguous and the record's
    own `projectRoot` has to settle it. A probe that resolved it the naive way
    would report "no live conversation" and quietly measure nothing.
    """
    return bridge_mod.resolve_conversation(client_home(), cwd)


def read_messages(chat_dir):
    path = os.path.join(chat_dir, "chat-messages.json")
    status, messages, watermark, detail = measure.read_record(path, settled_only=True)
    return status, messages, watermark, detail


def settled_turns(messages, limit=12):
    """Newest-first settled turns: `[(prompt_id, reply)]`.

    `resolve_last_turn` answers "what did the session last say", which is the
    wrong question for a *verified* round trip: the last turn may well have made
    no reader call, and then there is nothing to re-observe. This walks the
    settled turns back until it finds one that did, and the probe reports which
    one it used and how far back it had to go.
    """
    prompts = []
    for message in messages:
        if not isinstance(message, dict) or message.get("variant") != "user":
            continue
        epoch = measure.id_epoch(message.get("id"))
        if epoch is not None:
            prompts.append((epoch, message.get("id")))
    prompts.sort(reverse=True)
    out = []
    for epoch, message_id in prompts[:limit]:
        verdict, reply, detail = measure.resolve_turn(messages, epoch, quiet_ms=0)
        if verdict == "complete" and reply is not None:
            out.append((message_id, reply, detail))
    return out


def first_replayable(messages, limit=12, base=HERE):
    """The newest settled turn that made a reader call, or the newest settled one."""
    turns = settled_turns(messages, limit=limit)
    for position, (prompt_id, reply, detail) in enumerate(turns):
        if reader_calls(reply, base):
            return prompt_id, reply, detail, position, turns
    if turns:
        prompt_id, reply, detail = turns[0]
        return prompt_id, reply, detail, 0, turns
    return None, None, None, None, turns


def reader_calls(reply, base=HERE):
    """Reader-tool calls in a reply: `[(tool, arg, output)]` with an existing file.

    Measured: this client's reader calls carry **relative** paths
    (`{"paths": [".agents/mcp.json"]}`), so a probe that only accepted absolute
    ones would report "no reader call to verify" about a turn with fifty of them.
    """
    out = []
    for block in (reply or {}).get("tools") or []:
        name = str(block.get("toolName") or "")
        if name.lower() not in READER_NAMES:
            continue
        blob = json.dumps(block.get("input"), default=str)
        for candidate in _paths_in(blob, base):
            out.append((name, candidate, block.get("output")))
    return out


def _paths_in(blob, base=HERE):
    """Paths inside a tool input that name a real file, longest first."""
    found = []
    for chunk in blob.split('"'):
        text = chunk.strip()
        if not text or len(text) < 3 or "\\" in text:
            continue
        candidates = [text] if text.startswith("/") else [text,
                                                          os.path.join(base, text)]
        for candidate in candidates:
            if os.path.isfile(candidate):
                found.append(os.path.realpath(candidate))
    return sorted(set(found), key=len, reverse=True)


def needles_from(output, limit=4):
    """Distinctive substrings of a tool output, to use as a `contains:` needle."""
    if not isinstance(output, str):
        output = json.dumps(output, default=str)
    candidates = []
    for line in output.splitlines():
        text = line.strip().strip('",').strip()
        if len(text) >= 24 and not text.startswith(("{", "[", "}", "]")):
            candidates.append(text[:80])
    seen, out = set(), []
    for text in candidates:
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


# ── stage 1: the real lock ───────────────────────────────────────────────────

def stage_lock(transcript, home, cwd):
    lock_path = os.path.join(home, measure.LOCK_NAME)
    verdict, detail = measure.read_lock(lock_path)
    transcript.step(1, "real_lock_read", lock=lock_path, verdict=verdict,
                    owner=detail.get("owner"), alive=bool((detail.get("proc") or {}).get("alive")),
                    cmdline=(detail.get("proc") or {}).get("cmdline"),
                    reason=detail.get("reason"), extra_keys=detail.get("extra_keys"))

    ledger = bridge_mod.SignalLedger(dry_run=False)
    machine = bridge_mod.LockMachine(home, own={"pid": os.getpid()}, ledger=ledger)
    state, decision = machine.decide()
    transcript.step(1, "lock_decision", state=state, ours=decision.get("ours"),
                    code=decision.get("code"), owner_pid=(decision.get("owner") or {}).get("pid"),
                    reason=decision.get("reason"),
                    events=[e["event"] for e in machine.events],
                    signals=ledger.as_list())
    return {"lock_verdict": verdict, "state": state, "detail": decision,
            "signals": ledger.as_list(), "events": machine.events,
            "owner_pid": (decision.get("owner") or {}).get("pid")}


# ── stage 2: refuse, then adopt ──────────────────────────────────────────────

def stage_attach(transcript, home, cwd, state_dir):
    # `inline=True`: a probe wants one frame in and one frame out. With the
    # worker queue, `observe` answers `evt{accepted}` and the `res` goes to the
    # probe's stdout, which is how the first run of this stage measured nothing.
    bridge = bridge_mod.Bridge(home=home, cwd=cwd, state_dir=state_dir,
                               ready_timeout=5.0, inline=True)
    hello = bridge.handle({"v": 1, "id": "h", "type": "req", "op": "hello",
                           "client": "live-probe/1", "proto_min": 1, "proto_max": 1})
    transcript.step(2, "hello", server=hello.get("server"), v=hello.get("v"),
                    capabilities=hello.get("capabilities"),
                    instance=hello.get("instance"))

    spawned = []
    bridge.spawner = lambda: spawned.append("spawn attempted")
    refused = bridge.handle({"v": 1, "id": "a", "type": "req", "op": "attach"})
    transcript.step(2, "attach_refused", requested="attach without adopt",
                    code=refused.get("code"), type=refused.get("type"),
                    fallback_eligible=refused.get("fallback_eligible"),
                    owner=(refused.get("detail") or {}).get("owner"),
                    spawn_attempted=bool(spawned), signals=bridge.ledger.as_list())

    adopted = bridge.handle({"v": 1, "id": "a2", "type": "req", "op": "attach",
                             "adopt": True})
    transcript.step(2, "attach_adopted", type=adopted.get("type"),
                    state=adopted.get("state"), session_id=adopted.get("session_id"),
                    project=adopted.get("project"), read_only=adopted.get("read_only"),
                    owner=adopted.get("owner"), axis=adopted.get("axis"),
                    warnings=adopted.get("warnings"),
                    signals=bridge.ledger.as_list())

    posted = bridge.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                            "turn_id": "live-probe-post", "task": "probe: must not post",
                            "post": True})
    transcript.step(2, "post_into_live_session_refused", code=posted.get("code"),
                    asked=(posted.get("detail") or {}).get("asked"),
                    instead=(posted.get("detail") or {}).get("instead"),
                    signals=bridge.ledger.as_list())
    return bridge, {"refused": refused, "adopted": adopted, "posted": posted,
                    "spawn_attempted": bool(spawned)}


# ── stage 3: one verified round trip on the real record ──────────────────────

def stage_observe(transcript, bridge, chat_dir, cwd=HERE):
    status, messages, watermark, detail = read_messages(chat_dir)
    transcript.step(3, "live_record_read", status=status, messages=len(messages),
                    settled=watermark.message_count if watermark else None,
                    settled_bytes=watermark.prefix_bytes if watermark else None,
                    in_flight=detail.get("in_flight"))
    epoch, prompt_id, verdict, reply, rdetail = bridge_mod.resolve_last_turn(messages)
    if verdict != "complete" or reply is None:
        transcript.step(3, "observe_unavailable", verdict=verdict, reason=rdetail.get("reason"))
        return {"verdict": verdict, "reply": None}

    tools = [b.get("toolName") for b in reply.get("tools") or []]
    transcript.step(3, "last_settled_turn", verdict=verdict, answer=reply.get("text"),
                    answer_chars=len(reply.get("text") or ""), tools=tools,
                    tool_count=len(tools), signal=rdetail.get("signal"),
                    in_flight=rdetail.get("in_flight"),
                    newer_prompt_pending=rdetail.get("newer_prompt_pending"),
                    reply_id=reply.get("id"))

    out = {"verdict": verdict, "reply_chars": len(reply.get("text") or ""),
           "tools": tools, "attempts": [], "control": None,
           "claim_turn_reply_id": reply.get("id")}
    calls = reader_calls(reply, cwd)
    if not calls:
        # The last turn answered in prose and read nothing, so the gate has no
        # observation to re-observe. Walk back to the newest settled turn that
        # did read something, and say how far back that was — the alternative is
        # to report "nothing to verify" about a session that has plenty.
        fallback_id, fallback_reply, _fd, position, turns = first_replayable(
            messages, base=cwd)
        if fallback_id is None or fallback_reply is None or not reader_calls(
                fallback_reply, cwd):
            transcript.step(3, "no_reader_call_to_verify", note=(
                "no settled turn in this record made a reader-tool call, so there "
                "is nothing the gate can re-observe; the observe itself is the result"),
                settled_turns=len(turns))
            return out
        calls = reader_calls(fallback_reply, cwd)
        out["claim_turn_reply_id"] = fallback_reply.get("id")
        transcript.step(3, "claim_turn_walked_back", turns_back=position,
                        reply_id=fallback_reply.get("id"),
                        note=("the last settled turn read nothing; the claim uses the "
                              "newest settled turn that did"))

    tool, arg, output = calls[0]
    transcript.step(3, "claim_source", tool=tool, arg=arg,
                    output_chars=len(output or ""), output_head=(output or "")[:200])
    for attempt, needle in enumerate(needles_from(output), start=1):
        frame = bridge.handle({"v": 1, "id": "o%d" % attempt, "type": "req",
                               "op": "observe", "turn_id": "live-probe-observe-%d" % attempt,
                               "expect": {"tool": tool, "arg": arg,
                                          "check": "contains:%s" % needle}})
        evidence = frame.get("evidence") or {}
        record = {"attempt": attempt, "needle": needle[:80],
                  "type": frame.get("type"), "code": frame.get("code"),
                  "verified": evidence.get("verified"), "level": evidence.get("level"),
                  "ok": evidence.get("ok"), "signal": evidence.get("signal"),
                  "tool_names": evidence.get("tool_names"),
                  "how_verified": evidence.get("how_verified"),
                  "checks": evidence.get("checks"),
                  "bounce_note": (frame.get("detail") or {}).get("bounce_note")
                  or evidence.get("bounce_note")}
        out["attempts"].append(record)
        transcript.step(3, "observe_verified" if frame.get("type") == "res"
                        else "observe_refused", **record)
        if frame.get("type") == "res":
            break

    # The discriminating control: a needle that cannot be anywhere on this disk.
    control = "%s-not-on-any-disk" % os.urandom(8).hex()
    frame = bridge.handle({"v": 1, "id": "ctl", "type": "req", "op": "observe",
                           "turn_id": "live-probe-control",
                           "expect": {"tool": tool, "arg": arg,
                                      "check": "contains:%s" % control}})
    out["control"] = {"needle": control, "type": frame.get("type"),
                      "code": frame.get("code"),
                      "bounce_note": (frame.get("detail") or {}).get("bounce_note")}
    transcript.step(3, "control_unsatisfiable_expectation", **out["control"])
    return out


# ── stage 4: post, on a clone ────────────────────────────────────────────────

class StubChild(object):
    """A client replaced by a recorder, so a *real* turn can be replayed.

    `on_write` receives the framed prompt; `replay` is a real assistant message
    from the live record, re-stamped with a fresh epoch so it is genuinely the
    turn after this prompt.
    """

    def __init__(self, pid, chat_dir, replay, append):
        # The guard that this stage needed the first time it ran: `replay` must be
        # a **record message**, not a reply assembled by the reader. A reply's
        # `blocks` is a list of block *types*, and writing one into the record
        # produced a message with no `variant` — which the completion rule then
        # could never match, so the turn waited out its stall budget instead of
        # saying what was wrong.
        if not isinstance(replay, dict) or not replay.get("variant"):
            raise ValueError(
                "replay must be a raw record message with a `variant`, got %s"
                % type(replay).__name__)
        self.proc = type("Proc", (), {"pid": pid})()
        self.pgid = pid
        self.pane = "[stub pane: this stage has no client]\n"
        self.pane_bytes = self.pane.encode("utf-8")
        self.writes = []
        self.replayed = []
        self.terminated = False
        self.chat_dir = chat_dir
        self.replay = replay
        self._append = append

    def pump(self, _seconds):
        return self.pane_bytes

    def write(self, text):
        self.writes.append(text)
        message = json.loads(json.dumps(self.replay))
        message["id"] = "ai-%d" % int(time.time() * 1000)
        message["isComplete"] = True
        self._append(self.chat_dir, message)
        self.replayed.append(message.get("id"))

    def alive(self):
        return not self.terminated

    def pane_text(self):
        return self.pane

    def terminate(self, ledger):
        self.terminated = True
        ledger.killpg(self.proc.pid, 15)


def record_fingerprint(chat_dir):
    """What a conversation *is*, without reading all of it: size, count, hash.

    Used on the live record around stage 4 so "the live session was not written"
    is a measurement: a duplicate message or a changed byte count would show.
    """
    if not chat_dir:
        return {"chat_dir": None}
    import hashlib
    path = os.path.join(chat_dir, "chat-messages.json")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return {"chat_dir": chat_dir, "error": str(exc)}
    try:
        messages = json.loads(raw.decode("utf-8", "replace"))
        count = len(messages)
        ids = [m.get("id") for m in messages if isinstance(m, dict)]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
    except ValueError as exc:
        count, duplicates = None, ["unparseable: %s" % exc]
    return {"chat_dir": chat_dir, "bytes": len(raw), "messages": count,
            "sha256": hashlib.sha256(raw).hexdigest()[:16],
            "duplicate_ids": duplicates,
            "mtime": round(os.path.getmtime(path), 3)}


def stage_clone(transcript, home, cwd, base_state_dir, replay, live_owner=None):
    """`replay` is the raw record message to append — see `StubChild`."""
    scratch = tempfile.mkdtemp(prefix="bro-live-clone-")
    clone_home = os.path.join(scratch, ".config", "manicode")
    os.makedirs(clone_home)
    key = os.path.basename(os.path.realpath(cwd))
    src_project = os.path.join(home, "projects", key)
    dst_project = os.path.join(clone_home, "projects", key)
    os.makedirs(os.path.dirname(dst_project), exist_ok=True)
    shutil.copytree(src_project, dst_project)

    # The live record, watched but never written: its size and message count
    # before and after this stage are recorded, because a clone stage that writes
    # into the operator's session is not a clone stage.
    live_verdict, live_dir, live_detail = live_record(cwd)
    live_before = record_fingerprint(live_dir)
    transcript.step(4, "live_record_before", **live_before)

    def append(directory, message):
        path = os.path.join(directory, "chat-messages.json")
        with open(path, "r", encoding="utf-8") as handle:
            messages = json.load(handle)
        messages.append(message)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(messages, handle)

    # The clone's lock borrows the *live* instance's identity. That is the only
    # way `decide()` can reach the ATTACHED branch here: a fabricated pid is not
    # alive in this process's `/proc`, so it would be classified `owner_dead` and
    # the clone would never own its own session — a stub pid cannot model
    # ownership, and pretending otherwise would make this stage measure nothing.
    owner = live_owner or {}
    pid = owner.get("pid") or os.getpid()
    instance = owner.get("instanceId") or "live-probe-clone"
    with open(os.path.join(clone_home, measure.LOCK_NAME), "w",
              encoding="utf-8") as handle:
        json.dump({"pid": pid, "instanceId": instance}, handle)

    state_dir = os.path.join(scratch, "bro")
    bridge = bridge_mod.Bridge(home=clone_home, cwd=cwd, state_dir=state_dir,
                               ledger=bridge_mod.SignalLedger(dry_run=True),
                               inline=True, quiet_ms=0)
    bridge.handle({"v": 1, "id": "h", "type": "req", "op": "hello"})
    bridge.lock_machine.own["pid"] = pid
    bridge.lock_machine.own["instanceId"] = instance
    attached = bridge.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                              "spawn": False})

    # The stub writes where the *attached session* reads — never a path resolved
    # under the client's home. This is the guard, and it is checked rather than
    # commented: `inside()` against the clone home, before the writer exists.
    clone_chat_dir = bridge.session.chat_dir if bridge.session is not None else None
    if not bridge_mod.inside(clone_chat_dir, clone_home):
        transcript.step(4, "clone_refused_to_write", chat_dir=clone_chat_dir,
                        clone_home=clone_home,
                        note="the writer's target is outside the clone home")
        shutil.rmtree(scratch, ignore_errors=True)
        return {"error": "writer target outside the clone home",
                "chat_dir": clone_chat_dir}
    child = StubChild(pid, clone_chat_dir, replay, append)
    bridge.session.child = child
    transcript.step(4, "clone_writer_guard", chat_dir=clone_chat_dir,
                    clone_home=clone_home, inside_clone_home=True,
                    live_chat_dir=live_dir,
                    note="the writer's target is the session the reader watches, "
                         "under the clone home")
    transcript.step(4, "clone_attached", type=attached.get("type"),
                    code=attached.get("code"), state=attached.get("state"),
                    read_only=attached.get("read_only"),
                    session_id=attached.get("session_id"),
                    owner=attached.get("owner"), chat_dir=clone_chat_dir,
                    warnings=attached.get("warnings"),
                    substituted="record cloned from the live session; lock identity "
                                "borrowed from the live owner")
    if attached.get("type") == "err":
        transcript.step(4, "clone_attach_failed", code=attached.get("code"),
                        detail=attached.get("detail"))
        shutil.rmtree(scratch, ignore_errors=True)
        return {"error": attached.get("code"), "attached": attached}

    expect = None
    calls = reader_calls(replay, cwd)
    if calls:
        tool, arg, output = calls[0]
        needle = (needles_from(output) or ["x"])[0]
        expect = {"tool": tool, "arg": arg, "check": "contains:%s" % needle}

    posted = bridge.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                            "turn_id": "clone-1", "task": "probe: replayed turn",
                            "expect": expect} if expect else
                           {"v": 1, "id": "k", "type": "req", "op": "ask",
                            "turn_id": "clone-1", "task": "probe: replayed turn"})
    transcript.step(4, "clone_post_turn", replayed_ids=list(child.replayed),
                    type=posted.get("type"),
                    code=posted.get("code"), mode=(posted.get("evidence") or {}).get("mode"),
                    verified=(posted.get("evidence") or {}).get("verified"),
                    level=(posted.get("evidence") or {}).get("level"),
                    ok=(posted.get("evidence") or {}).get("ok"),
                    answer=(posted.get("reply") or {}).get("text"),
                    answer_chars=len(((posted.get("reply") or {}).get("text")) or ""),
                    timings=posted.get("timings"), state=posted.get("state"),
                    prompts_written=len(child.writes),
                    framed=child.writes[0][:120] if child.writes else None,
                    expect=expect, substituted="reply replayed from the live record")

    if posted.get("type") == "err":
        transcript.step(4, "clone_post_refused", code=posted.get("code"),
                        detail=posted.get("detail"))

    retry = bridge.handle({"v": 1, "id": "k2", "type": "req", "op": "ask",
                           "turn_id": "clone-1", "task": "probe: replayed turn"})
    transcript.step(4, "clone_retry_idempotent", type=retry.get("type"),
                    same_reply=((retry.get("reply") or {}).get("text")
                                == (posted.get("reply") or {}).get("text")),
                    prompts_written=len(child.writes))

    if calls:
        tool, arg, _output = calls[0]
        control = "%s-not-there" % os.urandom(6).hex()
        refused = bridge.handle({"v": 1, "id": "k3", "type": "req", "op": "ask",
                                 "turn_id": "clone-refuse-1",
                                 "task": "probe: replayed turn",
                                 "expect": {"tool": tool, "arg": arg,
                                            "check": "contains:%s" % control}})
        transcript.step(4, "clone_refusal", type=refused.get("type"),
                        code=refused.get("code"),
                        verdict=(refused.get("detail") or {}).get("verdict"),
                        bounce_note=(refused.get("detail") or {}).get("bounce_note"),
                        fallback_eligible=refused.get("fallback_eligible"),
                        substituted="reply replayed from the live record")

    status = bridge.handle({"v": 1, "id": "s", "type": "req", "op": "status"})
    transcript.step(4, "clone_status", watermark=status.get("watermark"),
                    session=(status.get("session") or {}).get("state"),
                    resyncs=status.get("resyncs"), queue_depth=status.get("queue_depth"))
    bridge.release()
    live_after = record_fingerprint(live_dir)
    transcript.step(4, "live_record_after", **live_after)
    out = {"chat_dir": clone_chat_dir, "live_chat_dir": live_dir,
           "attached": attached, "posted": posted, "live_before": live_before,
           "live_after": live_after, "retry": retry, "prompts": len(child.writes),
           "scratch": scratch}
    shutil.rmtree(scratch, ignore_errors=True)
    return out


# ── stage 5: a real spawn, isolated ──────────────────────────────────────────

def seed_home(home, scratch_home):
    """Copy the core and link its data so a second instance stays isolated.

    The executable itself must be copied, not symlinked. The client resolves its
    configuration beside the real executable path, so a symlink back to
    ``~/.config/manicode/freebuff`` silently reintroduces the operator's lock and
    chats even when ``HOME`` points at the scratch directory. That is exactly
    what the first isolated run measured: paint, no conversation, timeout.
    """
    os.makedirs(scratch_home, exist_ok=True)
    seeded = []
    for name in ("freebuff", "tree-sitter.wasm", "rg", "credentials.json",
                 "settings.json", "freebuff-metadata.json", "analytics-id.json"):
        source = os.path.join(home, name)
        if not os.path.exists(source):
            continue
        destination = os.path.join(scratch_home, name)
        try:
            if name == "freebuff":
                shutil.copy2(source, destination)
            else:
                os.symlink(source, destination)
            seeded.append(name)
        except OSError:
            pass
    return seeded


def exercise_mcp_tools(transcript, bridge, child, timeout=300.0):
    """Ask the fresh client to call every adapter tool, then verify its record."""
    child.write(MCP_EXERCISE_PROMPT + "\r")
    started = time.time()
    last_progress = started
    last_count = -1
    result = None
    while time.time() - started < timeout:
        child.pump(0.5)
        session = bridge.session
        status, _messages, _watermark, detail = session.read(settled_only=True)
        _epoch, prompt_id, verdict, reply, turn_detail = bridge_mod.resolve_last_turn(
            session.seen)
        tools = list((reply or {}).get("tools") or [])
        names = [str(tool.get("toolName") or "") for tool in tools]
        bridge_names = [name for name in names
                        if name.startswith("bro-bridge__bridge_")]
        count = len(set(bridge_names))
        if count != last_count:
            transcript.step(5, "mcp_progress", prompt_id=prompt_id,
                            record_status=status, verdict=verdict,
                            bridge_tools=bridge_names, detail=detail)
            last_count = count
            last_progress = time.time()
        if set(MCP_BRIDGE_TOOLS).issubset(set(names)):
            result = {
                "prompt_id": prompt_id,
                "reply_id": (reply or {}).get("id"),
                "tools": [{"name": tool.get("toolName"),
                           "input": tool.get("input"),
                           "output_chars": len(str(tool.get("output") or "")),
                           "output": str(tool.get("output") or "")[:1000]}
                          for tool in tools
                          if str(tool.get("toolName") or "").startswith(
                              "bro-bridge__bridge_")],
                "turn": turn_detail,
                "seconds": round(time.time() - started, 1),
            }
            break
        if not child.alive():
            break
        if time.time() - last_progress > 90:
            transcript.step(5, "mcp_stalled", prompt_id=prompt_id,
                            verdict=verdict, bridge_tools=bridge_names)
            break
    transcript.step(5, "mcp_result",
                    ok=result is not None,
                    expected=list(MCP_BRIDGE_TOOLS),
                    observed=(result or {}).get("tools"),
                    prompt_id=(result or {}).get("prompt_id"),
                    reply_id=(result or {}).get("reply_id"),
                    seconds=(result or {}).get("seconds", round(time.time() - started, 1)))
    return result


def stage_spawn(transcript, home, cwd, ready_timeout, ask=False, exercise_mcp=False):
    scratch = tempfile.mkdtemp(prefix="bro-live-spawn-")
    scratch_home = os.path.join(scratch, ".config", "manicode")
    os.makedirs(scratch_home)
    seeded = seed_home(home, scratch_home)
    real_lock = os.path.join(home, measure.LOCK_NAME)
    with open(real_lock, "rb") as handle:
        before = handle.read()
    transcript.step(5, "spawn_isolation", scratch_home=scratch_home, seeded=seeded,
                    real_lock_bytes=len(before))

    child = None
    bridge = bridge_mod.Bridge(home=scratch_home, cwd=cwd,
                               state_dir=os.path.join(scratch, "bro"),
                               ledger=bridge_mod.SignalLedger(dry_run=False),
                               inline=True, ready_timeout=ready_timeout,
                               quiet_ms=0)

    def spawner():
        nonlocal child
        child = bridge_mod.PtyChild([bridge.client, "--cwd", cwd, "--trust-agents"],
                                    cwd, env={"HOME": scratch})
        return child

    bridge.spawner = spawner
    bridge.handle({"v": 1, "id": "h", "type": "req", "op": "hello"})
    started = time.time()
    attached = bridge.handle({"v": 1, "id": "a", "type": "req", "op": "attach"})
    seconds = round(time.time() - started, 1)
    pane = child.pane_text() if child is not None else ""
    transcript.step(5, "spawn_result", type=attached.get("type"),
                    code=attached.get("code"), state=attached.get("state"),
                    seconds=seconds, ready_timeout=ready_timeout,
                    painted=bool(pane), pane_bytes=len(pane),
                    pane_tail=pane[-400:],
                    pane_plain_tail=mcp_probe.plain(pane)[-2000:],
                    detail=attached.get("detail"),
                    signals=bridge.ledger.as_list())
    with open(real_lock, "rb") as handle:
        after = handle.read()
    transcript.step(5, "operator_lock_untouched", unchanged=(before == after))

    out = {"attached": attached, "seconds": seconds, "pane_bytes": len(pane),
           "unchanged": before == after, "signals": bridge.ledger.as_list(),
           "ask": None, "mcp": None}
    if attached.get("type") == "res" and exercise_mcp:
        out["mcp"] = exercise_mcp_tools(transcript, bridge, child)
    if attached.get("type") == "res" and ask:
        turn = bridge.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                              "turn_id": "spawn-1",
                              "task": "Reply with exactly this token and nothing "
                                      "else: BRO-BRIDGE-LIVE-OK",
                              "timeout_s": 180})
        evidence = turn.get("evidence") or {}
        out["ask"] = {"type": turn.get("type"), "code": turn.get("code"),
                      "answer": ((turn.get("reply") or {}).get("text") or "")[:400],
                      "mode": evidence.get("mode"), "verified": evidence.get("verified"),
                      "answer_chars": len(((turn.get("reply") or {}).get("text")) or ""),
                      "timings": turn.get("timings")}
        transcript.step(5, "spawn_ask", **out["ask"])
    if child is not None:
        bridge.release()
    shutil.rmtree(scratch, ignore_errors=True)
    return out


# ── driver ───────────────────────────────────────────────────────────────────

def mcp_completion(summary):
    """Strict terminal verdict for the seven-call fresh-client exercise."""
    spawn = summary.get("spawn") or {}
    attached = spawn.get("attached") or {}
    exercise = spawn.get("mcp") or {}
    observed = [str(tool.get("name")) for tool in exercise.get("tools") or []
                if isinstance(tool, dict)]
    expected = list(MCP_BRIDGE_TOOLS)
    missing = [name for name in expected if name not in observed]
    unexpected = [name for name in observed if name not in expected]
    duplicates = sorted(name for name in set(observed) if observed.count(name) > 1)
    reasons = []
    if attached.get("type") != "res":
        reasons.append("fresh client did not attach: %s" %
                       (attached.get("code") or attached.get("type") or "unknown"))
    if spawn.get("unchanged") is not True:
        reasons.append("the operator's lock changed during isolated verification")
    if not exercise:
        reasons.append("no completed MCP exercise was recorded")
    if missing:
        reasons.append("missing tools: %s" % ", ".join(missing))
    if unexpected:
        reasons.append("unexpected tools: %s" % ", ".join(unexpected))
    if duplicates:
        reasons.append("tools called more than once: %s" % ", ".join(duplicates))
    if observed != expected:
        reasons.append("tool order differs from the required sequence")
    return {"state": "passed" if not reasons else "failed",
            "ok": not reasons, "expected": expected, "observed": observed,
            "missing": missing, "unexpected": unexpected, "duplicates": duplicates,
            "reasons": reasons,
            "prompt_id": exercise.get("prompt_id"),
            "reply_id": exercise.get("reply_id"),
            "seconds": exercise.get("seconds")}


def write_status(payload):
    """Atomically publish one status document; readers never see half JSON."""
    temporary = STATUS + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, STATUS)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cwd", default=HERE, help="the live project to attach to")
    parser.add_argument("--stages", default="1,2,3,4,5",
                        help="comma-separated stages to run")
    parser.add_argument("--ready-timeout", type=float, default=75.0)
    parser.add_argument("--spawn-ask", action="store_true",
                        help="if the isolated spawn reaches ready, ask it one token question")
    parser.add_argument("--exercise-mcp", action="store_true",
                        help="ask the isolated fresh client to call every bro-bridge MCP tool")
    parser.add_argument("--no-spawn", action="store_true")
    args = parser.parse_args(argv)

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    if args.no_spawn and "5" in stages:
        stages.remove("5")
    marker_armed = "5" in stages
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if marker_armed:
        write_status({
            "state": "running", "ok": None, "marker_version": 1,
            "started_at": started_at, "pid": os.getpid(),
            "expected": list(MCP_BRIDGE_TOOLS), "observed": [],
            "stages_run": stages, "exercise_mcp": bool(args.exercise_mcp),
            "status_file": STATUS,
        })
    home = client_home()
    cwd = os.path.realpath(args.cwd)
    state_dir = tempfile.mkdtemp(prefix="bro-live-state-")
    transcript = Transcript()
    summary = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "cwd": cwd, "home": home, "pid": os.getpid(),
               "stages_run": stages, "client_home_exists": os.path.isdir(home)}
    print("live probe: cwd=%s home=%s stages=%s" % (cwd, home, ",".join(stages)))
    bridge = None
    try:
        if "1" in stages:
            summary["lock"] = stage_lock(transcript, home, cwd)
        if "2" in stages:
            bridge, attach = stage_attach(transcript, home, cwd, state_dir)
            summary["attach"] = attach
        if "3" in stages:
            if bridge is None:
                bridge, _attach = stage_attach(transcript, home, cwd, state_dir)
            verdict, chat_dir, detail = live_record(cwd)
            summary["live_project"] = {
                "verdict": verdict, "project": detail.get("project_key"),
                "collisions": detail.get("collisions"), "chat_dir": chat_dir,
                "reason": detail.get("reason"),
                "disambiguated_by": detail.get("disambiguated_by")}
            transcript.step(3, "live_project_resolved", verdict=verdict,
                            project=detail.get("project_key"), chat_dir=chat_dir,
                            collisions=detail.get("collisions"),
                            disambiguated_by=detail.get("disambiguated_by"),
                            reason=detail.get("reason"))
            if chat_dir:
                summary["observe"] = stage_observe(transcript, bridge, chat_dir, cwd)
                _status, messages, _wm, _d = read_messages(chat_dir)
                prompt_id, reply, _rd, position, turns = first_replayable(messages, base=cwd)
                summary["replay_source"] = (
                    {"available": reply is not None, "prompt_id": prompt_id,
                     "settled_turns": len(turns), "turns_back": position,
                     "reply_id": (reply or {}).get("id"),
                     "tools": [b.get("toolName") for b in (reply or {}).get("tools") or []],
                     "reader_calls": len(reader_calls(reply)) if reply else 0,
                     "chars": len((reply or {}).get("text") or "")})
        if "4" in stages:
            src = summary.get("replay_source") or {}
            if not src.get("available"):
                transcript.step(4, "clone_skipped", reason="no settled live turn to replay")
            else:
                try:
                    _status, messages, _wm, _d = read_messages(
                        summary["live_project"]["chat_dir"])
                    _prompt, reply, _rd, _position, _turns = first_replayable(messages,
                                                                             base=cwd)
                    owner = ((summary.get("lock") or {}).get("detail") or {}).get("owner")
                    # The raw message, looked up by id: what goes back into a
                    # record has to come out of one.
                    raw = [m for m in messages if isinstance(m, dict)
                           and m.get("id") == (reply or {}).get("id")]
                    if not raw:
                        transcript.step(4, "clone_skipped",
                                        reason="the replay message is not in the record")
                    else:
                        summary["clone"] = stage_clone(transcript, home, cwd, state_dir,
                                                      raw[0], live_owner=owner)
                except Exception as exc:      # a stage that breaks is data, not a stop
                    import traceback
                    trace = traceback.format_exc()
                    transcript.step(4, "clone_failed", error="%s: %s"
                                    % (type(exc).__name__, exc),
                                    trace=trace[-1200:])
                    summary["clone"] = {"error": "%s: %s" % (type(exc).__name__, exc),
                                        "trace": trace[-1200:]}
                    print(trace[-1200:])
        if "5" in stages:
            try:
                summary["spawn"] = stage_spawn(transcript, home, cwd, args.ready_timeout,
                                               ask=args.spawn_ask,
                                               exercise_mcp=args.exercise_mcp)
            except Exception as exc:
                summary["spawn"] = {"probe_error": "%s: %s" %
                                                 (type(exc).__name__, exc)}
                transcript.step(5, "spawn_probe_failed", error=summary["spawn"]["probe_error"])
    finally:
        if bridge is not None:
            try:
                summary["release_signals"] = bridge.release()
            except Exception as exc:
                summary["release_signals_error"] = str(exc)
        transcript.close()
        shutil.rmtree(state_dir, ignore_errors=True)

    summary["steps"] = len(transcript.steps)
    with open(SUMMARY, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=str, sort_keys=True)
        handle.write("\n")
    print("\ntranscript: %s (%d steps)" % (TRANSCRIPT, len(transcript.steps)))
    print("summary:    %s" % SUMMARY)
    if marker_armed:
        completion = mcp_completion(summary)
        completion.update({
            "marker_version": 1, "started_at": started_at,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pid": os.getpid(), "exercise_mcp": bool(args.exercise_mcp),
            "summary": SUMMARY, "transcript": TRANSCRIPT,
        })
        write_status(completion)
        print("completion: %s — %s" %
              (completion["state"].upper(),
               completion["reasons"][0] if completion["reasons"]
               else "all seven tools called once in order"))
        print("marker:     %s" % STATUS)
    return 0 if not marker_armed or completion["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
