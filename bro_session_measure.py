#!/usr/bin/env python3
"""bro_session_measure.py — M0 of `bro-freebuff-brain-spec.md`: measure the session
before anything is designed around it.

WHY THIS EXISTS
---------------
The spec's own rule (`§14`) is that no design decision may assume an unmeasured
channel. Two of its channels were measured on 2026-09-21 against client
`0.0.180`; a third (the lock's real behaviour with a live foreign owner) was
explicitly left open, and `§19.9` lists seven questions M0 must answer before
`§19` is frozen.

The problem with leaving it at that is drift. The client auto-updates (measured:
the npm wrapper is `0.0.154` while the core moved `0.0.180` → `0.0.186` between
the spec being written and this file), and the on-disk contract is undocumented.
A reader written from `§18.7` today would be wrong tomorrow and would fail
*silently* — it would find zero tool calls, report `cannot_verify` on every turn,
and look like a careful verifier rather than a broken reader.

So this file is the instrument that makes the contract checkable: it reads the
live record, reports what shape it actually has, and refuses to guess. It is the
thing M1 is allowed to depend on, and the thing that tells you when the spec's
`§18.7` has gone stale.

WHAT IT MEASURES
----------------
1. **Lock / PROC axis** (`§19.2`). Reads the global instance lock and resolves the
   owning pid against `/proc` — alive, dead, or *reused by an unrelated process* —
   plus the pid's start-time, so a recycled pid cannot pass as our session.
   Read-only by construction: this module never calls `kill`, and
   `test_it_never_signals` asserts that by recording every `os.kill` call.
2. **Project resolution** (`§18.7` project-key hazard). Recent projects on this
   box list both `/mnt/sdcard/Download/builderbro` and
   `/sdcard/Download/builderbro`, and the project directory is keyed by basename —
   so two different paths can land in one directory. This resolves the key and
   reports the collision instead of reading another checkout's conversation.
3. **Active conversation** (`§18.7`). The newest directory is *wrong*: the
   per-launch log dirs sit alongside the conversation, and the newest is recently
   ~2 days newer than the live chat. The shape test (a conversation dir contains
   `chat-meta.json`) is what resolves it, and this reports both so the defect is
   visible rather than assumed away.
4. **`chat-messages.json` as a watermark channel.** A compact single-line JSON
   array rewritten *whole* (measured: 0 newlines in 32.8 MB). So there is no line
   to tail; the watermark is `(message_count, prefix_bytes, prefix_hash)`, the
   delta is parsed out of the growing suffix, and a prefix whose hash moved is
   reported as a `resync` — a visible cost, not a silent misread. A file caught
   mid-rewrite is reported as `torn`, which is a real hazard for a whole-file
   rewrite and is the reason this reader has a retry rather than a parse.
5. **Turn completion** (`§18.6`). Which record shape actually marks a finished
   assistant turn. `§18.7` proposed a quiet-period heuristic; the live record
   carries `isComplete`, and this module reports which signal fired.

WHAT IT CANNOT TELL YOU
-----------------------
- **Why** the client did anything. It reads files; it does not read the client.
- Anything about a session it did not measure. `--report` is a snapshot of *now*.
- Whether a *reply* is honest. That is the verifier's job, on the other side of
  the seam (`§C4`); this module only establishes that a reply was read intact.
- Whether a **second launch** parks, exits or errors (`§19.9` Q3). That needs a
  spawn and is measured by `--spawn-probe`, which runs the client under a scratch
  `HOME` so it cannot contest the operator's live lock. Without that flag this
  module spawns nothing at all.

Run: python3 bro_session_measure.py                     # measure now, print it
     python3 bro_session_measure.py --report /tmp/m0.json
     python3 bro_session_measure.py --watch 120         # watch the live record grow
     python3 bro_session_measure.py --spawn-probe       # §19.9 Q1–Q5, isolated HOME
Exit: 0 measured · 2 inconclusive (the channel could not be read, and says so)
"""

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# The client's own home. `HOME` is honoured by the client (measured), which is
# what makes `--spawn-probe` safe: a scratch HOME keeps a second instance away
# from the operator's lock. `FREEBUFF_HOME` overrides for tests.
DEFAULT_HOME = os.path.join(os.path.expanduser("~"), ".config", "manicode")

# The lock is global, not per-project (measured, `§19.1`).
LOCK_NAME = "freebuff-instance-owner.json"

# A conversation directory contains this; a per-launch log directory does not.
# Measured: the two sit side by side in the same `chats/` folder, and the newest
# by name can be days newer than the live conversation.
CONVERSATION_MARKER = "chat-meta.json"

# The answer is in `blocks[]`, not in `content` (measured: `content` is empty for
# every assistant message on 0.0.186). `reasoning` is separated by structure.
ANSWER_TEXTTYPE = "text"
REASONING_TEXTTYPE = "reasoning"

# Tool blocks are `tool-call` in `§18.7` (0.0.180) and `tool` as measured on
# 0.0.186. Both are accepted and *which one was seen* is reported, because a
# reader that knows only one of them fails silently and looks like a verifier.
TOOL_BLOCK_TYPES = ("tool", "tool-call")

# `id` embeds epoch milliseconds: `user-1789979040257`, `ai-1789979041048-<hash>`.
# `timestamp` is `"05:52 AM"` — display only, never an ordering key (`§18.7`).
_ID = re.compile(r"^(user|ai|divider|system)-(\d{10,})")

# `§18.6` rule 4, used only when the record carries no completeness flag.
DEFAULT_QUIET_MS = 1200

# The client's `log.jsonl` states its own session state; `§19.2`'s SESS axis.
# Note these lines are the *client's*, and a spec document quoted into a session
# also contains them — callers should prefer the tail of the file.
SESS_OVER = "Freebuff session over"
SESS_RECONNECTED = "Reconnection detected"

# The client names its own core in `cmdline`. Identity must not assume a path or
# version (`§19.6`: the wrapper and the core version independently).
CLIENT_MARKER = "freebuff"

PROC = "/proc"


# ── small helpers ────────────────────────────────────────────────────────────

def _client_home(explicit=None):
    return explicit or os.environ.get("FREEBUFF_HOME") or DEFAULT_HOME


def id_epoch(message_id):
    """Epoch ms embedded in a message id, or `None`. The only ordering key."""
    match = _ID.match(str(message_id or ""))
    return int(match.group(2)) if match else None


def id_role(message_id):
    match = _ID.match(str(message_id or ""))
    return match.group(1) if match else None


