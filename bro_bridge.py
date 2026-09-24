#!/usr/bin/env python3
"""bro_bridge.py — the bridge between `bro` and a live Freebuff session.

    spec: bro-freebuff-brain-spec.md  §7 (C2) · §18 (wire protocol) · §19 (lock)

WHAT THIS IS
------------
Three things, in one process, because they are one problem:

1. **A line protocol** (`§18`) so the Node side needs no pty knowledge: NDJSON on
   stdio, one operation per `req`, exactly one `res` xor `err` back per request.
2. **A lock state machine** (`§19`) that respects the client's singleton lock
   file. The measured normal case on this box is that *somebody else's* session
   owns it — the operator's own — so "refuse, name the owner, offer read-only"
   is the main path, not an edge case.
3. **A disk-first reply channel**: a task goes in over the pty, and the answer
   comes back out of `chat-messages.json`. The pane is used for liveness and as
   an explicit last resort (`capture`), never as the basis of a reply.

WHAT IT DELIBERATELY CANNOT DO
------------------------------
- **Write into a session it does not own.** `ask` with `post: true` on a foreign
  lock is `E_LOCK_FOREIGN`, not an injection. Reading and verifying somebody
  else's session is what `ask` with `post: false` is for (`observe` mode), and
  that is the only mode available under a foreign lock.
- **Fabricate a reply.** If the record cannot support an answer the result is
  `E_UNREADABLE` carrying the raw capture (`§18.6`, decision #16).
- **Spawn a second instance.** Spawning happens only when the lock is *absent*
  or stale. A live foreign owner is a refusal (`I1`).
- **Signal a pid it does not own** (`I2`). Every signal goes through
  `SignalLedger`, and the foreign path is asserted to have sent nothing.

Reused rather than re-invented (`§17`): the record reader, lock reader and turn
resolver are `bro_session_measure`'s (M0's instrument, 89 tests); the terminal
query answering and pane teardown are `live_mcp_probe`'s; the verdict itself is
`verifier.confirm` — called, never copied.

    python3 bro_bridge.py --status
    python3 bro_bridge.py --serve
    python3 bro_bridge.py --call hello --call attach --call status
    python3 bro_bridge.py --self-test
"""

import argparse
import errno
import json
import os
import queue
import sys
import threading
import time

import bro_session_measure as measure

HERE = os.path.dirname(os.path.abspath(__file__))

VERSION = 1
PROTOCOL_MIN = 1
PROTOCOL_MAX = 1

MAX_FRAME = int(os.environ.get("BRO_BRIDGE_MAX_FRAME", 8 * 1024 * 1024))
QUEUE_MAX = int(os.environ.get("BRO_BRIDGE_QUEUE_MAX", 32))
READY_TIMEOUT_S = float(os.environ.get("FREEBUFF_READY_TIMEOUT_S", 45.0))
TURN_TIMEOUT_S = float(os.environ.get("FREEBUFF_TURN_TIMEOUT_S", 600.0))
STALL_S = float(os.environ.get("BRO_BRIDGE_STALL_S", 60.0))
# Consecutive polls a record may be caught mid-rewrite before the turn is called
# unreadable rather than merely slow. `§18.6`: a record that cannot be read is
# `E_UNREADABLE`, never a fabricated reply and never an unbounded wait.
TORN_LIMIT = int(os.environ.get("BRO_BRIDGE_TORN_LIMIT", 40))
QUIET_MS = int(os.environ.get("BRO_BRIDGE_QUIET_MS", 1200))
VERIFY_RETRIES = int(os.environ.get("BRO_VERIFY_RETRIES", 2))
HEARTBEAT_S = float(os.environ.get("BRO_BRIDGE_HEARTBEAT_S", 5.0))
POLL_S = float(os.environ.get("BRO_BRIDGE_POLL_S", 0.5))
CAPTURE_MAX = 64 * 1024

CLIENT = os.environ.get("FREEBUFF_BIN", "freebuff")
SERVER_NAME = "bro_bridge/1"

# `§18.8`, verbatim. `(retryable, fallback_eligible)`. The two polarities that
# matter: a **refusal** is a verdict and must never fall through to another brain
# (`E_REFUSED → fallback false`), and a full queue is *our* problem, not the
# brain's (`E_QUEUE_FULL → fallback false`).
ERRORS = {
    "E_PROTOCOL": (False, False),
    "E_BAD_OP": (False, False),
    "E_LOCK_FOREIGN": (True, True),
    "E_LOCK_MALFORMED": (True, True),
    "E_PROJECT_AMBIGUOUS": (False, True),
    "E_NO_SESSION": (True, True),
    "E_SESSION_DEAD": (True, True),
    "E_PTY": (True, True),
    "E_READY_TIMEOUT": (True, True),
    "E_TIMEOUT_STALLED": (True, True),
    "E_UNREADABLE": (True, True),
    "E_QUEUE_FULL": (True, False),
    "E_REFUSED": (False, False),
    "E_INTERNAL": (True, False),
}

# `§19.3`, by name. `FOREIGN` and `FOREIGN_ADOPT` are distinct because only the
# second one may serve reads.
ABSENT = "ABSENT"
STARTING = "STARTING"
ATTACHED = "ATTACHED"
RECONNECTING = "RECONNECTING"
DEGRADED = "DEGRADED"
FOREIGN = "FOREIGN"
RELEASING = "RELEASING"

# `§19.2`: READY is two axes, not one. A reconnecting session is *not* ready.
CONNECTED = "connected"
RECONNECTING_AXIS = "reconnecting"
SESSION_OVER = "over"

# Announced in `hello`, and this list *is* the implementation: every name here
# has an `op_<name>` and every name not here is `E_BAD_OP` (`§18.4`: the bridge
# never assumes a capability it did not announce, and the courtesy runs the
# other way too). `new` and `interrupt` are deliberately absent: `new` would
# fork the conversation the queue is serialized against, and `interrupt` cannot
# be honoured through a pty we do not control. Announcing either would be a lie
# rather than a limitation.
CAPABILITIES = ["status", "attach", "ask", "observe", "capture", "stop", "shutdown"]

# The prompt framing (`§18.10`). Everything between the sentinels is our own
# text, and a pane frame that contains them is an echo — excluded from reply
# assembly, because a pty echoes input and the prompt necessarily names whatever
# it asks for.
PROMPT_OPEN = "<<<BRO-TASK"
PROMPT_CLOSE = "BRO-TASK>>>"

BRO_HOME = os.environ.get("BRO_HOME") or os.path.join(os.path.expanduser("~"), ".bro")
SESSION_FILE = "freebuff-session.json"

# Tool names that mean "read a file", per client (the trailing `s` is the
# measured spelling on 0.0.186). Used only to decide whether a claim can be
# independently re-observed by reading the file ourselves.
READER_TOOLS = ("read_file", "read_files", "read", "view_file", "cat")


# ── errors ───────────────────────────────────────────────────────────────────

class BridgeError(Exception):
    """A protocol-visible failure: a taxonomy code, and nothing guessed.

    `retryable` and `fallback_eligible` default to the table in `§18.8` and are
    overridable only where a call site knows better than the table (a refusal
    that must not fall through, an `E_LOCK_FOREIGN` raised by an `ask`).
    """

    def __init__(self, code, message, detail=None, retryable=None,
                 fallback_eligible=None):
        Exception.__init__(self, message)
        if code not in ERRORS:
            code = "E_INTERNAL"
        default_retry, default_fallback = ERRORS[code]
        self.code = code
        self.message = message
        self.detail = detail or {}
        self.retryable = default_retry if retryable is None else retryable
        self.fallback_eligible = (default_fallback if fallback_eligible is None
                                  else fallback_eligible)

    def as_dict(self, request_id):
        frame = {"v": VERSION, "id": request_id, "type": "err", "ok": False,
                 "code": self.code, "message": self.message,
                 "retryable": self.retryable,
                 "fallback_eligible": self.fallback_eligible}
        if self.detail:
            frame["detail"] = self.detail
        return frame


class SignalLedger(object):
    """Every signal this process sends, and to whom (`I2`).

    `§19`'s invariant is not "we try not to kill foreign pids" — it is that the
    foreign path sends **nothing**, asserted by recording every call and
    requiring an empty list. A ledger is the only way that assertion can be
    written, so the bridge has no other way to signal: `os.kill`/`os.killpg` are
    reached through here and nowhere else.
    """

    def __init__(self, dry_run=False):
        self.calls = []
        self.dry_run = dry_run

    def killpg(self, pid, sig):
        entry = {"pid": pid, "signal": int(sig), "dry_run": self.dry_run}
        try:
            pgid = os.getpgid(pid)
            entry["pgid"] = pgid
        except OSError as exc:
            entry["pgid_error"] = exc.strerror
            pgid = None
        self.calls.append(entry)
        if self.dry_run or pgid is None:
            return False
        try:
            os.killpg(pgid, sig)
            return True
        except OSError as exc:
            entry["error"] = exc.strerror
            return False

    def as_list(self):
        return [dict(call) for call in self.calls]