def blocks_of(message):
    """The message's blocks, and only the ones that can be read.

    Measured the hard way: `blocks` is *not* guaranteed to hold objects. A record
    written by something other than this client (or a synthesised reply handed
    back by a caller) can carry `blocks: ["text", "tool"]`, and a reader that
    assumed dicts raised `AttributeError: 'str' object has no attribute 'get'`
    from inside the settled-boundary scan — which is the worst place for it,
    because that path is reached only when the record has *already* grown, so the
    crash lands on the first read after a write. Non-objects are counted, not
    silently dropped, and `malformed_blocks` is what reports them.
    """
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [b for b in blocks if isinstance(b, dict)]


def malformed_blocks(message):
    """How many entries in `blocks` cannot be read as blocks."""
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        return 0
    return len([b for b in blocks if not isinstance(b, dict)])


def is_divider(message):
    """A `mode-divider` message is `variant: ai` with no answer — `§18.7`'s named
    edge case for "the last assistant message"."""
    return any(b.get("type") == "mode-divider" for b in blocks_of(message))


def tool_blocks(message):
    return [b for b in blocks_of(message)
            if b.get("type") in TOOL_BLOCK_TYPES]


def tool_block_type_seen(message):
    """Which spelling the record uses for tool calls — the drift `§16` predicted."""
    for block in blocks_of(message):
        if block.get("type") in TOOL_BLOCK_TYPES:
            return block.get("type")
    return None


def answer_text(message):
    """The prose of a turn, excluding reasoning and tool calls (`§18.6`)."""
    parts = []
    for block in blocks_of(message):
        if block.get("type") != "text":
            continue
        if block.get("textType") == REASONING_TEXTTYPE:
            continue
        parts.append(block.get("content") or "")
    return "".join(parts)


def reasoning_text(message):
    return "".join(b.get("content") or "" for b in blocks_of(message)
                   if b.get("type") == "text"
                   and b.get("textType") == REASONING_TEXTTYPE)


def tool_names(message):
    return [b.get("toolName") for b in tool_blocks(message)]


# ── 1. the lock: PROC axis ───────────────────────────────────────────────────

def proc_facts(pid, proc=PROC):
    """What `/proc` says about `pid`: alive, is it the client, when did it start.

    `start_time` is `/proc/<pid>/stat` field 22 (clock ticks since boot). It is
    recorded so a pid that dies and is *reused* by an unrelated process cannot be
    mistaken for the session we own — a case `§19.4` rule 5 names and a bare
    `kill(pid, 0)` cannot detect.

    Read-only. This function does not signal; neither does this module.
    """
    facts = {"pid": pid, "alive": False, "is_client": False,
             "start_time": None, "cmdline": None}
    if not isinstance(pid, int) or pid <= 0:
        return facts
    root = os.path.join(proc, str(pid))
    if not os.path.isdir(root):
        return facts
    facts["alive"] = True
    try:
        with open(os.path.join(root, "cmdline"), "rb") as handle:
            raw = handle.read()
        facts["cmdline"] = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        facts["cmdline"] = None
    try:
        with open(os.path.join(root, "stat"), "r", encoding="utf-8",
                  errors="replace") as handle:
            stat = handle.read()
        # `comm` can contain spaces and parens, so split after the last ')'.
        tail = stat[stat.rindex(")") + 2:].split()
        facts["start_time"] = tail[19]
    except (OSError, ValueError, IndexError):
        facts["start_time"] = None
    cmdline = facts["cmdline"] or ""
    facts["is_client"] = CLIENT_MARKER in cmdline
    return facts


def read_lock(path, proc=PROC):
    """The lock file and the `/proc` truth about its owner. Never raises.

    Returns `(verdict, detail)`. Verdicts are `§19.4`'s cases by name:

    - `absent` — no lock file, or not readable as one.
    - `malformed` — present but unusable (bad JSON, no `pid`, wrong type). The
      fail-safe direction is that this is **never** interpreted as ours (`I6`).
    - `owner_dead` — a pid that is gone. `§19.4` rule 3 reclaims this.
    - `owner_reused` — the pid lives but is not the client (rule 5): stale *with
      a recorded reason*, never silently.
    - `live` — a live client owns the machine. `§19` rule 4: refuse to spawn.
    """
    detail = {"lock": path, "owner": None, "proc": None, "reason": None}
    # `exists`, not `isfile`: a *directory* sitting where the lock belongs is
    # present-but-broken, and calling that `absent` would license a spawn over a
    # live owner. Everything present and unreadable is `malformed` (`I6`).
    if not os.path.exists(path):
        detail["reason"] = "no lock file"
        return "absent", detail
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    except OSError as exc:
        # Present but unreadable is *malformed*, not *absent*: it is never ours
        # (`I6`), and calling it absent would invite a spawn over a live owner.
        detail["reason"] = "present but unreadable: %s" % exc
        return "malformed", detail

    try:
        lock = json.loads(raw)
    except ValueError as exc:
        detail["reason"] = "not JSON: %s" % exc
        detail["raw_bytes"] = len(raw)
        return "malformed", detail
    if not isinstance(lock, dict):
        detail["reason"] = "not an object (%s)" % type(lock).__name__
        return "malformed", detail

    pid = lock.get("pid")
    instance = lock.get("instanceId")
    detail["owner"] = {"pid": pid, "instanceId": instance}
    detail["extra_keys"] = sorted(set(lock) - {"pid", "instanceId"})
    if not isinstance(pid, int) or isinstance(pid, bool):
        detail["reason"] = "pid is %s, not an integer" % type(pid).__name__
        return "malformed", detail

    facts = proc_facts(pid, proc=proc)
    detail["proc"] = facts
    if not facts["alive"]:
        detail["reason"] = "pid %d is gone" % pid
        return "owner_dead", detail
    if not facts["is_client"]:
        detail["reason"] = ("pid %d is alive but its cmdline is not the client (%r)"
                            % (pid, (facts["cmdline"] or "")[:80]))
        return "owner_reused", detail
    return "live", detail


def _timestamp_seconds(value):
    """`"2026-09-23T16:46:41.258Z"` → epoch seconds, or `None`. UTC, no guessing."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()


def session_axis(log_path, record_path=None, max_lines=2000):
    """The SESS axis (`§19.2`): `connected` / `reconnecting` / `over` / `unknown`.

    Process liveness and session connectivity are not the same thing: the client
    keeps running while logging that a session ended, which is why this axis
    exists. Two things about reading it are *measured* here, and both of them
    break the obvious implementation:

    1. **The phrase is not the event.** `log.jsonl` is structured JSON and it
       records the payloads of the session's own actions — every file written,
       every command run. On this box, 18 of 27 lines containing
       `Freebuff session over` contain it inside a *payload* (a source file the
       session wrote, a command's output), and only 9 are the client saying it.
       A raw substring scan therefore reads the wrong thing, and reports `over`
       for a session that is working — which would make the router refuse every
       turn on a live brain. So the event is taken from the record's own `msg`
       field, and lines that merely mention the phrase are counted and reported.
    2. **A marker is a statement about the past; progress is a statement about
       now.** If the last event says the session ended but the conversation
       record was written after it, the marker is stale, not the session. This
       is checked with `record_path` rather than assumed, because guessing here
       costs a fallback hop in the safe direction and a wrong answer in the other.
    """
    detail = {"log": log_path, "record_path": record_path, "lines_examined": 0,
              "events": [], "event_count": 0, "mentions": 0, "last_event": None,
              "last_event_seconds": None, "reason": None}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = collections.deque(handle, maxlen=max_lines)
    except OSError as exc:
        detail["reason"] = str(exc)
        return "unknown", detail

    events, mentions = [], 0
    for number, line in enumerate(lines, 1):
        record, message = None, ""
        try:
            parsed = json.loads(line)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            record = parsed
            message = str(parsed.get("msg") or "")
        matched = None
        if message:
            if SESS_OVER in message:
                matched = "over"
            elif SESS_RECONNECTED in message:
                matched = "reconnecting"
        if matched:
            events.append({"line": number, "state": matched,
                           "timestamp": record.get("timestamp"),
                           "seconds": _timestamp_seconds(record.get("timestamp")),
                           "msg": message[:120]})
        elif SESS_OVER in line or SESS_RECONNECTED in line:
            # Present, but nobody said it: a payload the session wrote or ran.
            mentions += 1

    detail["lines_examined"] = len(lines)
    detail["event_count"] = len(events)
    detail["events"] = events[-6:]
    detail["mentions"] = mentions
    if not events:
        detail["reason"] = ("no lifecycle event in %d lines; %d line(s) mention the "
                            "phrase inside a payload" % (len(lines), mentions))
        return "unknown", detail

    last = events[-1]
    detail["last_event"] = last["state"]
    detail["last_event_seconds"] = last["seconds"]
    detail["last_event_msg"] = last["msg"]
    state = last["state"]

    if state == "over" and record_path and last["seconds"] is not None:
        try:
            written = os.path.getmtime(record_path)
        except OSError:
            written = None
        if written is not None and written > last["seconds"]:
            detail["reason"] = ("the last event says the session ended, but the "
                                "record was written %.1fs later — the marker is "
                                "stale, not the session" % (written - last["seconds"]))
            return "connected", detail
    return state, detail


# ── 2. project resolution ────────────────────────────────────────────────────

def resolve_project(home, cwd):
    """Which project directory a `--cwd` maps to, and whether that is ambiguous.

    The project key is the **basename** of the path (measured). `recent-projects.json`
    on this box lists both `/mnt/sdcard/Download/builderbro` and
    `/sdcard/Download/builderbro`; both key to `builderbro`. Two checkouts, one
    directory — so a reader that resolves by basename alone can read the wrong
    conversation and report it as this one's.
    """
    cwd = os.path.realpath(cwd)
    key = os.path.basename(cwd) or cwd
    project_dir = os.path.join(home, "projects", key)
    detail = {"cwd": cwd, "project_key": key, "project_dir": project_dir,
              "recent": [], "collisions": []}
    recent_path = os.path.join(home, "recent-projects.json")
    try:
        with open(recent_path, "r", encoding="utf-8", errors="replace") as handle:
            recent = json.load(handle)
    except (OSError, ValueError):
        recent = None
    if isinstance(recent, list):
        paths = [entry.get("path") for entry in recent
                 if isinstance(entry, dict) and entry.get("path")]
        detail["recent"] = paths
        detail["collisions"] = [p for p in paths
                                if os.path.basename(os.path.realpath(p)) == key]
    if not os.path.isdir(project_dir):
        detail["reason"] = "no project directory for key %r" % key
        return "missing", detail
    if len(detail["collisions"]) > 1:
        detail["reason"] = ("%d recent paths share the basename %r: %s"
                            % (len(detail["collisions"]), key,
                               ", ".join(detail["collisions"])))
        return "ambiguous", detail
    return "ok", detail


# ── 3. active conversation ───────────────────────────────────────────────────

def resolve_active_conversation(project_dir):
    """The live conversation, by *shape* and not by name (`§18.7`).

    Returns `(verdict, chat_dir, detail)`. `newest_by_name` is always reported
    next to the answer, because on this box they disagree by days and the
    disagreement is the whole reason the shape test exists.
    """
    chats = os.path.join(project_dir, "chats")
    detail = {"chats": chats, "newest_by_name": None, "conversations": [],
              "log_dirs": 0}
    try:
        entries = sorted(os.listdir(chats))
    except OSError as exc:
        detail["reason"] = str(exc)
        return "missing", None, detail
    if entries:
        detail["newest_by_name"] = entries[-1]

    conversations = []
    for name in entries:
        path = os.path.join(chats, name)
        if not os.path.isdir(path):
            continue
        if os.path.isfile(os.path.join(path, CONVERSATION_MARKER)):
            conversations.append(name)
        else:
            detail["log_dirs"] += 1
    detail["conversations"] = conversations

    if not conversations:
        detail["reason"] = ("%d dirs, none holding %s — every dir here is a "
                            "per-launch log" % (len(entries), CONVERSATION_MARKER))
        return "none", None, detail

    # Newest by directory mtime among the *conversations*: the id is written when
    # the conversation is opened, so the name is its start time and the newest
    # live one is the most recently written.
    def written(name):
        path = os.path.join(chats, name, CONVERSATION_MARKER)
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    active = max(conversations, key=written)
    detail["active"] = active
    detail["active_written"] = written(active)
    detail["newest_is_active"] = (active == detail["newest_by_name"])
    detail["stale_by_days"] = _days_between(detail["newest_by_name"], active)
    return "ok", os.path.join(chats, active), detail


def _days_between(newer, older):
    """Days from one `YYYY-MM-DDTHH-MM-SS.mmmZ` dir name to another, or None.

    Reported as a number because "the newest directory is not the conversation"
    is an impression until it has a magnitude.
    """
    def parse(name):
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})",
                         name or "")
        if not match:
            return None
        y, mo, d, h, mi, s = (int(part) for part in match.groups())
        return ((y * 372 + mo * 31 + d) * 86400) + h * 3600 + mi * 60 + s
    a, b = parse(newer), parse(older)
    if a is None or b is None:
        return None
    return round((a - b) / 86400.0, 2)


# ── 4. the record as a watermark channel ─────────────────────────────────────

class Watermark(object):
    """`(message_count, prefix_bytes, prefix_hash)` — `§18.7`'s watermark triple.

    `prefix_bytes` is the count of bytes **before the closing bracket**, so the
    next read can re-hash exactly those bytes and prove the file only grew.
    """

    __slots__ = ("message_count", "prefix_bytes", "prefix_hash")

    def __init__(self, message_count, prefix_bytes, prefix_hash):
        self.message_count = message_count
        self.prefix_bytes = prefix_bytes
        self.prefix_hash = prefix_hash

    def as_dict(self):
        return {"message_count": self.message_count,
                "prefix_bytes": self.prefix_bytes,
                "prefix_hash": self.prefix_hash}

    @classmethod
    def from_dict(cls, data):
        return cls(data["message_count"], data["prefix_bytes"],
                   data["prefix_hash"])


def _watermark_of(raw, count):
    """Watermark for a whole file read as `raw`: everything but the final `]`."""
    prefix = raw[:-1] if raw.endswith(b"]") else raw
    return Watermark(count, len(prefix),
                     hashlib.sha256(prefix).hexdigest()[:16])


def _is_settled(message):
    """Whether a message can be consumed into the watermark.

    A user message carries no `isComplete` flag and never will — the prompt is
    finished the moment it is written. Neither does a `mode-divider`, which is a
    state marker rather than a turn. Treating "no flag" as "in flight" for those
    would leave 12 of 17 messages permanently unconsumed on the live record, and
    the watermark would sit at byte 1,457,492 of 2,081,841 for no reason.
    """
    if not isinstance(message, dict):
        return False
    if message.get("variant") == "user":
        return True
    if any(b.get("type") == "mode-divider" for b in blocks_of(message)):
        return True
    return message.get("isComplete") is True


def _settled_boundary(raw):
    """`(byte offset after the last finished message, settled, total)` or `None`.

    This is the correction the live measurement forced. `§18.7` proposed a
    watermark at the end of what has been read, and that does not work, because
    the message currently being written is *rewritten in place* as its blocks and
    metadata grow — so the bytes just before the closing bracket move, and every
    growth invalidates the whole consumed prefix.

    Measured on this box, polling the live record at 0.25 s for 240 s
    (1,081 polls): the file grew by 4,940 bytes and produced **4 resyncs and 0
    usable deltas**. Byte-level: between two consecutive snapshots, the file
    diverged at offset 1,959,925 — inside the in-flight message, which ran from
    1,451,109 — while the last *finished* message ended at 1,450,253. So
    everything up to the last finished message was stable, and everything after
    it is in flux.

    Hence: the watermark belongs at the end of the last `isComplete` message,
    never at the end of the file. The in-flight message is then re-read on each
    poll until it settles, which is the correct behaviour anyway — it is the turn
    being watched, and it is not yet an answer.

    The boundary is computed over **bytes**, not over a decoded string: the live
    record contains literal multi-byte characters (measured: `_settled_boundary`
    refused the real file when it decoded first, because a string index stops
    equalling a byte offset the moment any character is not ASCII). So the
    structure is scanned in bytes. That is safe because every structural
    character of JSON is ASCII — a byte-level scan of `{}[],":\\` cannot be
    confused by a multi-byte sequence, whose continuation bytes are all >= 0x80.

    `None` means the spans could not be established; the caller then falls back
    to a whole-file watermark, which costs a resync rather than a misread.
    """
    spans = _message_spans(raw)
    if spans is None:
        return None
    start_of_array = raw.index(b"[") + 1
    end_of_last_settled, settled = start_of_array, 0
    for start, end in spans:
        try:
            element = json.loads(raw[start:end].decode("utf-8", "replace"))
        except ValueError:
            return None
        if not isinstance(element, dict):
            # A non-object element cannot be a message, so it cannot be settled.
            # Counting it as one would place the watermark past bytes we do not
            # understand; refusing the whole scan is the safe direction.
            return None
        if _is_settled(element):
            settled += 1
            end_of_last_settled = end
    return end_of_last_settled, settled, len(spans)


def _advance_settled(content, start, already_settled):
    """Extend a settled boundary from `start` through `content`.

    The caller has already verified that every byte before `start` is unchanged
    (the prefix hash matched), so the settled elements inside it cannot have
    changed either and do not need decoding again. That is not a micro-optimisation:
    decoding the settled region on every poll measured **1.75 s per poll** against
    22 ms for a watermark at the end of the file — 80x — which would make the
    correction unusable in a bridge that polls.

    The tail is scanned as `[` + tail so the same structural scanner applies, and
    the offsets are shifted back accordingly.
    """
    tail = content[start:]
    spans = _message_spans(b"[" + tail)
    if spans is None:
        return None
    offset, settled = start, already_settled
    for begin, end in spans:
        try:
            decoded = json.loads(tail[begin - 1:end - 1].decode("utf-8", "replace"))
        except ValueError:
            return None
        if _is_settled(decoded):
            settled += 1
            offset = start + end - 1
    return offset, settled, already_settled + len(spans)


def _message_spans(raw):
    """Byte spans of the top-level elements of the compact JSON array, or `None`.

    A structural scan: no JSON parse, because a parse would need the whole file
    as one string and this has to answer with byte offsets. Tracks string state
    and escapes so a brace inside a tool output cannot end an element.
    """
    try:
        index = raw.index(b"[") + 1
    except ValueError:
        return None
    spans = []
    depth = 0
    in_string = False
    escaped = False
    start = None
    length = len(raw)
    while index < length:
        byte = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:      # \
                escaped = True
            elif byte == 0x22:      # "
                in_string = False
        elif byte == 0x22:
            if depth == 0:
                # Every conversation message is an object. A bare top-level
                # string is a malformed element the object/array span scanner
                # would otherwise skip, letting the watermark move past bytes
                # it never inspected.
                return None
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            if depth == 0:
                start = index
            depth += 1
        elif byte in (0x7D, 0x5D):  # } ]
            if depth == 0:
                # The array's own closing bracket. Anything but whitespace after
                # it means this is not the record we think it is.
                if raw[index + 1:].strip():
                    return None
                return spans
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, index + 1))
                start = None
        elif depth == 0 and byte not in b" \t\r\n,":
            # Between elements only a separator is allowed. Without this an array
            # of prose (`[not json]`) scans as an array of zero elements, and the
            # reader would report an empty conversation instead of refusing.
            return None
        index += 1
    return None


def read_record(path, prior=None, settled_only=False):
    """Read the conversation record, whole or as a delta. Never raises.

    With `settled_only`, the watermark is taken at the end of the last finished
    message rather than at the end of the file — the correction `_settled_boundary`
    argues for, and the only setting under which a delta read survives a live
    writer. The consequence the caller must expect: the in-flight message is
    returned by every delta read until it settles, so `message_count` counts
    *finished* messages and a repeated delta is a turn still being written, not
    a duplicate.

    Returns `(status, messages, watermark, detail)`. Statuses:

    - `initial` — a full read.
    - `unchanged` — the file grew by nothing but its closing bracket.
    - `delta` — only the new suffix was read, and the prefix hash proved the file
      did not rewrite what was already consumed.
    - `resync` — the hash moved, so the suffix cannot be trusted; the caller gets
      a full read and a *recorded* cost (`§18.7`: a visible cost, never a silent
      misread). Also returned when the prior watermark is unusable.
    - `torn` — the file was caught mid-rewrite (no closing bracket). A 32 MB file
      rewritten in place is readable while half-written, so this is expected
      occasionally rather than exceptional, and the caller retries.
    """
    detail = {"path": path, "resync_reason": None}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        detail["reason"] = str(exc)
        return "missing", [], None, detail

    # A prior watermark that cannot be trusted is a `resync`, not a fresh
    # `initial`: the caller has to know that its offset was discarded, or it will
    # keep computing deltas from a position that no longer means anything.
    resyncing = False
    if prior is not None:
        if not isinstance(prior, Watermark):
            detail["resync_reason"] = "prior watermark is not a Watermark"
            prior, resyncing = None, True
        elif prior.prefix_bytes > size:
            detail["resync_reason"] = ("record shrank: %d bytes, watermark said %d"
                                       % (size, prior.prefix_bytes))
            prior, resyncing = None, True

    try:
        with open(path, "rb") as handle:
            if prior is None:
                raw = handle.read()
                if not raw.endswith(b"]"):
                    detail["reason"] = "no closing bracket at %d bytes" % size
                    return "torn", [], None, detail
                try:
                    messages = json.loads(raw.decode("utf-8", "replace"))
                except ValueError as exc:
                    detail["reason"] = "unparseable: %s" % exc
                    return "torn", [], None, detail
                if not isinstance(messages, list):
                    detail["reason"] = ("record is %s, not an array"
                                        % type(messages).__name__)
                    return "torn", [], None, detail
                watermark = _watermark_of(raw, len(messages))
                if settled_only:
                    boundary = _settled_boundary(raw)
                    detail["settled_boundary"] = boundary
                    if boundary is not None:
                        offset, settled, total = boundary
                        watermark = Watermark(
                            settled, offset,
                            hashlib.sha256(raw[:offset]).hexdigest()[:16])
                        detail["in_flight"] = total - settled
                return ("resync" if resyncing else "initial", messages,
                        watermark, detail)

            # A delta read: prove the consumed prefix is byte-identical first.
            # Reading it is the only way to know, and it is bounded by the prefix
            # the caller already consumed — not by the file.
            handle.seek(0)
            prefix = handle.read(prior.prefix_bytes)
            digest = hashlib.sha256(prefix).hexdigest()[:16]
            if digest != prior.prefix_hash:
                detail["resync_reason"] = ("prefix hash moved: %s → %s"
                                           % (prior.prefix_hash, digest))
                handle.seek(0)
                raw = handle.read()
                if not raw.endswith(b"]"):
                    detail["reason"] = "no closing bracket at %d bytes" % size
                    return "torn", [], None, detail
                try:
                    messages = json.loads(raw.decode("utf-8", "replace"))
                except ValueError as exc:
                    detail["reason"] = "unparseable: %s" % exc
                    return "torn", [], None, detail
                return ("resync", messages, _watermark_of(raw, len(messages)),
                        detail)

            suffix = handle.read()
    except OSError as exc:
        detail["reason"] = str(exc)
        return "missing", [], None, detail

    if not suffix.endswith(b"]"):
        detail["reason"] = "no closing bracket at %d bytes" % size
        return "torn", [], None, detail
    # The suffix is `,<items>]`. Keep the separator for the watermark — the next
    # read has to hash exactly the bytes before the new closing bracket, and
    # `[a,b` + `,c` is `[a,b,c` while `[a,b` + `c` is not `[a,bc`.
    body = suffix[:-1]
    separator = b"," if body.startswith(b",") else b""
    items = body[len(separator):]
    if not items.strip():
        return ("unchanged", [],
                Watermark(prior.message_count, len(prefix), prior.prefix_hash),
                detail)
    try:
        new_messages = json.loads(b"[" + items + b"]")
    except ValueError as exc:
        detail["reason"] = "delta unparseable: %s" % exc
        return "resync", [], None, detail
    if not isinstance(new_messages, list):
        detail["reason"] = "delta is not an array"
        return "resync", [], None, detail

    full_prefix = prefix + separator + items
    watermark = _watermark_of(full_prefix,
                              prior.message_count + len(new_messages))
    if settled_only:
        # The delta read already holds every byte of the file — `prefix` is what
        # was consumed and `suffix` is the rest — and the prefix hash just proved
        # it did not change, so the boundary only has to be *advanced* over the
        # new suffix rather than recomputed over the record.
        content = prefix + suffix
        boundary = _advance_settled(content, prior.prefix_bytes,
                                    prior.message_count)
        detail["settled_boundary"] = boundary
        if boundary is not None:
            offset, settled, total = boundary
            digest = (prior.prefix_hash if offset == prior.prefix_bytes
                      else hashlib.sha256(content[:offset]).hexdigest()[:16])
            watermark = Watermark(settled, offset, digest)
            detail["in_flight"] = total - settled
    return ("delta", new_messages, watermark, detail)


def watch_record(path, seconds, interval=0.5, settled_only=False):
    """Poll a live record and report how it actually grows.

    This is the measurement the whole watermark design rests on: does the file
    grow only by appending (so a delta read is sound), and does a whole-file
    rewrite ever present as a torn read? Both are answered by watching a session
    that is writing, which on this box is the operator's own.

    `settled_only` chooses between the two watermark rules so they can be
    measured against each other on the same live writer — the comparison that
    produced this module's central finding.
    """
    started = time.time()
    stats = {"path": path, "seconds": seconds, "settled_only": settled_only,
             "polls": 0, "changes": 0, "delta_reads": 0, "unchanged": 0,
             "torn": 0, "resync": 0, "inflight_rereads": 0,
             "first_bytes": None, "last_bytes": None, "messages": None,
             "new_messages": 0, "torn_at_bytes": [], "resync_at_bytes": [],
             "max_seconds": 0.0, "total_seconds": 0.0}
    watermark = None
    previous_count = None
    deadline = started + seconds
    while time.time() < deadline:
        at = time.time()
        status, messages, watermark, detail = read_record(
            path, watermark, settled_only=settled_only)
        stats["polls"] += 1
        if status == "missing":
            stats["missing"] = stats.get("missing", 0) + 1
            time.sleep(interval)
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        if stats["first_bytes"] is None:
            stats["first_bytes"] = size
        stats["last_bytes"] = size
        count = watermark.message_count if watermark is not None else None
        if count is not None:
            stats["messages"] = count
        if status in ("delta", "initial"):
            grew = (previous_count is not None and count is not None
                    and count > previous_count)
            if status == "initial" or grew:
                stats["changes"] += 1
                stats["delta_reads"] += 1
                stats["new_messages"] += len(messages)
            else:
                # In `settled_only` mode a delta that adds no finished message is
                # the turn still being written: re-read on purpose, not new.
                stats["inflight_rereads"] += 1
        elif status == "unchanged":
            stats["unchanged"] += 1
        if count is not None:
            previous_count = count
        elif status == "initial":
            stats["changes"] += 1
        elif status == "torn":
            stats["torn"] += 1
            stats["torn_at_bytes"].append(size)
            watermark = None  # force a full re-read, as a caller would
        elif status == "resync":
            stats["resync"] += 1
            stats["resync_at_bytes"].append(size)
        elapsed = time.time() - at
        stats["max_seconds"] = round(max(stats["max_seconds"], elapsed), 4)
        stats["total_seconds"] = round(stats["total_seconds"] + elapsed, 4)
        # A torn read must not be answered with a nap as long as the interval, or
        # the retry budget vanishes on a slow rewrite.
        time.sleep(0.02 if status == "torn" else interval)
    stats["observed_seconds"] = round(time.time() - started, 1)
    stats["grew_by_bytes"] = (None if stats["first_bytes"] is None
                              or stats["last_bytes"] is None
                              else stats["last_bytes"] - stats["first_bytes"])
    return stats


# ── 5. turn completion ───────────────────────────────────────────────────────

def resolve_turn(messages, posted_epoch, quiet_ms=DEFAULT_QUIET_MS, prior=None,
                 posted_index=None):
    """Resolve a posted prompt to a finished assistant turn (`§18.6`).

    `posted_index` is how many messages the record already held when the prompt
    was posted, and it is the tiebreaker the epoch alone cannot give: ids are
    stamped to the millisecond, so a reply that lands in the *same* millisecond
    as the prompt is not distinguishable from one that was already there. A
    caller that can count (`session.seen` before the write) should pass it; the
    earlier messages are then excluded by position rather than by clock.

    Returns `(verdict, reply, detail)`. Verdicts:

    - `complete` — a new assistant message after `posted_epoch`, not a divider,
      with answer text or a tool call.

    The reply is the **first** qualifying assistant message at or after the
    prompt, not the newest one in the record. Measured: taking the newest makes
    the rule unable to ever report `complete` on a live box, because the newest
    qualifying message is always the turn currently being written — the self-check
    below found that on 12 of 12 real prompts. Taking the first also refuses to
    follow the record into somebody else's later turn, which is the same
    "never guess" rule as everywhere else in this file.
    - `streaming` — a candidate exists but is not finished, or no new message has
      appeared. Per `§18.6` this is **not** a failure: a progressing turn waits.
    - `unreadable` — the record cannot support a reply. `§18.6`: the raw capture
      is handed to the verifier and the router treats it as an explicit failure,
      never as a fabricated answer.

    Two completion signals are accepted and the one that fired is reported:
    `isComplete` (measured on 0.0.186, absent on in-flight messages) and the
    quiet period `§18.6` proposed. `want_quiet_signal` makes the second one the
    sole decider, which is how it is tested.
    """
    detail = {"posted_epoch": posted_epoch, "candidates": 0, "signal": None,
              "tool_block_type": None, "quiet_ms": quiet_ms}
    if not isinstance(messages, list):
        detail["reason"] = "record is not a list"
        return "unreadable", None, detail
    if posted_epoch is None:
        detail["reason"] = "no posted epoch to match against"
        return "unreadable", None, detail

    floor = posted_index if isinstance(posted_index, int) and posted_index >= 0 else 0
    detail["posted_index"] = floor
    first = None
    for index, message in enumerate(messages):
        if index < floor:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("variant") != "ai":
            continue
        epoch = id_epoch(message.get("id"))
        if epoch is None or epoch < posted_epoch:
            continue
        if is_divider(message):
            continue
        if not answer_text(message) and not tool_blocks(message):
            continue
        if first is None or epoch < id_epoch(first.get("id")):
            first = message

    if first is None:
        detail["reason"] = ("no assistant message at or after epoch %s with "
                            "content" % posted_epoch)
        return "streaming", None, detail
    detail["candidates"] = 1
    detail["candidate_id"] = first.get("id")
    detail["tool_block_type"] = tool_block_type_seen(first)

    flagged = "isComplete" in first
    complete = first.get("isComplete")
    if flagged and complete is True:
        detail["signal"] = "isComplete"
    elif flagged and complete is not True:
        detail["signal"] = "isComplete=false"
        detail["reason"] = "the record marks this message unfinished"
        return "streaming", None, detail
    else:
        detail["signal"] = "quiet-period"
        if quiet_ms > 0:
            detail["reason"] = ("no isComplete flag on this record; a quiet period "
                                "of %dms is the only available signal" % quiet_ms)
            return "streaming", None, detail

    reply = {
        "id": first.get("id"),
        "text": answer_text(first),
        "reasoning": reasoning_text(first),
        "tools": [{"toolName": b.get("toolName"), "input": b.get("input"),
                   "output": b.get("output"), "toolCallId": b.get("toolCallId")}
                  for b in tool_blocks(first)],
        "blocks": [b.get("type") for b in blocks_of(first)],
    }
    return "complete", reply, detail


# ── 6. the measurement ───────────────────────────────────────────────────────

def _shape_summary(messages):
    """What the record's own shape is, counted rather than described."""
    shape = {"messages": len(messages), "variants": {}, "ai_with_empty_content": 0,
             "ai_total": 0, "dividers": 0, "tool_block_types": {},
             "text_texttypes": {}, "is_complete": {}, "tools_named": {},
             "id_roles": {}}
    for message in messages:
        if not isinstance(message, dict):
            continue
        variant = message.get("variant")
        shape["variants"][variant] = shape["variants"].get(variant, 0) + 1
        role = id_role(message.get("id"))
        shape["id_roles"][role] = shape["id_roles"].get(role, 0) + 1
        if variant == "ai":
            shape["ai_total"] += 1
            if not (message.get("content") or ""):
                shape["ai_with_empty_content"] += 1
            if "isComplete" in message:
                key = str(message.get("isComplete"))
                shape["is_complete"][key] = shape["is_complete"].get(key, 0) + 1
        if is_divider(message):
            shape["dividers"] += 1
        for block in blocks_of(message):
            kind = block.get("type")
            if kind in TOOL_BLOCK_TYPES:
                shape["tool_block_types"][kind] = (
                    shape["tool_block_types"].get(kind, 0) + 1)
                name = block.get("toolName")
                shape["tools_named"][name] = shape["tools_named"].get(name, 0) + 1
            elif kind == "text":
                tt = block.get("textType")
                shape["text_texttypes"][tt] = shape["text_texttypes"].get(tt, 0) + 1
    return shape


def measure(home=None, cwd=None, proc=PROC, watch_seconds=0.0):
    """Everything M0 asks for, as numbers. Read-only; spawns nothing."""
    home = _client_home(home)
    cwd = os.path.realpath(cwd or HERE)
    report = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "home": home,
        "cwd": cwd,
        "client": {},
        "lock": {},
        "project": {},
        "conversation": {},
        "record": {},
        "turn": {},
        "inconclusive": [],
    }

    metadata_path = os.path.join(home, "freebuff-metadata.json")
    try:
        with open(metadata_path, "r", encoding="utf-8", errors="replace") as handle:
            report["client"]["metadata"] = json.load(handle)
    except (OSError, ValueError) as exc:
        report["client"]["metadata"] = None
        report["client"]["metadata_error"] = str(exc)
    report["client"]["home_exists"] = os.path.isdir(home)

    verdict, detail = read_lock(os.path.join(home, LOCK_NAME), proc=proc)
    report["lock"] = {"verdict": verdict}
    report["lock"].update(detail)
    if verdict in ("absent", "malformed"):
        report["inconclusive"].append("lock: %s (%s)" % (verdict, detail.get("reason")))

    verdict, detail = resolve_project(home, cwd)
    report["project"] = {"verdict": verdict}
    report["project"].update(detail)

    chat_dir = None
    if verdict in ("ok", "ambiguous"):
        verdict_c, chat_dir, detail_c = resolve_active_conversation(
            detail["project_dir"])
        report["conversation"] = {"verdict": verdict_c}
        report["conversation"].update(detail_c)
        if verdict_c != "ok":
            report["inconclusive"].append("conversation: %s (%s)"
                                          % (verdict_c, detail_c.get("reason")))
        if detail_c.get("newest_is_active") is False:
            report["inconclusive"].append(
                "conversation: newest dir %s is not the conversation (%s), "
                "%s days apart — resolved by shape, not by name"
                % (detail_c.get("newest_by_name"), os.path.basename(chat_dir or ""),
                   detail_c.get("stale_by_days")))
    else:
        report["conversation"] = {"verdict": "skipped", "reason": detail.get("reason")}
        report["inconclusive"].append("conversation: %s" % detail.get("reason"))

    record_path = os.path.join(chat_dir, "chat-messages.json") if chat_dir else None
    if record_path:
        meta_path = os.path.join(chat_dir, "chat-meta.json")
        try:
            with open(meta_path, "r", encoding="utf-8", errors="replace") as handle:
                report["record"]["chat_meta"] = json.load(handle)
        except (OSError, ValueError) as exc:
            report["record"]["chat_meta"] = None
            report["record"]["chat_meta_error"] = str(exc)

        started = time.time()
        prior = None
        status = None
        for _ in range(3):
            # The settled watermark is the rule the live measurement selected;
            # `in_flight` is then the number of messages it deliberately leaves
            # unconsumed, which is what a caller must expect to see again.
            status, messages, prior, detail = read_record(record_path,
                                                          settled_only=True)
            if status != "torn":
                break
            time.sleep(0.05)
        full_seconds = time.time() - started
        report["record"].update({
            "path": record_path,
            "status": status,
            "bytes": os.path.getsize(record_path) if os.path.exists(record_path) else None,
            "full_read_seconds": round(full_seconds, 3),
            "detail": detail,
        })
        if status != "initial":
            report["inconclusive"].append("record: %s (%s)"
                                          % (status, detail.get("reason")))
        else:
            report["record"]["shape"] = _shape_summary(messages)
            report["record"]["newlines"] = _count_newlines(record_path)
            report["record"]["watermark"] = prior.as_dict()
            report["record"]["inflight"] = detail.get("in_flight")
            report["record"]["settled_boundary"] = detail.get("settled_boundary")

            # Two independent witnesses to the same fact: the 198-byte
            # `chat-meta.json` poll counter and the array the reader just
            # parsed. `§18.7` measured them equal; if they ever disagree, one of
            # them is wrong and this reader is the one that would be trusted
            # silently, so the disagreement is surfaced instead.
            meta = report["record"].get("chat_meta") or {}
            counted = meta.get("messageCount")
            if isinstance(counted, int) and counted != len(messages):
                report["inconclusive"].append(
                    "record: chat-meta.json says %d messages, the record holds %d"
                    % (counted, len(messages)))

            # A delta read against an unchanged file must report `unchanged`; a
            # delta read that starts one message short must report exactly one
            # new message and a matching hash. Both are assertions about the
            # channel, measured rather than assumed.
            started = time.time()
            status_d, new_messages, _, detail_d = read_record(
                record_path, prior, settled_only=True)
            delta_seconds = time.time() - started
            report["record"]["delta_check"] = {
                "status": status_d,
                "new_messages": len(new_messages),
                "seconds": round(delta_seconds, 3),
                "ratio_vs_full": (round(delta_seconds / full_seconds, 5)
                                  if full_seconds else None),
                "reason": detail_d.get("reason"),
            }

            # Walk the record with the reader and resolve the last user turn, so
            # the completion rule is exercised against real content rather than a
            # fixture. The final turn is normally *in flight* — the session running
            # this measurement is writing it — so `streaming` is the expected and
            # correct answer there, and the last *settled* turn is the one that
            # proves `complete`.
            users = [m for m in messages if isinstance(m, dict)
                     and m.get("variant") == "user"
                     and id_epoch(m.get("id"))]
            if users:
                last_user = users[-1]
                verdict_t, reply, detail_t = resolve_turn(
                    messages, id_epoch(last_user.get("id")))
                report["turn"] = {"verdict": verdict_t, "detail": detail_t,
                                  "prompt": (last_user.get("content") or "")[:120]}
                if reply:
                    report["turn"]["reply"] = {
                        "id": reply["id"],
                        "text_chars": len(reply["text"]),
                        "reasoning_chars": len(reply["reasoning"]),
                        "tools": [t["toolName"] for t in reply["tools"]],
                    }
                # Walk the prompts newest-first to the first one that resolves
                # `complete`. This is the self-check that the rule *reaches* a
                # real answer: on a live box the newest turn is in flight, so
                # `complete` at the top would be the suspicious result, and the
                # count of turns skipped as `streaming` is the number of turns
                # genuinely still running.
                settled, streamed = None, 0
                for user in reversed(users):
                    verdict_s, reply_s, detail_s = resolve_turn(
                        messages, id_epoch(user.get("id")))
                    if verdict_s == "complete":
                        settled = {
                            "verdict": verdict_s,
                            "signal": detail_s.get("signal"),
                            "reply_id": reply_s["id"],
                            "text_chars": len(reply_s["text"]),
                            "tools": [t["toolName"] for t in reply_s["tools"]],
                            "newer_turns_streaming": streamed,
                        }
                        break
                    streamed += 1
                report["turn"]["settled_check"] = settled or {
                    "verdict": "none",
                    "reason": "no prompt in this record resolves complete",
                    "prompts_tried": streamed,
                }
            else:
                report["inconclusive"].append("turn: no user message carries an epoch")

    sess_verdict, sess_detail = session_axis(
        os.path.join(chat_dir, "log.jsonl") if chat_dir else os.path.join(
            report["project"].get("project_dir") or home, "log.jsonl"),
        record_path=record_path)
    report["session_axis"] = {"verdict": sess_verdict}
    report["session_axis"].update(sess_detail)

    if watch_seconds and record_path:
        # Both watermark rules against the same live writer, splitting the budget:
        # this is the comparison the module's central finding rests on, and it is
        # only meaningful when the two are measured on the same session.
        half = max(1.0, watch_seconds / 2.0)
        report["watch_eof_watermark"] = watch_record(record_path, half)
        report["watch"] = watch_record(record_path, half, settled_only=True)
        for label in ("watch", "watch_eof_watermark"):
            if report[label]["torn"]:
                report["inconclusive"].append(
                    "record: %s saw %d torn read(s) while watching a live writer"
                    % (label, report[label]["torn"]))
            if report[label]["resync"]:
                report["inconclusive"].append(
                    "record: %s resynced %d time(s) in %.0fs — the consumed prefix "
                    "was invalidated"
                    % (label, report[label]["resync"],
                       report[label]["observed_seconds"]))
    return report