def _proc_pgid(pid, proc=measure.PROC):
    """`/proc/<pid>/stat` field 5 — the process group.

    Read through the same fake-`/proc` seam as everything else, so the identity
    check below is testable without a real client. Returns `None` when unknown,
    which is *not* a match.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        with open(os.path.join(proc, str(pid), "stat"), "r", encoding="utf-8",
                  errors="replace") as handle:
            stat = handle.read()
        return int(stat[stat.rindex(")") + 2:].split()[2])
    except (OSError, ValueError, IndexError):
        return None


# ── the lock (§19.4) ─────────────────────────────────────────────────────────

class LockMachine(object):
    """`§19.4`'s decision procedure, in order, with the transitions recorded.

    Step 2 is the one M0's measurement makes interesting: the lock is very often
    the *operator's own* session, so "ours" has to be decidable. `pid` equality
    is not enough on this client — the lock's pid is the **core** while the
    process we spawn is the wrapper — so ownership is: same pid, or same process
    group as our child, or a matching `instanceId` we recorded when we took it.
    """

    def __init__(self, home, own=None, proc=measure.PROC, ledger=None):
        self.home = home
        self.lock_path = os.path.join(home, measure.LOCK_NAME)
        self.own = own or {}
        self.proc = proc
        self.ledger = ledger if ledger is not None else SignalLedger()
        self.events = []

    def note(self, event, **fields):
        """`I5` — no transition without a recorded event line."""
        record = {"event": event, "at": round(time.time(), 3)}
        record.update(fields)
        self.events.append(record)
        return record

    def _is_ours(self, owner):
        if not owner:
            return False
        pid = owner.get("pid")
        instance = owner.get("instanceId")
        if self.own.get("instanceId") and instance == self.own["instanceId"]:
            return True
        if self.own.get("pid") and pid == self.own["pid"]:
            return True
        ours_pgid = self.own.get("pgid")
        if ours_pgid and pid:
            # The child we spawned is the core's ancestor, so it is a different
            # pid in the same session group. This is what makes "ours" decidable
            # across the wrapper→core exec that `§19.9` Q5 measured.
            return _proc_pgid(pid, proc=self.proc) == ours_pgid
        return False

    def decide(self):
        """`(state, detail)`. Never raises; never signals; never writes the lock."""
        verdict, detail = measure.read_lock(self.lock_path, proc=self.proc)
        owner = detail.get("owner") or {}
        proc_facts = detail.get("proc") or {}
        out = {
            "lock_verdict": verdict,
            "lock": self.lock_path,
            "owner": owner,
            "pid_alive": bool(proc_facts.get("alive")),
            "owner_cmdline": proc_facts.get("cmdline"),
            "reason": detail.get("reason"),
            "ours": False,
        }
        if verdict == "absent":
            self.note("acquire", lock_verdict=verdict,
                      reason="no lock file — the machine is free")
            return ABSENT, out
        if verdict == "malformed":
            # `I6`: fail-safe direction is refuse-to-spawn. An unreadable lock is
            # never interpreted as ours.
            out["code"] = "E_LOCK_MALFORMED"
            self.note("refuse", code="E_LOCK_MALFORMED",
                      reason=detail.get("reason"))
            return FOREIGN, out
        if verdict in ("owner_dead", "owner_reused"):
            out["stale_reason"] = verdict
            if verdict == "owner_reused":
                # Rule 5: alive but not the client. Stale *with the reason
                # recorded*, never silently.
                self.note("reclaimable", reason="pid_reused",
                          old_pid=owner.get("pid"))
                out["reason_code"] = "pid_reused"
            else:
                self.note("reclaimable", reason="owner_dead",
                          old_pid=owner.get("pid"))
                out["reason_code"] = "owner_dead"
            return ABSENT, out
        # `live`
        out["ours"] = self._is_ours(owner)
        if out["ours"]:
            self.note("ready", ours=True, pid=owner.get("pid"))
            return ATTACHED, out
        self.note("refuse", code="E_LOCK_FOREIGN", owner_pid=owner.get("pid"),
                  instance_id=owner.get("instanceId"))
        return FOREIGN, out


# ── the pty child ────────────────────────────────────────────────────────────

class PtyChild(object):
    """A spawned client on a pty, with the terminal queries answered.

    The local `spawn` (rather than `live_mcp_probe.spawn`) exists for one reason:
    the isolated run needs `HOME` overridden so a second instance cannot touch
    the operator's lock, chat history or credentials-in-place. The query
    answering and the teardown are the probe's, reused.
    """

    def __init__(self, command, cwd, env=None, columns=120, rows=44):
        import fcntl
        import pty
        import struct
        import subprocess
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, columns, 0, 0))
        child_env = dict(os.environ)
        child_env.setdefault("TERM", "xterm-256color")
        child_env.update(env or {})
        self.proc = subprocess.Popen(command, cwd=cwd, stdin=slave, stdout=slave,
                                     stderr=slave, env=child_env,
                                     start_new_session=True)
        os.close(slave)
        self.master = master
        self.pgid = os.getpgid(self.proc.pid)
        self.stats = {}
        self.pane = bytearray()

    def pump(self, seconds):
        """Read the pane for `seconds`, answering terminal queries as they come."""
        import select

        import live_mcp_probe as probe

        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.pane.extend(chunk)
            probe.answer(self.master, chunk, self.stats)
            if "first_byte_seconds" not in self.stats:
                self.stats["first_byte_seconds"] = round(seconds, 1)
        return bytes(self.pane)

    def write(self, text):
        try:
            os.write(self.master, text.encode("utf-8"))
        except OSError as exc:
            raise BridgeError("E_PTY", "writing to the pane failed: %s" % exc,
                              {"errno": exc.errno})

    def alive(self):
        return self.proc.poll() is None

    def pane_text(self):
        return bytes(self.pane).decode("utf-8", "replace")

    def terminate(self, ledger):
        """`terminate` the *group we started*, and only that group (`I2`)."""
        import signal

        ledger.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=5)
        except Exception:
            ledger.killpg(self.proc.pid, signal.SIGKILL)
        try:
            os.close(self.master)
        except OSError:
            pass


# ── the session ──────────────────────────────────────────────────────────────

class Session(object):
    """An attached conversation: the project, the record, the axes, the child.

    The reader is `bro_session_measure.read_record` with `settled_only=True`,
    because M0 measured that a watermark at the end of file is invalidated by the
    very growth it watches for. Messages accumulate here so a delta read can
    still be resolved as a whole.
    """

    def __init__(self, home, cwd, state, lock=None, project=None, chat_dir=None,
                 session_id=None, owner=None, ours=False, child=None,
                 boot_seconds=None, warnings=None, proc=measure.PROC):
        self.home = home
        self.cwd = cwd
        self.state = state
        self.lock = lock or {}
        self.project = project
        self.chat_dir = chat_dir
        self.session_id = session_id
        self.owner = owner or {}
        self.ours = ours
        self.child = child
        self.boot_seconds = boot_seconds
        self.warnings = list(warnings or [])
        self.proc = proc
        self.seen = []
        self.watermark = None
        self.read_status = None
        self.resyncs = 0
        self.last_progress = time.time()
        self.axis = None
        self.record_project_root = None
        self.project_root_source = None

    @property
    def record_path(self):
        return os.path.join(self.chat_dir, "chat-messages.json") if self.chat_dir else None

    @property
    def log_path(self):
        return os.path.join(self.chat_dir, "log.jsonl") if self.chat_dir else None

    def read(self, settled_only=True):
        """One delta read. Never raises; a failure is a status the caller records."""
        if not self.record_path:
            return "missing", [], None, {"reason": "no chat directory resolved"}
        status, messages, watermark, detail = measure.read_record(
            self.record_path, self.watermark, settled_only=settled_only)
        self.read_status = status
        if status in ("delta", "initial", "resync"):
            for message in messages:
                if message not in self.seen:
                    self.seen.append(message)
        if status == "resync":
            self.resyncs += 1
            self.seen = list(messages)
        if watermark is not None:
            self.watermark = watermark
        return status, messages, watermark, detail

    def progress_marker(self):
        """A cheap tuple that changes when the channel moved."""
        try:
            size = os.path.getsize(self.record_path)
        except (OSError, TypeError):
            size = None
        count = self.watermark.message_count if self.watermark else None
        try:
            mtime = os.path.getmtime(self.record_path)
        except (OSError, TypeError):
            mtime = None
        return (size, count, mtime)

    def session_axis_now(self):
        """`§19.2`'s SESS axis, read from the client's own `msg` field (M0's fix)."""
        if not self.log_path:
            return None, {}
        verdict, detail = measure.session_axis(self.log_path, self.record_path)
        self.axis = verdict
        return verdict, detail

    def usable(self):
        """Whether this session can serve an `ask`.

        **The axis is reported, not gated — deliberately.** `§19.2` says
        `READY ⇔ pid_alive ∧ connected`, and on this box that rule would refuse
        every turn on a healthy brain: M0 measured the operator's live, working
        session reporting `reconnecting`, because a reconnect callback fires and
        nothing fires the opposite marker. What *is* gated is `over`, and only
        after the reader has already shown the marker is not stale — i.e. the
        record has not been written since the client said the session ended.
        Gating the rest would be the expensive failure in the safe-looking
        direction, so the axis travels in every result instead.
        """
        if self.state not in (ATTACHED, DEGRADED, FOREIGN):
            return False
        if self.axis == SESSION_OVER:
            return False
        return self.child is None or self.child.alive()

    def write_prompt(self, task):
        """One delimited submission (`§18.10`). The sentinels make the echo findable."""
        framed = "%s %s %s\n" % (PROMPT_OPEN, task.replace("\r", " ").replace("\n", " "),
                                 PROMPT_CLOSE)
        self.child.write(framed)
        return framed


# Where a conversation records the directory it was opened in. Measured live on
# client 0.0.186: **`run-state.json`**, at `sessionState.fileContext.projectRoot`.
# `chat-meta.json` holds only `messageCount`/`firstPrompt`/`messagesSize`/
# `messagesMtimeMs` — M0's note that "the record names its own project root" is
# right about the conversation *directory* and wrong about the file, which is why
# the source is returned and reported rather than assumed.
PROJECT_ROOT_SOURCES = (
    ("run-state.json", ("sessionState", "fileContext", "projectRoot")),
    ("chat-meta.json", ("metadata", "runState", "sessionState", "fileContext",
                        "projectRoot")),
)


def project_root_of(chat_dir):
    """`(root, source_file)` — the directory a conversation was opened in.

    Only field lookups on parsed JSON: `projectRoot` also appears *inside*
    `chat-messages.json` and `log.jsonl` (it is in the text of files that were
    read and printed), which is exactly the substring-match trap M0 found in
    `log.jsonl`. Structure, never text.
    """
    for name, path in PROJECT_ROOT_SOURCES:
        try:
            with open(os.path.join(chat_dir, name), "r", encoding="utf-8",
                      errors="replace") as handle:
                node = json.load(handle)
        except (OSError, ValueError):
            continue
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, str) and node.strip():
            return node, name
    return None, None