def _count_newlines(path):
    """Whether the record is compact. `§18.7` measured 0 newlines in 32.8 MB, and
    the whole watermark design follows from that, so it is counted, not assumed."""
    try:
        with open(path, "rb") as handle:
            return handle.read().count(b"\n")
    except OSError:
        return None


# ── 7. the spawn probe (§19.9 Q1–Q5) ─────────────────────────────────────────

def spawn_probe(home=None, cwd=None, wait=40.0, binary="freebuff"):
    """What a **second** launch actually does, without touching the real lock.

    `§19.9` Q3 asks whether a second instance exits with a code, prints a warning,
    or parks in a loading animation, and Q4 asks whether it writes anything we
    could detect. Answering that on the operator's real `HOME` risks the only
    session they have, so this runs under a **scratch `HOME`** (`HOME` is honoured
    by the client, measured) seeded with the installed core and this box's
    credentials, and it reports the real `HOME`'s lock before and after.

    What it cannot do: log in. A scratch home is a fresh client identity, so a
    launch here may stop at auth. That is reported as-is — an honest
    `inconclusive` is a result, and this whole probe exists because a silent pty
    is ambiguous.
    """
    home = _client_home(home)
    real_lock = os.path.join(home, LOCK_NAME)
    before = read_lock(real_lock)
    scratch = tempfile.mkdtemp(prefix="ff-spawn-probe-")
    probe_home = os.path.join(scratch, ".config", "manicode")
    os.makedirs(probe_home)
    seeded = []
    try:
        # Seed the core and identity so the client does not spend the whole budget
        # downloading 49.4 MB into the scratch home (measured: that is exactly what
        # a bare scratch HOME does, and it makes the probe measure the download).
        for name in ("freebuff", "tree-sitter.wasm", "rg", "credentials.json",
                     "settings.json", "freebuff-metadata.json", "analytics-id.json"):
            source = os.path.join(home, name)
            target = os.path.join(probe_home, name)
            if not os.path.exists(source):
                continue
            try:
                os.symlink(source, target)
                seeded.append(name)
            except OSError:
                pass
        log_path = os.path.join(scratch, "spawn.log")
        started = time.time()
        command = [binary, "--cwd", os.path.realpath(cwd or HERE), "--trust-agents"]
        with open(log_path, "wb") as log:
            try:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        cwd=scratch,
                                        env=dict(os.environ, HOME=scratch,
                                                 TERM="xterm-256color"),
                                        start_new_session=True)
            except OSError as exc:
                return {"verdict": "not-run", "reason": str(exc),
                        "command": command, "seeded": seeded}
            exit_code = None
            parked = False
            while time.time() - started < wait:
                exit_code = proc.poll()
                if exit_code is not None:
                    break
                time.sleep(0.5)
            parked = exit_code is None
            if parked:
                _terminate_group(proc)
        seconds = round(time.time() - started, 1)
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                output = handle.read()
        except OSError:
            output = ""
        scratch_lock = os.path.join(probe_home, LOCK_NAME)
        result = {
            "command": command,
            "scratch_home": scratch,
            "seeded": seeded,
            "exit_code": exit_code,
            "parked": parked,
            "seconds": seconds,
            "output_bytes": len(output),
            "output_tail": output[-1200:],
            "scratch_lock": read_lock(scratch_lock, proc="/nonexistent")[1],
            "scratch_lock_after_exit": os.path.exists(scratch_lock),
            "real_lock_before": before[1],
            "real_lock_after": read_lock(real_lock)[1],
            "wrote_downloads": os.path.isdir(
                os.path.join(probe_home, ".freebuff-download-temp")),
        }
        result["real_lock_unchanged"] = (
            (before[1].get("owner") or {}).get("pid")
            == (result["real_lock_after"].get("owner") or {}).get("pid"))
        if parked:
            result["verdict"] = "parked"
        elif exit_code is not None:
            result["verdict"] = "exited"
        else:
            result["verdict"] = "inconclusive"
        return result
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _terminate_group(proc):
    """Kill a process group we started. Never signals a pid we did not spawn."""
    import signal as _signal
    for sig in (_signal.SIGTERM, _signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


# ── 8. CLI ───────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default=None,
                        help="the client's config dir (default ~/.config/manicode)")
    parser.add_argument("--cwd", default=None, help="the project to resolve")
    parser.add_argument("--report", default=None, help="write the measurement here as JSON")
    parser.add_argument("--watch", type=float, default=0.0,
                        help="also watch the live record for this many seconds")
    parser.add_argument("--spawn-probe", action="store_true",
                        help="answer §19.9 Q1–Q5 with an isolated second launch")
    parser.add_argument("--spawn-wait", type=float, default=40.0)
    args = parser.parse_args(argv)

    report = measure(home=args.home, cwd=args.cwd, watch_seconds=args.watch)
    if args.spawn_probe:
        report["spawn_probe"] = spawn_probe(home=args.home, cwd=args.cwd,
                                            wait=args.spawn_wait)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if report["inconclusive"]:
        for item in report["inconclusive"]:
            print("inconclusive: %s" % item, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