def inside(path, root):
    """Whether `path` is `root` or under it — the guard for anything that writes.

    Written down as a function because it has one job and it is easy to get wrong:
    the first version of M1's clone stage resolved the conversation under the
    *client's* home while attaching under a scratch one, so its stub writer had a
    path into the operator's live record. The append did not survive the client's
    own next rewrite (the client owns that file), but "it happened not to matter"
    is not a safety property. A writer now has to prove where it is writing.
    """
    if not path or not root:
        return False
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(root)
    return real_path == real_root or real_path.startswith(real_root + os.sep)


def _same_directory(left, right):
    """How two path strings are the same directory, or `None`.

    Measured live: `/mnt/sdcard/Download/builderbro` and
    `/sdcard/Download/builderbro` are listed as two recent projects, are the
    *same* directory (both `st_dev=176 st_ino=817413` on this box), and neither
    is a symlink — so `realpath` cannot tell and the inode can. String equality
    would have called the operator's own session ambiguous.
    """
    if not left or not right:
        return None
    if os.path.realpath(left) == os.path.realpath(right):
        return "realpath"
    try:
        a, b = os.stat(left), os.stat(right)
    except OSError:
        return None
    if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino):
        return "st_dev/st_ino"
    return None


def _written(chat_dir):
    try:
        return os.path.getmtime(os.path.join(chat_dir, "chat-meta.json"))
    except OSError:
        return 0.0


def resolve_conversation(home, cwd):
    """Which conversation belongs to `cwd` — `(verdict, chat_dir, detail)`.

    Measured live: the project key is a **basename**, and this box lists both
    `/mnt/sdcard/Download/builderbro` and `/sdcard/Download/builderbro`, so
    `resolve_project` reports `ambiguous` for the very cwd a session is running
    in. M0 found the key that actually distinguishes them — the record's own
    `projectRoot` (`§19.9` Q6) — and this promotes it from a cross-check to the
    resolution rule.

    The fail-safe direction is preserved: when no record claims the requested
    root, the answer is `ambiguous`, never "the newest one", because reading the
    wrong checkout's conversation and reporting it as this one's is the failure
    the ambiguity check exists to prevent.
    """
    verdict, detail = measure.resolve_project(home, cwd)
    if verdict not in ("ok", "ambiguous"):
        return verdict, None, detail
    status, chat_dir, conversation = measure.resolve_active_conversation(
        detail["project_dir"])
    # The conversation detail carries the directory it looked in, which is what
    # the ambiguous branch has to enumerate.
    detail = dict(detail, conversation=conversation, chats=conversation.get("chats"))
    if verdict == "ok":
        return (status, chat_dir if status == "ok" else None, detail)

    matches = []
    matched_by = {}
    try:
        entries = sorted(os.listdir(detail["chats"]))
    except OSError as exc:
        detail["reason"] = "%s (and the basename is ambiguous)" % exc
        return "ambiguous", None, detail
    for name in entries:
        candidate = os.path.join(detail["chats"], name)
        root, source = project_root_of(candidate)
        how = _same_directory(root, cwd)
        if how:
            matches.append(candidate)
            matched_by[candidate] = {"projectRoot": root, "source": source,
                                     "matched_by": how}
    detail["project_root_matches"] = matched_by
    if not matches:
        detail["reason"] = (
            "%d recent paths share the basename %r and none of the %d conversations "
            "in %s names %s as its projectRoot"
            % (len(detail["collisions"]), detail["project_key"], len(entries),
               detail["chats"], cwd))
        return "ambiguous", None, detail
    chosen = max(matches, key=_written)
    detail["disambiguated_by"] = matched_by[chosen]
    return "ok", chosen, detail


def resolve_last_turn(messages):
    """The newest *settled* user turn: `(prompt_epoch, user_id, verdict, reply, detail)`.

    This is observe mode's whole job — answer "what did the session last say?" for
    a session we do not own, without writing anything into it. It is deliberately
    the last **settled** turn and not the last turn: on a live box the newest turn
    is the one being written, and reporting a half-written turn as the answer is
    the fabricated-reply failure wearing a timestamp.

    So prompts are walked newest-first and the first one with a finished reply
    wins. Two cases are then distinguishable and both are reported rather than
    smoothed over:

    - the winning prompt is not the newest one → `detail["in_flight"]` is true and
      `newer_prompt_pending` names the prompt still being answered. The settled
      answer is returned *with* that label, because "the session's last answer"
      and "the session is mid-turn right now" are both true and a reader needs
      both.
    - no prompt has a finished reply → verdict `streaming`, `reply` None. The
      caller must not turn that into an answer.
    """
    prompts = []
    for message in messages:
        if not isinstance(message, dict) or message.get("variant") != "user":
            continue
        epoch = measure.id_epoch(message.get("id"))
        if epoch is not None:
            prompts.append((epoch, message.get("id")))
    if not prompts:
        return None, None, "unreadable", None, {
            "reason": "no user message with an epoch-bearing id in the record"}
    prompts.sort(reverse=True)
    newest_epoch, newest_id = prompts[0]
    for epoch, message_id in prompts:
        verdict, reply, detail = measure.resolve_turn(messages, epoch, quiet_ms=QUIET_MS)
        if verdict == "complete" and reply is not None:
            detail["prompt_epoch"] = epoch
            detail["in_flight"] = epoch != newest_epoch
            if epoch != newest_epoch:
                detail["newer_prompt_pending"] = newest_id
            return epoch, message_id, verdict, reply, detail
    return newest_epoch, newest_id, "streaming", None, {
        "reason": "no prompt in this record has a finished reply",
        "newest_prompt": newest_id, "prompts": len(prompts)}


# ── the verification gate (C4) ───────────────────────────────────────────────

def _resolved_paths(blob, base):
    """Every quoted string in a tool input that names a file, as a realpath.

    Structure-free on purpose: the `input` shape varies by tool (object, string,
    `paths` list), and a claim about which file was read should not depend on
    guessing the shape right — only on whether the path resolves to that file.
    """
    out = set()
    for chunk in str(blob).split('"'):
        text = chunk.strip()
        if not text or len(text) < 2 or "\\" in text:
            continue
        candidate = text if os.path.isabs(text) else os.path.join(base, text)
        try:
            if os.path.exists(candidate):
                out.add(os.path.realpath(candidate))
        except OSError:
            continue
    return out


class Gate(object):
    """`§C4` — the one call site where a claim is checked.

    The verifier is `verifier.confirm`, called with a claim built from the
    record's own tool block. Two things are worth being explicit about:

    - **A turn with no declared expectation verifies nothing.** It is reported as
      `not_declared`, not as a pass, because "we looked at it and it seemed fine"
      is the thing this gate exists to replace.
    - **A reader tool can be re-observed independently.** When the client's tool
      was `read_files`, the independent observation is *our* read of that file
      from disk — which is why the claim can reach `invariant` rather than only
      `observed`. The transcript is never the evidence; the file is.
    """

    def __init__(self, cwd, reader=None, max_read_bytes=200 * 1024):
        self.cwd = cwd
        self.max_read_bytes = max_read_bytes
        self._reader = reader

    def _read_path(self, path):
        """Our own read of a file the client claimed to have read. No shell, no eval."""
        if not isinstance(path, str) or not path.strip():
            return None
        candidate = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        candidate = os.path.realpath(candidate)
        try:
            if not os.path.isfile(candidate):
                return None
            if os.path.getsize(candidate) > self.max_read_bytes:
                return None
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    def _rerun_for(self, tool, arg):
        """An independent re-observation, or `None` when there is nothing to redo.

        A reader tool with no argument names no file, so there is nothing to
        re-read — and offering a rerun that can only return `None` would fail the
        `reproduced` check and refuse a claim whose *evidence* was never in doubt.
        The absence of an independent observation is not a contradiction; it is
        the `observed` band, and the verdict says so by name.
        """
        lowered = (tool or "").lower()
        if lowered not in READER_TOOLS:
            return None
        if not isinstance(arg, str) or not arg.strip():
            return None
        return lambda _arg: self._read_path(arg)

    def match_tool_block(self, reply, expect):
        """The record's tool block the expectation names, or `None`.

        Matching is deliberately loose about the `input` shape — measured tool
        blocks carry `input` as either an object or a string depending on the
        tool — and strict about the name, because a claim about `read_files`
        confirmed by a `run_terminal_command` is not a confirmation.
        """
        wanted = (expect or {}).get("tool")
        if not wanted:
            return None, "the expectation names no tool"
        arg = (expect or {}).get("arg")
        candidates = []
        for block in (reply or {}).get("tools") or []:
            name = (block.get("toolName") or "")
            if name == wanted or name.lower() == str(wanted).lower():
                candidates.append(block)
        if not candidates:
            return None, ("no tool block named %r in this turn (observed: %s)"
                          % (wanted, ", ".join(sorted(
                              {str((b.get("toolName") or "?"))
                               for b in (reply or {}).get("tools") or []})) or "none"))
        if arg:
            needle = str(arg).lower()
            wanted_path = None
            if isinstance(arg, str) and arg.strip():
                wanted_path = os.path.realpath(
                    arg if os.path.isabs(arg) else os.path.join(self.cwd, arg))
            for block in candidates:
                blob = json.dumps(block.get("input"), default=str)
                if needle in blob.lower():
                    return block, None
                if wanted_path and wanted_path in _resolved_paths(blob, self.cwd):
                    # Measured: this client's reader calls carry **relative**
                    # paths (`{"paths": [".agents/mcp.json"]}`) while an
                    # expectation may name the file absolutely. Same file, so
                    # the same call — compared by `realpath`, not by string.
                    return block, None
            return None, ("a %r block ran, but no call carried the argument %r"
                          % (wanted, arg))
        return candidates[0], None

    def judge(self, reply, expect):
        """`(verdict_dict, result)` — `result` is None unless something was refused."""
        import verifier

        out = {"declared": bool(expect), "level": None, "ok": False,
               "tool_observed": False, "checks": [], "bounce_note": None,
               "verified": "not_declared"}
        if not expect:
            out["bounce_note"] = ("this turn declared no expectation, so nothing "
                                  "about it was verified")
            return out, None

        block, note = self.match_tool_block(reply, expect)
        if block is None:
            out["verified"] = "cannot_verify"
            out["bounce_note"] = note
            return out, "cannot_verify"

        out["tool_observed"] = True
        tool = block.get("toolName")
        arg = expect.get("arg")
        raw = block.get("output")
        output = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        claim = {"tool": tool, "arg": arg, "output": output,
                 "expect": _parse_check(expect.get("check"))}
        # `deterministic_tools=None`: here the eligible set *is* "tools we hold an
        # independent observation for", and `_rerun_for` supplies one only for
        # the reader tools. Without this, `verifier.DETERMINISTIC_TOOLS` decides —
        # and it names `read_file`, while the client on this box spells it
        # `read_files` (measured, M0). The one tool the client actually uses would
        # then be structurally unable to leave `observed`, which is a limitation
        # dressed up as caution.
        ruling = verifier.confirm(claim, rerun=self._rerun_for(tool, arg),
                                  deterministic_tools=None)
        out["level"] = ruling.level
        out["ok"] = bool(ruling.ok)
        out["checks"] = ruling.checks
        out["how_verified"] = ruling.how_verified
        out["refused_by"] = ruling.refused_by
        out["verified"] = ruling.level
        out["claim"] = {"tool": tool, "arg": arg,
                        "expect": _format_check(claim["expect"]),
                        "output_chars": len(output),
                        "output_head": output[:400]}
        if not ruling.ok:
            out["bounce_note"] = ("claim not confirmed over the available evidence: "
                                  "refused by %s" % ruling.refused_by)
            return out, "refused"
        return out, None


def _parse_check(text):
    """`autonomy.parse_spec`, imported lazily and used exactly once.

    A second expectation grammar would be a second thing to audit, so the bridge
    does not have one.
    """
    import autonomy
    return autonomy.parse_spec(text)


def _format_check(spec):
    import autonomy
    return autonomy.format_spec(spec)


# ── the bridge ───────────────────────────────────────────────────────────────

class Bridge(object):
    """The protocol server. `serve()` reads frames from stdin; `handle()` is one frame."""

    def __init__(self, home=None, cwd=None, proc=measure.PROC, spawner=None,
                 ledger=None, inline=False, state_dir=None, clock=time.time,
                 client=CLIENT, ready_timeout=None, quiet_ms=None):
        self.home = home or measure.DEFAULT_HOME
        self.cwd = os.path.realpath(cwd or HERE)
        self.proc = proc
        self.clock = clock
        self.client = client
        self.ready_timeout = READY_TIMEOUT_S if ready_timeout is None else ready_timeout
        self.quiet_ms = QUIET_MS if quiet_ms is None else quiet_ms
        self.ledger = ledger if ledger is not None else SignalLedger()
        self.state_dir = state_dir or BRO_HOME
        self.spawner = spawner or self._spawn
        self.inline = inline

        self.lock_machine = LockMachine(self.home, proc=proc, ledger=self.ledger)
        self.session = None
        self.state = ABSENT
        self.started = self.clock()
        self.had_hello = False
        self.turns = {}
        self.calls = []
        self.notes = []
        self.turn = None
        self._queue = queue.PriorityQueue()
        self._seq = 0
        self._out_lock = threading.Lock()
        self._worker = None
        self._stop = threading.Event()
        self._out = sys.stdout

    # ── frames out ──
    def emit(self, frame):
        with self._out_lock:
            self._out.write(json.dumps(frame, default=str) + "\n")
            self._out.flush()
        return frame

    def note(self, text, **fields):
        record = {"at": round(self.clock(), 3), "note": text}
        record.update(fields)
        self.notes.append(record)
        return record

    def _require_hello(self):
        if not self.had_hello:
            raise BridgeError("E_PROTOCOL", "hello must be the first frame")

    # ── ops ──
    def op_hello(self, req):
        if self.had_hello:
            raise BridgeError("E_PROTOCOL", "hello was already negotiated")
        low = req.get("proto_min", VERSION)
        high = req.get("proto_max", VERSION)
        if not isinstance(low, int) or not isinstance(high, int):
            raise BridgeError("E_PROTOCOL", "proto_min/proto_max must be integers")
        effective = min(high, PROTOCOL_MAX)
        if effective < max(low, PROTOCOL_MIN):
            raise BridgeError("E_PROTOCOL",
                              "no protocol overlap: client %d..%d, server %d..%d"
                              % (low, high, PROTOCOL_MIN, PROTOCOL_MAX))
        self.had_hello = True
        state, detail = self.lock_machine.decide()
        self.state = state
        return {"v": effective, "server": SERVER_NAME,
                "platform": "posix" if os.name == "posix" else "windows",
                "capabilities": list(CAPABILITIES),
                "instance": {"lock": detail.get("lock_verdict"),
                             "owner": detail.get("owner"),
                             "state": state,
                             "home": self.home,
                             "cwd": self.cwd},
                "verify_retries": VERIFY_RETRIES}

    def op_attach(self, req):
        self._require_hello()
        cwd = req.get("cwd") or self.cwd
        want_adopt = bool(req.get("adopt"))
        want_spawn = req.get("spawn", True) and not want_adopt
        if cwd and os.path.realpath(cwd) != self.cwd:
            self.cwd = os.path.realpath(cwd)
        state, detail = self.lock_machine.decide()
        self.state = state

        if state == FOREIGN:
            if detail.get("code") == "E_LOCK_MALFORMED":
                raise BridgeError(
                    "E_LOCK_MALFORMED",
                    "the lock file is present but unreadable; refusing to spawn",
                    detail)
            if not want_adopt:
                raise BridgeError(
                    "E_LOCK_FOREIGN", "a Freebuff instance already owns this machine",
                    {"owner": detail.get("owner"), "pid_alive": detail.get("pid_alive"),
                     "read_only_attach": True,
                     "owner_cmdline": detail.get("owner_cmdline")},
                    retryable=True, fallback_eligible=True)
            session = self._open_session(state=FOREIGN, detail=detail, ours=False)
            if session is None:
                raise BridgeError("E_NO_SESSION",
                                  "the owning instance's conversation could not be resolved",
                                  detail, retryable=True, fallback_eligible=True)
            session.warnings.append(
                "read-only adopt: this session belongs to pid %s, so `ask` may "
                "observe it but never post into it" % (detail.get("owner") or {}).get("pid"))
            self.session = session
            self.state = FOREIGN
            self._persist()
            return self._attach_payload(session, detail)

        if state == ATTACHED and detail.get("ours"):
            session = self._open_session(state=ATTACHED, detail=detail, ours=True)
            self.session = session
            self.state = ATTACHED
            self._persist()
            return self._attach_payload(session, detail)

        # ABSENT (or stale-and-reclaimed): the machine is free, so we may spawn.
        if not want_spawn:
            session = self._open_session(state=DEGRADED, detail=detail, ours=False)
            if session is None:
                raise BridgeError("E_NO_SESSION", "no session attached and spawning was "
                                  "not requested", detail)
            self.session = session
            self.state = DEGRADED
            return self._attach_payload(session, detail)
        return self._spawn_and_attend(detail)

    def _spawn_and_attend(self, detail):
        self.state = STARTING
        started = self.clock()
        child = self.spawner()
        self.lock_machine.own["pid"] = child.proc.pid
        self.lock_machine.own["pgid"] = child.pgid
        self.lock_machine.note("spawn", pid=child.proc.pid, pgid=child.pgid,
                               client=self.client, cwd=self.cwd)

        # Ready is not a sleep: it is a painted pane *and* a resolvable
        # conversation, because a client that has painted but written no record
        # yet cannot answer anything.
        deadline = started + self.ready_timeout
        paints = False
        chat_dir = None
        while self.clock() < deadline:
            child.pump(0.5)
            if child.pane:
                paints = True
            if chat_dir is None:
                chat_dir = self._resolve_chat_dir()
            if paints and chat_dir:
                break
            if not child.alive():
                raise BridgeError("E_SESSION_DEAD",
                                  "the spawned client exited before it was ready",
                                  {"pid": child.proc.pid, "seconds": round(self.clock() - started, 1)})
        boot = round(self.clock() - started, 1)
        if not (paints and chat_dir):
            child.terminate(self.ledger)
            raise BridgeError(
                "E_READY_TIMEOUT", "no ready frame within %.1fs" % self.ready_timeout,
                {"painted": paints, "record_resolved": bool(chat_dir),
                 "seconds": boot, "pane_bytes": len(child.pane)},
                retryable=True, fallback_eligible=True)

        state, detail2 = self.lock_machine.decide()
        ours = detail2.get("ours")
        self.lock_machine.note("ready", seconds=boot, lock_state=state, ours=ours)
        warnings = []
        if not ours:
            # We spawned and the lock is not attributable to us. That is a real
            # anomaly and it is reported, not smoothed over: it means some other
            # process took the machine while we were booting.
            warnings.append("spawned, but the lock is not attributable to our child")
        session = self._open_session(state=ATTACHED if ours else FOREIGN,
                                     detail=detail2, ours=bool(ours),
                                     child=child, boot_seconds=boot,
                                     warnings=warnings)
        if session is None:
            child.terminate(self.ledger)
            raise BridgeError("E_NO_SESSION",
                              "the spawned session wrote no resolvable conversation",
                              {"boot_seconds": boot, "chat_dir": chat_dir})
        self.session = session
        self.state = session.state
        self._persist()
        return self._attach_payload(session, detail2)

    def _resolve_chat_dir(self):
        verdict, chat_dir, _detail = resolve_conversation(self.home, self.cwd)
        return chat_dir if verdict == "ok" else None

    def _open_session(self, state, detail, ours, child=None, boot_seconds=None,
                      warnings=None):
        verdict, chat_dir, project_detail = resolve_conversation(self.home, self.cwd)
        if verdict == "ambiguous":
            # Fail-safe: two checkouts share this basename and the record does not
            # settle which one this is. Refuse *by name* rather than read one of
            # them and call it this project's.
            raise BridgeError(
                "E_PROJECT_AMBIGUOUS",
                "the project key %r is shared and no record claims this root"
                % project_detail.get("project_key"),
                {"cwd": self.cwd, "collisions": project_detail.get("collisions"),
                 "reason": project_detail.get("reason")},
                retryable=False, fallback_eligible=True)
        if verdict != "ok":
            self.note("project_unresolved", verdict=verdict,
                      reason=project_detail.get("reason"))
            return None
        warnings = list(warnings or [])
        if len(project_detail.get("collisions") or []) > 1:
            # `§18.7`: the basename collision is real on this box, and it was
            # resolved — by the record naming its own root (M0's `§19.9` Q6),
            # which is reported here so a reader can see which key decided.
            resolved = project_detail.get("disambiguated_by") or {}
            warnings.append(
                "project basename collides across %d paths (%s); resolved by %s"
                % (len(project_detail["collisions"]),
                   ", ".join(project_detail["collisions"]),
                   ("%s's projectRoot=%s, matched by %s"
                    % (resolved.get("source"), resolved.get("projectRoot"),
                       resolved.get("matched_by"))
                   if resolved else "the active-conversation shape")))
        session = Session(self.home, self.cwd, state, lock=detail,
                          project=os.path.basename(project_detail["project_key"]),
                          chat_dir=chat_dir, session_id=os.path.basename(chat_dir),
                          owner=detail.get("owner"), ours=ours, child=child,
                          boot_seconds=boot_seconds, warnings=warnings, proc=self.proc)
        root, source = project_root_of(chat_dir)
        if root:
            session.record_project_root = root
            session.project_root_source = source
            if not _same_directory(root, self.cwd):
                session.warnings.append(
                    "the record says projectRoot=%s (%s), this attach asked for %s"
                    % (root, source, self.cwd))
        else:
            session.warnings.append(
                "no conversation file here carries a projectRoot to cross-check "
                "(looked for %s)"
                % ", ".join(name for name, _path in PROJECT_ROOT_SOURCES))
        axis, _axis_detail = session.session_axis_now()
        if axis == SESSION_OVER:
            # The client said the session ended and the record has not moved
            # since — the one axis value that gates (`usable()`). The state stays
            # what the lock says, because "we own this session" and "the client
            # thinks it is over" are different facts and only the second one is
            # about serving a turn.
            session.warnings.append(
                "the client logged %r and the record has not been written since; "
                "posting is refused until it is rejoined" % SESSION_OVER)
        elif axis == RECONNECTING_AXIS:
            # Reported, never gated: M0 measured this marker on a live, working
            # session (a reconnect callback fires and nothing fires the opposite
            # marker), so refusing on it would refuse every turn on a healthy
            # brain — the expensive failure in the safe-looking direction.
            session.warnings.append(
                "the client logged a reconnect; the session is still being "
                "written, so this is reported and not gated")
        return session

    def _attach_payload(self, session, detail):
        return {"state": session.state, "session_id": session.session_id,
                "project": session.project, "chat_dir": session.chat_dir,
                "owner": {"pid": (session.owner or {}).get("pid"),
                          "instanceId": (session.owner or {}).get("instanceId"),
                          "ours": session.ours},
                "boot_seconds": session.boot_seconds,
                "read_only": not session.ours,
                "warnings": session.warnings,
                "lock": {"verdict": detail.get("lock_verdict"),
                         "reason": detail.get("reason")},
                "axis": session.axis,
                "home": self.home}

    def op_status(self, req):
        # `§18.4`: always answerable, even mid-turn, and read-only. "Always" is
        # read strictly: every op is behind `hello`, `status` included, because
        # `hello` is what announces the capability — an unnegotiated `status` is
        # an unannounced protocol, which is the one thing `§18.2` forbids.
        self._require_hello()
        state, detail = self.lock_machine.decide()
        session = self.session
        watermark = None
        if session is not None and session.watermark is not None:
            watermark = session.watermark.as_dict()
            try:
                watermark["bytes"] = os.path.getsize(session.record_path)
            except (OSError, TypeError):
                pass
        turn = None
        if self.turn is not None:
            turn = {"turn_id": self.turn.get("turn_id"), "state": self.turn.get("state"),
                    "posted_epoch": self.turn.get("posted_epoch")}
        return {"lock": {"state": state, "verdict": detail.get("lock_verdict"),
                         "owner": detail.get("owner"), "ours": detail.get("ours"),
                         "reason": detail.get("reason")},
                "session": None if session is None else {
                    "state": session.state, "session_id": session.session_id,
                    "project": session.project, "chat_dir": session.chat_dir,
                    "ours": session.ours, "axis": session.axis,
                    "boot_seconds": session.boot_seconds, "read_only": not session.ours},
                "turn": turn,
                "watermark": watermark,
                "queue_depth": self._queue.qsize(),
                "uptime_s": round(self.clock() - self.started, 1),
                "resyncs": None if session is None else session.resyncs}

    def op_capture(self, req):
        self._require_hello()
        wanted = int(req.get("bytes") or CAPTURE_MAX)
        wanted = max(1, min(wanted, CAPTURE_MAX))
        if self.session is None or self.session.child is None:
            raise BridgeError("E_NO_SESSION", "nothing to capture: no session we spawned")
        raw = self.session.child.pane_text()
        truncated = len(raw) > wanted
        text = raw[-wanted:]
        return {"raw": text, "truncated": truncated,
                "echo": _is_echo(text),
                "pane_bytes": len(raw),
                "note": ("echoed prompt excluded from reply assembly" if _is_echo(text)
                         else "pane text; advisory only")}

    def op_stop(self, req):
        self._require_hello()
        if self.session is None or self.session.child is None:
            raise BridgeError("E_NO_SESSION", "no session we own to stop")
        self.state = RELEASING
        self.session.child.terminate(self.ledger)
        self.lock_machine.note("stop", graceful=bool(req.get("graceful", True)),
                               pid=self.session.child.proc.pid)
        self.session.state = DEGRADED
        self.state = DEGRADED
        return {"stopped": True, "signals": self.ledger.as_list()}

    def op_observe(self, req):
        """`observe` is `ask` with posting forced off — the read-only half.

        It exists as its own announced op because the *client* has to be able to
        say "look at this session, do not touch it" without knowing which session
        it will turn out to be: on a foreign lock, `ask` answers `E_LOCK_FOREIGN`
        while `observe` is exactly the permitted thing (`§19.5`). The turn still
        runs, still queues, and still goes through the same gate — the only
        difference is that no bytes are written into the record.
        """
        self._require_hello()
        forced = dict(req)
        forced["post"] = False
        return self.op_ask(forced)

    def op_shutdown(self, req):
        self._require_hello()
        self.state = RELEASING
        self.lock_machine.note("shutdown", queue_depth=self._queue.qsize(),
                               turn_id=(self.turn or {}).get("turn_id"))
        self._stop.set()
        return {"stopping": True, "signals": self.ledger.as_list()}

    def op_ask(self, req):
        self._require_hello()
        turn = self._prepare_turn(req)
        if self.inline:
            frame = self._run_turn_frame(turn)
            return frame
        priority = 0 if turn["priority"] == "cli" else 1
        if self._queue.qsize() >= QUEUE_MAX:
            raise BridgeError("E_QUEUE_FULL", "bridge backlog is full",
                              {"queue_depth": self._queue.qsize()}, fallback_eligible=False)
        self._seq += 1
        self._queue.put((priority, self._seq, turn))
        self._start_worker()
        self.emit({"v": VERSION, "type": "evt", "op": "queued",
                   "position": self._queue.qsize(), "turn_id": turn["turn_id"]})
        return {"queued": True, "turn_id": turn["turn_id"],
                "position": self._queue.qsize()}

    def _prepare_turn(self, req):
        session = self.session
        if session is None:
            raise BridgeError("E_NO_SESSION", "no session attached",
                              {"hint": "attach first"}, retryable=True,
                              fallback_eligible=True)
        turn_id = req.get("turn_id")
        if not turn_id:
            raise BridgeError("E_PROTOCOL", "ask requires turn_id")
        task = req.get("task")
        if task is not None and not isinstance(task, str):
            raise BridgeError("E_PROTOCOL", "task must be a string")
        if turn_id in self.turns:
            # `§18.5` idempotency: a re-sent completed turn returns the recorded
            # result instead of posting a second prompt.
            return {"replay": True, "turn_id": turn_id,
                    "recorded": self.turns[turn_id]}
        post = req.get("post")
        if post is None:
            # The default follows who owns the session, never what would be
            # convenient: a foreign session is observed (the only permitted
            # thing), and one we own but cannot serve is refused *by name* so the
            # caller can choose to observe instead of being silently downgraded.
            if session.ours and not session.usable():
                raise BridgeError(
                    "E_SESSION_DEAD",
                    "this session cannot serve a turn (state %s, axis %s)"
                    % (session.state, session.axis),
                    {"state": session.state, "axis": session.axis,
                     "instead": "ask with post:false to observe it"},
                    retryable=True, fallback_eligible=True)
            post = bool(session.ours and session.state in (ATTACHED, DEGRADED))
        post = bool(post)
        if post and not session.ours:
            raise BridgeError(
                "E_LOCK_FOREIGN",
                "this session belongs to another instance; posting is not permitted",
                {"owner": session.owner, "read_only": True,
                 "asked": "post", "instead": "ask with post:false to observe it"},
                retryable=True, fallback_eligible=True)
        if post and not session.usable():
            raise BridgeError(
                "E_SESSION_DEAD",
                "the session cannot serve a turn (state %s, axis %s)"
                % (session.state, session.axis),
                {"state": session.state, "axis": session.axis},
                retryable=True, fallback_eligible=True)
        if post and not (isinstance(task, str) and task.strip()):
            # A task is required to *write*; an observation has nothing to write,
            # and demanding a prompt for it would be asking the caller to invent a
            # question so a read could happen.
            raise BridgeError("E_PROTOCOL", "ask requires a non-empty task when posting")
        expect = req.get("expect")
        if expect is not None and not isinstance(expect, dict):
            raise BridgeError("E_PROTOCOL", "expect must be an object")
        return {"replay": False, "turn_id": turn_id, "task": task or "", "post": post,
                "req_id": req.get("id"),
                "expect": expect, "priority": req.get("priority") or "cli",
                "timeout_s": float(req.get("timeout_s") or TURN_TIMEOUT_S),
                "posted_epoch": req.get("posted_epoch"),
                "attempt": 0, "state": "prepared", "started": self.clock()}

    def _run_turn_frame(self, turn):
        """Run one turn and return the frame the client should receive."""
        try:
            payload = self.run_turn(turn)
        except BridgeError as exc:
            return exc.as_dict(turn.get("req_id") or turn.get("turn_id"))
        return {"v": VERSION, "id": turn.get("req_id") or turn["turn_id"],
                "type": "res", "ok": True, **payload}

    def run_turn(self, turn):
        if turn.get("replay"):
            return turn["recorded"]
        session = self.session
        posting = turn["post"]
        if posting:
            session.read()  # settle the watermark before the write
            before = session.progress_marker()
            # How many messages the record already held. This is the tiebreaker
            # for the same-millisecond case: a reply is *after* the prompt by
            # position even when the ids cannot say so by clock.
            turn["posted_index"] = len(session.seen)
            # Stamped *before* the write, not after: `§18.6` matches replies at or
            # after this epoch, and a reply that lands in the same millisecond as
            # the prompt must not be skipped as "older than the prompt".
            turn["posted_epoch"] = int(self.clock() * 1000)
            session.write_prompt(turn["task"])
            self.lock_machine.note("turn_posted", turn_id=turn["turn_id"],
                                   posted_epoch=turn["posted_epoch"],
                                   watermark_before=before[1])
        turn["state"] = "running"
        self.turn = turn

        result = None
        blind = 0
        last_progress = self.clock()
        last_marker = session.progress_marker()
        started = self.clock()
        while True:
            if self.clock() - started > turn["timeout_s"]:
                break
            if session.child is not None and not session.child.alive():
                raise BridgeError("E_SESSION_DEAD",
                                  "the session process exited during the turn",
                                  {"pid": session.child.proc.pid})
            status, _messages, _wm, detail = session.read()
            if status == "resync":
                self.emit({"v": VERSION, "type": "evt", "op": "resync",
                           "reason": detail.get("resync_reason"),
                           "bytes": (session.watermark.prefix_bytes
                                     if session.watermark else None),
                           "resyncs": session.resyncs})
            if status == "missing":
                # Not a stall: there is no channel to wait on. The reader says
                # why, and the raw pane goes along with it — `§18.6`'s escape
                # hatch, so the caller can see what the client is actually doing
                # instead of being handed a guess.
                raise BridgeError(
                    "E_UNREADABLE", "the conversation record is not readable",
                    {"path": session.record_path, "reason": detail.get("reason"),
                     "raw_capture": (session.child.pane_text()[-4000:]
                                     if session.child else None)},
                    retryable=True, fallback_eligible=True)
            if status == "torn":
                blind += 1
                if blind > TORN_LIMIT:
                    raise BridgeError(
                        "E_UNREADABLE",
                        "the record was mid-rewrite for %d consecutive polls" % blind,
                        {"path": session.record_path, "polls": blind,
                         "bytes": session.progress_marker()[0],
                         "raw_capture": (session.child.pane_text()[-4000:]
                                         if session.child else None)},
                        retryable=True, fallback_eligible=True)
                time.sleep(POLL_S)
                continue
            blind = 0

            prompt_epoch = turn["posted_epoch"]
            if not posting:
                prompt_epoch, _prompt_id, verdict, reply, rdetail = \
                    resolve_last_turn(session.seen)
                if prompt_epoch is None:
                    raise BridgeError("E_UNREADABLE", "no settled turn to observe",
                                      {"reason": rdetail.get("reason")})
                if verdict != "complete" or reply is None:
                    # Mid-turn. There is no answer to read yet, and the one thing
                    # this must never do is name the newest assistant message as
                    # the reply, which is precisely the part still being written.
                    raise BridgeError(
                        "E_UNREADABLE", "the session is mid-turn: no finished reply "
                        "to observe",
                        {"reason": rdetail.get("reason"),
                         "newest_prompt": rdetail.get("newest_prompt"),
                         "in_flight": (session.watermark.as_dict()
                                       if session.watermark else None),
                         "raw_capture": (session.child.pane_text()[-4000:]
                                         if session.child else None)},
                        retryable=True, fallback_eligible=True)
                result = ("complete", reply, rdetail)
                break

            verdict, reply, rdetail = measure.resolve_turn(
                session.seen, prompt_epoch, quiet_ms=self.quiet_ms,
                posted_index=turn.get("posted_index"))
            if verdict == "complete":
                result = (verdict, reply, rdetail)
                break
            if verdict == "unreadable":
                raise BridgeError("E_UNREADABLE", "the reply could not be assembled",
                                  {"detail": rdetail,
                                   "raw_capture": session.child.pane_text()[-4000:]
                                   if session.child else None},
                                  retryable=True, fallback_eligible=True)
            # The quiet-period fallback `§18.6` proposed, used only where the
            # record carries no `isComplete` flag — and timed against the file's
            # own mtime, so it is a measurement rather than a sleep.
            if rdetail.get("signal") == "quiet-period":
                try:
                    quiet = self.clock() - os.path.getmtime(session.record_path)
                except (OSError, TypeError):
                    quiet = 0.0
                if quiet * 1000 >= self.quiet_ms:
                    verdict2, reply2, rdetail2 = measure.resolve_turn(
                        session.seen, prompt_epoch, quiet_ms=0,
                        posted_index=turn.get("posted_index"))
                    if verdict2 == "complete":
                        rdetail2["signal"] = "quiet-period(mtime)"
                        result = (verdict2, reply2, rdetail2)
                        break

            marker = session.progress_marker()
            if marker != last_marker:
                last_marker = marker
                last_progress = self.clock()
                self.emit({"v": VERSION, "type": "evt", "op": "turn_progress",
                           "turn_id": turn["turn_id"],
                           "watermark": (session.watermark.as_dict()
                                         if session.watermark else None),
                           "last_progress_ms": int((self.clock() - started) * 1000)})
            elif self.clock() - last_progress > STALL_S and turn["timeout_s"] > 0:
                raise BridgeError(
                    "E_TIMEOUT_STALLED",
                    "no progress for %.0fs" % STALL_S,
                    {"seconds": round(self.clock() - started, 1),
                     "in_flight": (session.watermark.as_dict()
                                   if session.watermark else None)},
                    retryable=True, fallback_eligible=True)
            time.sleep(POLL_S)

        if result is None:
            raise BridgeError("E_TIMEOUT_STALLED",
                              "turn exceeded %.0fs" % turn["timeout_s"],
                              {"seconds": round(self.clock() - started, 1)},
                              retryable=True, fallback_eligible=True)

        _verdict, reply, rdetail = result
        gate = Gate(self.cwd)
        evidence, refusal = gate.judge(reply, turn["expect"])
        evidence["turn_id"] = turn["turn_id"]
        evidence["reply_id"] = (reply or {}).get("id")
        evidence["signal"] = rdetail.get("signal")
        evidence["tool_block_type"] = rdetail.get("tool_block_type")
        evidence["tool_names"] = sorted({str(t.get("toolName"))
                                         for t in (reply or {}).get("tools") or []})
        evidence["mode"] = "post" if posting else "observe"
        evidence["attempt"] = turn["attempt"]
        if rdetail.get("in_flight"):
            # Observing a session whose newest prompt is unanswered: the settled
            # answer is returned, and this says why it may not be the one the
            # caller was picturing.
            evidence["in_flight"] = True
            evidence["newer_prompt_pending"] = rdetail.get("newer_prompt_pending")
        payload = {"turn_id": turn["turn_id"],
                   "reply": None if reply is None else {
                       "text": reply.get("text"),
                       "blocks": reply.get("blocks"),
                       "tools": [{"toolName": t.get("toolName"), "input": t.get("input"),
                                  "toolCallId": t.get("toolCallId")}
                                 for t in reply.get("tools") or []],
                       "reasoning_chars": len(reply.get("reasoning") or "")},
                   "evidence": evidence,
                   "state": session.state,
                   "attempt": turn["attempt"],
                   "timings": {"seconds": round(self.clock() - started, 1),
                               "posted_epoch": turn["posted_epoch"]}}

        if refusal is not None:
            turn["state"] = "refused"
            self.turns[turn["turn_id"]] = payload
            self.lock_machine.note("turn_refused", turn_id=turn["turn_id"],
                                   verdict=evidence.get("verified"),
                                   refused_by=evidence.get("refused_by"))
            raise BridgeError(
                "E_REFUSED",
                "claim not confirmed over the available evidence",
                {"verdict": evidence.get("verified"),
                 "checked": [c.get("check") for c in evidence.get("checks") or []],
                 "bounce_note": evidence.get("bounce_note"),
                 "reply_id": evidence.get("reply_id"),
                 "tool_names": evidence.get("tool_names")},
                retryable=False, fallback_eligible=False)

        turn["state"] = "done"
        self.turns[turn["turn_id"]] = payload
        self.lock_machine.note("turn_done", turn_id=turn["turn_id"],
                               verdict=evidence.get("verified"),
                               seconds=payload["timings"]["seconds"])
        return payload

    # ── the worker (so a second `ask` queues instead of interleaving, §18.5) ──
    def _start_worker(self):
        if self.inline or self._worker is not None:
            return
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

    def _work(self):
        while not self._stop.is_set():
            try:
                _priority, _seq, turn = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self.emit(self._run_turn_frame(turn))

    def handle(self, req):
        """One request frame → exactly one response frame (`§18.3`).

        A protocol-visible failure is returned as an `err` frame rather than
        raised, because "one `res` xor one `err` per `req`" is the contract the
        client sees: `serve` would have to convert it anyway, and a `handle` that
        sometimes raises and sometimes returns is a contract with two shapes.
        Unexpected exceptions are *not* caught here — those are `serve`'s to
        report as `E_INTERNAL`, so a bug in this file cannot be mistaken for a
        verdict about the session.
        """
        try:
            return self._handle(req)
        except BridgeError as exc:
            return exc.as_dict(req.get("id") if isinstance(req, dict) else None)

    def _handle(self, req):
        if not isinstance(req, dict):
            raise BridgeError("E_PROTOCOL", "a frame must be a JSON object")
        if "type" in req and req.get("type") != "req":
            raise BridgeError("E_PROTOCOL", "the bridge only accepts `req` frames")
        op = req.get("op")
        if not op:
            raise BridgeError("E_PROTOCOL", "req without an op")
        handler = getattr(self, "op_" + str(op), None)
        if handler is None or (str(op) not in CAPABILITIES
                               and str(op) not in ("hello", "attach")):
            raise BridgeError("E_BAD_OP", "unknown or unannounced op %r" % op,
                              {"capabilities": list(CAPABILITIES)})
        self.calls.append({"op": str(op), "id": req.get("id")})
        payload = handler(req)
        if isinstance(payload, dict) and payload.get("queued"):
            # An async ask answers later, from the worker.
            return {"v": VERSION, "id": req.get("id"), "type": "evt", "op": "accepted",
                    **payload}
        if isinstance(payload, dict) and payload.get("type") in ("res", "err"):
            return payload
        return {"v": VERSION, "id": req.get("id"), "type": "res", "ok": True, **payload}

    def serve(self, stream=None, heartbeat=True):
        """The NDJSON loop. A bad frame is answered and the connection stays up."""
        stream = stream or sys.stdin
        if heartbeat:
            thread = threading.Thread(target=self._heartbeat, daemon=True)
            thread.start()
        for line in stream:
            if not line.strip():
                continue
            if len(line.encode("utf-8", "replace")) > MAX_FRAME:
                self.emit(BridgeError("E_PROTOCOL", "frame exceeds %d bytes" % MAX_FRAME,
                                      {"bytes": len(line)}).as_dict(None))
                continue
            try:
                req = json.loads(line)
            except ValueError as exc:
                self.emit(BridgeError("E_PROTOCOL", "not JSON: %s" % exc).as_dict(None))
                continue
            try:
                frame = self.handle(req)
            except Exception as exc:  # a bridge bug is E_INTERNAL, never a silent drop
                frame = BridgeError("E_INTERNAL", "%s: %s" % (type(exc).__name__, exc),
                                    {"where": "handle"}).as_dict(
                    req.get("id") if isinstance(req, dict) else None)
            if frame is not None:
                self.emit(frame)
            if req.get("op") == "shutdown" and frame is not None \
                    and frame.get("type") != "err":
                break
        self._stop.set()
        self.release()
        return self

    def _heartbeat(self):
        while not self._stop.is_set():
            time.sleep(HEARTBEAT_S)
            if self._stop.is_set():
                break
            session = self.session
            self.emit({"v": VERSION, "type": "evt", "op": "heartbeat",
                       "state": self.state,
                       "turn_id": (self.turn or {}).get("turn_id"),
                       "queue_depth": self._queue.qsize(),
                       "watermark": (session.watermark.as_dict()
                                     if session and session.watermark else None),
                       "uptime_s": round(self.clock() - self.started, 1)})

    def release(self):
        """Stop only what we started (`I2`), and say what we signalled."""
        if self.session is not None and self.session.child is not None:
            if self.session.child.alive():
                self.session.child.terminate(self.ledger)
                self.lock_machine.note("release", pid=self.session.child.proc.pid)
        return self.ledger.as_list()

    # ── persistence (`§9`: ~/.bro/freebuff-session.json) ──
    def _persist(self):
        if self.session is None:
            return
        record = {"session_id": self.session.session_id, "project": self.session.project,
                  "chat_dir": self.session.chat_dir, "cwd": self.cwd,
                  "home": self.home, "state": self.session.state,
                  "ours": self.session.ours, "owner": self.session.owner,
                  "updated": int(self.clock())}
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            path = os.path.join(self.state_dir, SESSION_FILE)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            self.note("persist_failed", error=str(exc), dir=self.state_dir)
        return record

    def _spawn(self):
        """The default spawner: the real client on a pty, in `self.cwd`."""
        return PtyChild([self.client, "--cwd", self.cwd, "--trust-agents"], self.cwd)


def _is_echo(text):
    """`§18.10`: our own prompt coming back is not the client talking."""
    return PROMPT_OPEN in (text or "") or PROMPT_CLOSE in (text or "")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print(value):
    json.dump(value, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true",
                        help="speak NDJSON on stdio until EOF or shutdown")
    parser.add_argument("--status", action="store_true",
                        help="print lock + session status as JSON and exit")
    parser.add_argument("--call", action="append", default=[],
                        help="run one op locally and print it (repeatable)")
    parser.add_argument("--home", default=None,
                        help="the client's config dir (default: ~/.config/manicode)")
    parser.add_argument("--cwd", default=HERE, help="the project the session runs in")
    parser.add_argument("--adopt", action="store_true",
                        help="on a foreign lock, attach read-only instead of refusing")
    parser.add_argument("--no-spawn", action="store_true",
                        help="never start a client; attach to what exists")
    parser.add_argument("--state-dir", default=None, help="where to persist session state")
    parser.add_argument("--turn-timeout", type=float, default=TURN_TIMEOUT_S)
    parser.add_argument("--ready-timeout", type=float, default=READY_TIMEOUT_S)
    parser.add_argument("--self-test", action="store_true",
                        help="protocol round trip against a stubbed session, no client")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    bridge = Bridge(home=args.home, cwd=args.cwd, state_dir=args.state_dir,
                    ready_timeout=args.ready_timeout,
                    # `--call` is one-shot: the caller is waiting for the answer, so
                    # the worker queue would only turn a result into a promise.
                    inline=bool(args.call))
    bridge.spawner = (lambda: (_ for _ in ()).throw(
        BridgeError("E_NO_SESSION", "spawning disabled by --no-spawn"))) \
        if args.no_spawn else bridge.spawner

    if args.status:
        bridge.had_hello = True
        _print(bridge.op_status({}))
        return 0

    if args.call:
        out = []
        for op in args.call:
            request = {"v": VERSION, "id": op, "type": "req", "op": op,
                       "cwd": args.cwd, "adopt": args.adopt,
                       "spawn": not args.no_spawn}
            if op == "hello":
                request.update({"client": "bro_bridge-cli", "proto_min": 1, "proto_max": 1})
            out.append(bridge.handle(request))
        _print(out)
        bridge.release()
        return 0 if not any(f.get("type") == "err" for f in out) else 1

    if args.serve:
        bridge.serve()
        return 0

    parser.print_help()
    return 0


def self_test():
    """A protocol round trip with a stubbed session: no client, no pty, no network.

    This is what `--self-test` is for: it proves the frame contract (hello first,
    one `res` xor `err` per `req`, taxonomy codes, refusal polarity) on the machine
    you are standing on, in about a second, without touching a real session.
    """
    import tempfile

    bridge = Bridge(home=tempfile.mkdtemp(prefix="bro-bridge-selftest-"), cwd=HERE,
                    state_dir=tempfile.mkdtemp(prefix="bro-bridge-state-"))
    frames = []
    # The first frame is deliberately `status` before `hello`: it must come back
    # `E_PROTOCOL`, which is the negative control for `§18.2` in this self-test.
    for request in ({"op": "status"}, {"op": "hello", "proto_min": 1, "proto_max": 1},
                    {"op": "attach", "spawn": False}, {"op": "status"},
                    {"op": "interrupt"}, {"op": "shutdown"}):
        request = dict(request, v=VERSION, id=request["op"],
                       type="req")
        try:
            frames.append(bridge.handle(request))
        except BridgeError as exc:
            frames.append(exc.as_dict(request["id"]))
    _print({"frames": frames, "ledger": bridge.ledger.as_list(),
            "events": bridge.lock_machine.events})
    return 0 if all(f.get("type") in ("res", "err") for f in frames) else 1


if __name__ == "__main__":
    sys.exit(main())
