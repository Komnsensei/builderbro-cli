#!/usr/bin/env python3
"""bro_bridge_test.py — tests for M1's bridge (`§18`, `§19`).

The bridge talks to a real client on a real pty. Almost nothing about *that* can
be unit-tested, and pretending otherwise would be the decoration this repo keeps
calling out. What can be tested is every decision the bridge makes with the
client replaced by a fixture — and those decisions are exactly where a bridge
turns an honest failure into a comfortable one.

Three layers, and the third is the reason the file exists:

1. **The contract.** Frame shapes, the error taxonomy of `§18.8`, capability
   negotiation, the queue's ordering, the reader's watermark, the completion rule.
2. **The lock machine of `§19.4`.** Every verdict, including the two that fail
   safe: `owner_reused` (alive, not the client) is stale *with a reason*, and a
   lock that is present but unreadable is never ours and never absent.
3. **Negative controls that must fail on purpose.**
   - an op before `hello` is `E_PROTOCOL`, `status` included;
   - a foreign lock sends **zero** signals — every classification runs under a
     recording ledger and the list must be empty (`I2`), and no classification
     may spawn;
   - an adopt is read-only: `post:true` on a foreign session is `E_LOCK_FOREIGN`,
     and the writer records that it was never asked to write;
   - a turn that declares no expectation is never reported as verified;
   - an expectation no evidence could satisfy (`ok`) is refused as **vacuous**,
     so the gate cannot be satisfied by declaring nothing in particular;
   - a tool name mismatch is reported with the names that *were* observed, so a
     refusal cannot be mistaken for the thing it was supposed to confirm;
   - an unreadable record is `E_UNREADABLE` with the raw capture attached, never
     a synthesised reply — asserted on a record that is torn, missing, and
     unparseable in turn.

Run: python3 bro_bridge_test.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bro_bridge as bridge_mod
import bro_session_measure as measure

# Epoch-ms ids are the only ordering key in the record, and `§18.6` resolves a
# reply as "the first assistant message at or after the posted epoch". So the
# fixtures stamp ids at *now* (plus a small offset for intra-fixture ordering),
# exactly as the client does: a reply appended after a prompt must never carry a
# timestamp that looks older than the prompt that asked for it.

def mid(role, offset=0):
    return "%s-%d" % (role, int(time.time() * 1000) + offset)


def text_block(content, text_type="text"):
    return {"type": "text", "textType": text_type, "content": content}


def tool_block(name="read_files", input_=None, output="", call_id="call-1"):
    return {"type": "tool", "toolName": name, "input": input_ or {"paths": ["a.py"]},
            "output": output, "toolCallId": call_id}


def ai(offset=3000, text="done", tools=None, complete=True, divider=False):
    """An assistant message in the measured shape (`type: tool`, not `tool-call`)."""
    blocks = []
    if divider:
        blocks.append({"type": "mode-divider"})
    if text is not None:
        blocks.append(text_block(text))
    blocks.extend(tools or [])
    message = {"id": mid("ai", offset), "variant": "ai", "blocks": blocks}
    if complete is not None:
        message["isComplete"] = complete
    return message


def user(offset=0, text="do the thing"):
    return {"id": mid("user", offset), "variant": "user",
            "blocks": [text_block(text)]}


def _stat_line(pid, comm="freebuff", start="3456506", pgrp=None):
    """`/proc/<pid>/stat`; after `)` come fields 3..22, so `tail[2]` is pgrp."""
    tail = ["S", "1", str(pgrp if pgrp is not None else pid)]
    tail += [str(i) for i in range(3, 19)]
    tail += [start]                                     # tail[19] = starttime
    return "%d (%s) %s\n" % (pid, comm, " ".join(tail))


class FakeProc(object):
    """A `/proc` that reports exactly the pids we added, with their command line."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="bro-bridge-proc-")
        self.pids = {}

    def add(self, pid, cmdline="freebuff --cwd /x --trust-agents", start="3456506",
            pgrp=None):
        self.pids[pid] = cmdline
        directory = os.path.join(self.root, str(pid))
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "cmdline"), "wb") as handle:
            handle.write(cmdline.encode("utf-8").replace(b" ", b"\0") + b"\0")
        with open(os.path.join(directory, "stat"), "w", encoding="utf-8") as handle:
            handle.write(_stat_line(pid, start=start, pgrp=pgrp))
        return pid

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class FakeChild(object):
    """A pty child replaced by a callable. `on_write` is where the client would be."""

    def __init__(self, pid=4242, pgid=4242, on_write=None, pane="boot ok\n"):
        self.proc = types.SimpleNamespace(pid=pid)
        self.pgid = pgid
        self.pane = pane
        self.pane_bytes = pane.encode("utf-8")
        self.writes = []
        self.pumped = 0
        self.terminated = False
        self.alive_flag = True
        self.on_write = on_write

    def pump(self, seconds):
        self.pumped += 1
        return self.pane_bytes

    def write(self, text):
        self.writes.append(text)
        if self.on_write is not None:
            self.on_write(text)

    def alive(self):
        return self.alive_flag

    def pane_text(self):
        return self.pane

    def terminate(self, ledger):
        self.terminated = True
        self.alive_flag = False
        ledger.killpg(self.proc.pid, 15)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bro-bridge-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "manicode")
        self.cwd = os.path.join(self.tmp, "project")
        os.makedirs(self.home)
        os.makedirs(self.cwd)
        self.state_dir = os.path.join(self.tmp, "bro")
        self.proc = FakeProc()
        self.addCleanup(self.proc.cleanup)
        self.ledger = bridge_mod.SignalLedger(dry_run=True)

    # ── fixtures ──
    def project_fixture(self, messages, name="2026-09-23T15-00-00.000Z",
                        project_root=None, log_lines=None):
        key = os.path.basename(os.path.realpath(self.cwd))
        chat_dir = os.path.join(self.home, "projects", key, "chats", name)
        os.makedirs(chat_dir, exist_ok=True)
        with open(os.path.join(chat_dir, "chat-meta.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"metadata": {"runState": {"sessionState": {
                "fileContext": {"projectRoot": project_root or self.cwd}}}}}, handle)
        with open(os.path.join(chat_dir, "chat-messages.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(messages, handle)
        with open(os.path.join(chat_dir, "log.jsonl"), "w",
                  encoding="utf-8") as handle:
            for line in (log_lines or []):
                handle.write(json.dumps(line) + "\n")
        return chat_dir

    def record_path(self, chat_dir):
        return os.path.join(chat_dir, "chat-messages.json")

    def append(self, chat_dir, message):
        """Append to the record the way the client does: rewrite, then close."""
        path = self.record_path(chat_dir)
        with open(path, "r", encoding="utf-8") as handle:
            messages = json.load(handle)
        messages.append(message)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(messages, handle)
        return messages

    def write_lock(self, pid, instance="inst-1", home=None):
        path = os.path.join(home or self.home, measure.LOCK_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"pid": pid, "instanceId": instance}, handle)
        return path

    def machine(self, own=None):
        return bridge_mod.LockMachine(self.home, own=own, proc=self.proc.root,
                                      ledger=self.ledger)

    def bridge(self, **kwargs):
        kwargs.setdefault("home", self.home)
        kwargs.setdefault("cwd", self.cwd)
        # `proc` is the `/proc` *root path*, not the fixture object: the reader
        # takes the same seam as everything else in `bro_session_measure`.
        kwargs.setdefault("proc", self.proc.root)
        kwargs.setdefault("ledger", self.ledger)
        kwargs.setdefault("state_dir", self.state_dir)
        kwargs.setdefault("ready_timeout", 0.2)
        kwargs.setdefault("quiet_ms", 0)
        # `inline=True`: one frame in, one frame out, so the tests are about the
        # turn itself rather than about waiting for the worker thread.
        kwargs.setdefault("inline", True)
        instance = bridge_mod.Bridge(**kwargs)
        instance._out = io.StringIO()
        self.addCleanup(instance.release)
        return instance

    def call(self, instance, request):
        """One frame through the same path `serve` uses, with an id defaulted."""
        request = dict(request, v=1, type="req")
        request.setdefault("id", request.get("op"))
        return instance.handle(request)

    def assert_error(self, frame, code):
        """`§18.3`: exactly one `err` per failed `req`, carrying the taxonomy code."""
        self.assertEqual(frame.get("type"), "err", frame)
        self.assertEqual(frame.get("code"), code, frame)
        self.assertFalse(frame["ok"])
        return frame

    def hello(self, instance):
        return self.call(instance, {"id": "h", "op": "hello",
                                    "proto_min": 1, "proto_max": 1})

    def frames(self, instance):
        return [json.loads(line) for line in instance._out.getvalue().splitlines()
                if line.strip()]

    def attached(self, messages=None, own_pid=None, **kwargs):
        """A bridge attached to a session that is *ours* (so `ask` may post)."""
        chat_dir = self.project_fixture(messages if messages is not None
                                        else [user(), ai()])
        pid = self.proc.add(own_pid or 777)
        self.write_lock(pid)
        instance = self.bridge(**kwargs)
        instance.ledger = self.ledger
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: FakeChild(pid=pid, pgid=pid)
        payload = self.call(instance, {"id": "a", "op": "attach", "spawn": False})
        self.assertEqual(payload["type"], "res", payload)
        return instance, chat_dir

    def wait_for(self, predicate, timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()


# ── 1. the frame contract (`§18`) ────────────────────────────────────────────

class ProtocolTest(Base):

    def test_hello_must_be_the_first_frame(self):
        instance = self.bridge()
        for op in ("status", "attach", "ask", "observe", "capture", "stop"):
            frame = self.call(instance, {"id": op, "op": op})
            self.assert_error(frame, "E_PROTOCOL")
            self.assertEqual(frame["message"], "hello must be the first frame")
        self.assertFalse(instance.had_hello)

    def test_hello_negotiates_and_announces_only_what_exists(self):
        instance = self.bridge()
        frame = self.hello(instance)
        self.assertEqual(frame["type"], "res")
        self.assertEqual(frame["v"], 1)
        self.assertEqual(frame["server"], "bro_bridge/1")
        self.assertEqual(frame["platform"], "posix")
        for name in frame["capabilities"]:
            self.assertTrue(hasattr(instance, "op_" + name),
                            "%s announced with no implementation" % name)
        for op in ("new", "interrupt"):
            self.assertNotIn(op, frame["capabilities"])

    def test_hello_twice_is_a_protocol_error(self):
        instance = self.bridge()
        self.hello(instance)
        self.assert_error(self.hello(instance), "E_PROTOCOL")

    def test_no_protocol_overlap_is_refused(self):
        instance = self.bridge()
        frame = self.assert_error(self.call(
            instance, {"id": "h", "op": "hello", "proto_min": 7, "proto_max": 9}),
            "E_PROTOCOL")
        self.assertIn("no protocol overlap", frame["message"])
        self.assertFalse(instance.had_hello)

    def test_non_integer_protocol_bounds_are_refused(self):
        instance = self.bridge()
        self.assert_error(self.call(instance, {"id": "h", "op": "hello",
                                               "proto_min": "one",
                                               "proto_max": "two"}), "E_PROTOCOL")

    def test_unknown_op_lists_the_capabilities(self):
        instance = self.bridge()
        self.hello(instance)
        frame = self.assert_error(self.call(instance, {"id": "x", "op": "interrupt"}),
                                  "E_BAD_OP")
        self.assertEqual(frame["detail"]["capabilities"], bridge_mod.CAPABILITIES)

    def test_a_req_without_an_op_is_a_protocol_error(self):
        instance = self.bridge()
        self.assert_error(self.call(instance, {"id": "x"}), "E_PROTOCOL")

    def test_an_inbound_res_frame_is_refused(self):
        instance = self.bridge()
        self.assert_error(instance.handle({"v": 1, "id": "x", "type": "res",
                                           "op": "status"}), "E_PROTOCOL")

    def test_non_object_frame_is_refused(self):
        instance = self.bridge()
        self.assert_error(instance.handle(["hello"]), "E_PROTOCOL")

    def test_the_error_taxonomy_is_the_spec_table(self):
        # `§18.8`, verbatim. The two polarities that matter are asserted by name.
        self.assertEqual(bridge_mod.ERRORS["E_REFUSED"], (False, False))
        self.assertEqual(bridge_mod.ERRORS["E_QUEUE_FULL"], (True, False))
        self.assertEqual(bridge_mod.ERRORS["E_INTERNAL"], (True, False))
        self.assertEqual(bridge_mod.ERRORS["E_LOCK_FOREIGN"], (True, True))
        self.assertEqual(bridge_mod.ERRORS["E_PROTOCOL"], (False, False))
        self.assertEqual(sorted(bridge_mod.ERRORS), [
            "E_BAD_OP", "E_INTERNAL", "E_LOCK_FOREIGN", "E_LOCK_MALFORMED",
            "E_NO_SESSION", "E_PROJECT_AMBIGUOUS", "E_PROTOCOL", "E_PTY",
            "E_QUEUE_FULL", "E_READY_TIMEOUT", "E_REFUSED", "E_SESSION_DEAD",
            "E_TIMEOUT_STALLED", "E_UNREADABLE"])

    def test_error_frames_carry_the_polarity_for_the_client(self):
        frame = bridge_mod.BridgeError("E_REFUSED", "no", {"verdict": "refused"}).as_dict("t1")
        self.assertEqual(frame["type"], "err")
        self.assertFalse(frame["ok"])
        self.assertFalse(frame["retryable"])
        self.assertFalse(frame["fallback_eligible"])
        self.assertEqual(frame["detail"]["verdict"], "refused")

    def test_an_unknown_code_degrades_to_internal(self):
        error = bridge_mod.BridgeError("E_NOT_A_THING", "x")
        self.assertEqual(error.code, "E_INTERNAL")

    def test_serve_answers_bad_json_and_keeps_the_connection_up(self):
        instance = self.bridge()
        script = io.StringIO("not json\n"
                             + json.dumps({"v": 1, "id": "h", "type": "req",
                                           "op": "hello"}) + "\n"
                             + json.dumps({"v": 1, "id": "s", "type": "req",
                                           "op": "status"}) + "\n")
        instance.serve(stream=script, heartbeat=False)
        frames = self.frames(instance)
        self.assertEqual([f["type"] for f in frames], ["err", "res", "res"])
        self.assertEqual(frames[0]["code"], "E_PROTOCOL")
        self.assertEqual(frames[1]["op"] if "op" in frames[1] else frames[1]["id"], "h")
        self.assertEqual(frames[2]["id"], "s")

    def test_oversized_frames_are_refused_without_killing_the_loop(self):
        instance = self.bridge()
        original = bridge_mod.MAX_FRAME
        bridge_mod.MAX_FRAME = 64
        self.addCleanup(setattr, bridge_mod, "MAX_FRAME", original)
        script = io.StringIO(json.dumps({"op": "hello", "pad": "x" * 200}) + "\n"
                             + json.dumps({"v": 1, "id": "h", "type": "req",
                                           "op": "hello"}) + "\n")
        instance.serve(stream=script, heartbeat=False)
        frames = self.frames(instance)
        self.assertEqual(frames[0]["code"], "E_PROTOCOL")
        self.assertIn("exceeds", frames[0]["message"])
        self.assertEqual(frames[1]["type"], "res")

    def test_blank_lines_are_ignored(self):
        instance = self.bridge()
        script = io.StringIO("\n   \n" + json.dumps({"v": 1, "id": "h", "type": "req",
                                                    "op": "hello"}) + "\n")
        instance.serve(stream=script, heartbeat=False)
        self.assertEqual(len(self.frames(instance)), 1)

    def test_shutdown_releases_and_ends_the_loop(self):
        chat_dir = self.project_fixture([user(), ai()])
        pid = self.proc.add(888)
        self.write_lock(pid)
        child = FakeChild(pid=pid, pgid=pid)
        instance = self.bridge()
        instance.lock_machine.own["pid"] = pid
        instance.handle({"v": 1, "id": "h", "type": "req", "op": "hello"})
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        script = io.StringIO(json.dumps({"v": 1, "id": "q", "type": "req",
                                         "op": "shutdown"}) + "\n"
                             + json.dumps({"v": 1, "id": "never", "type": "req",
                                           "op": "status"}) + "\n")
        instance.serve(stream=script, heartbeat=False)
        frames = self.frames(instance)
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0]["stopping"])
        self.assertTrue(child.terminated)
        self.assertTrue(instance._stop.is_set())

    def test_heartbeats_are_emitted_and_are_advisory(self):
        original = bridge_mod.HEARTBEAT_S
        bridge_mod.HEARTBEAT_S = 0.01
        self.addCleanup(setattr, bridge_mod, "HEARTBEAT_S", original)
        instance = self.bridge()
        self.hello(instance)
        thread = threading.Thread(target=instance._heartbeat, daemon=True)
        thread.start()
        time.sleep(0.15)
        instance._stop.set()
        thread.join(timeout=2)
        beats = [f for f in self.frames(instance) if f.get("op") == "heartbeat"]
        self.assertGreaterEqual(len(beats), 3)
        self.assertIn("queue_depth", beats[0])
        self.assertNotIn("id", beats[0])

    def test_an_op_that_raises_unexpectedly_is_an_internal_error_not_a_drop(self):
        instance = self.bridge()
        self.hello(instance)

        def boom(_req):
            raise RuntimeError("kaboom")

        instance.op_status = boom
        script = io.StringIO(json.dumps({"v": 1, "id": "s", "type": "req",
                                         "op": "status"}) + "\n")
        instance.serve(stream=script, heartbeat=False)
        frames = self.frames(instance)
        self.assertEqual(frames[0]["code"], "E_INTERNAL")
        self.assertIn("kaboom", frames[0]["message"])
        self.assertFalse(frames[0]["fallback_eligible"])


# ── 2. the lock machine (`§19.4`) ────────────────────────────────────────────

class LockTest(Base):

    def test_absent_when_there_is_no_lock(self):
        state, detail = self.machine().decide()
        self.assertEqual(state, bridge_mod.ABSENT)
        self.assertEqual(detail["lock_verdict"], "absent")
        self.assertEqual(self.ledger.as_list(), [])

    def test_our_own_pid_is_attached_and_never_spawns(self):
        pid = self.proc.add(1234)
        self.write_lock(pid)
        machine = bridge_mod.LockMachine(self.home, own={"pid": pid},
                                         proc=self.proc.root, ledger=self.ledger)
        state, detail = machine.decide()
        self.assertEqual(state, bridge_mod.ATTACHED)
        self.assertTrue(detail["ours"])
        self.assertEqual(self.ledger.as_list(), [])

    def test_the_wrapper_to_core_group_counts_as_ours(self):
        # Measured on this client: the lock's pid is the core, our child is the
        # wrapper — same process group, different pid.
        core = self.proc.add(2001, pgrp=2000)
        self.write_lock(core)
        machine = bridge_mod.LockMachine(self.home, own={"pid": 2000, "pgid": 2000},
                                         proc=self.proc.root, ledger=self.ledger)
        state, detail = machine.decide()
        self.assertEqual(state, bridge_mod.ATTACHED)
        self.assertTrue(detail["ours"])

    def test_a_matching_instance_id_counts_as_ours(self):
        pid = self.proc.add(3001)
        self.write_lock(pid, instance="same-instance")
        machine = bridge_mod.LockMachine(self.home, own={"instanceId": "same-instance"},
                                         proc=self.proc.root, ledger=self.ledger)
        state, detail = machine.decide()
        self.assertEqual(state, bridge_mod.ATTACHED)
        self.assertTrue(detail["ours"])

    def test_a_live_foreign_owner_is_foreign_and_never_signalled(self):
        pid = self.proc.add(4001)
        self.write_lock(pid)
        machine = bridge_mod.LockMachine(self.home, own={"pid": 9},
                                         proc=self.proc.root, ledger=self.ledger)
        state, detail = machine.decide()
        self.assertEqual(state, bridge_mod.FOREIGN)
        self.assertFalse(detail["ours"])
        self.assertTrue(detail["pid_alive"])
        self.assertEqual(self.ledger.as_list(), [], "a foreign lock was signalled")
        self.assertEqual([e["event"] for e in machine.events], ["refuse"])

    def test_a_dead_owner_is_reclaimable_with_the_reason(self):
        self.write_lock(999999)
        machine = self.machine()
        state, detail = machine.decide()
        self.assertEqual(state, bridge_mod.ABSENT)
        self.assertEqual(detail["stale_reason"], "owner_dead")
        self.assertEqual(detail["reason_code"], "owner_dead")
        self.assertEqual(machine.events[-1]["event"], "reclaimable")

    def test_a_reused_pid_is_reclaimable_with_the_reason(self):
        pid = self.proc.add(5001, cmdline="python3 something_else.py")
        self.write_lock(pid)
        machine = self.machine()
        state, detail = machine.decide()
        self.assertEqual(state, bridge_mod.ABSENT)
        self.assertEqual(detail["stale_reason"], "owner_reused")
        self.assertEqual(detail["reason_code"], "pid_reused")
        self.assertIn("something_else", detail["reason"])

    def test_an_unreadable_lock_is_fatal_not_absent(self):
        path = self.write_lock(6001)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        machine = self.machine()
        state, detail = machine.decide()
        self.assertEqual(state, bridge_mod.FOREIGN, "an unreadable lock licensed a spawn")
        self.assertEqual(detail["code"], "E_LOCK_MALFORMED")
        self.assertFalse(detail["ours"])
        self.assertEqual(self.ledger.as_list(), [])

    def test_a_directory_in_place_of_the_lock_is_fatal_not_absent(self):
        os.makedirs(os.path.join(self.home, measure.LOCK_NAME))
        state, detail = self.machine().decide()
        self.assertEqual(state, bridge_mod.FOREIGN)
        self.assertEqual(detail["code"], "E_LOCK_MALFORMED")

    def test_no_lock_verdict_ever_signals(self):
        # Every verdict, one after another, under a recording ledger: `I2` says
        # the foreign path sends nothing, and this is the assertion for it.
        self.write_lock(7001)
        self.proc.add(7001)
        self.machine().decide()                                     # live, foreign
        os.remove(os.path.join(self.home, measure.LOCK_NAME))
        self.machine().decide()                                     # absent
        self.write_lock(999998)
        self.machine().decide()                                     # owner_dead
        self.proc.add(7002, cmdline="node something.js")
        self.write_lock(7002)
        self.machine().decide()                                     # owner_reused
        with open(os.path.join(self.home, measure.LOCK_NAME), "w") as handle:
            handle.write("[]")
        self.machine().decide()                                     # malformed
        self.assertEqual(self.ledger.as_list(), [])

    def test_the_decision_never_writes_the_lock(self):
        pid = self.proc.add(7003)
        path = self.write_lock(pid)
        with open(path, "rb") as handle:
            before = handle.read()
        self.machine().decide()
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)


# ── 3. attach, adopt, refusal ────────────────────────────────────────────────

class AttachTest(Base):

    def test_a_foreign_lock_refuses_to_attach_and_offers_the_adopt(self):
        pid = self.proc.add(8001)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        frame = self.assert_error(self.call(instance, {"id": "a", "op": "attach"}),
                                  "E_LOCK_FOREIGN")
        self.assertTrue(frame["retryable"])
        self.assertTrue(frame["fallback_eligible"])
        self.assertEqual(frame["detail"]["owner"]["pid"], pid)
        self.assertTrue(frame["detail"]["read_only_attach"])
        self.assertEqual(self.ledger.as_list(), [])

    def test_an_unreadable_lock_refuses_to_spawn(self):
        path = self.write_lock(8002)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not a lock")
        instance = self.bridge()
        self.hello(instance)
        spawned = []
        instance.spawner = lambda: spawned.append(True)
        self.assert_error(self.call(instance, {"id": "a", "op": "attach"}),
                          "E_LOCK_MALFORMED")
        self.assertEqual(spawned, [], "spawned over an unreadable lock")

    def test_adopt_attaches_read_only_and_says_whose_it_is(self):
        pid = self.proc.add(8003)
        self.write_lock(pid)
        self.project_fixture([user(), ai()])
        instance = self.bridge()
        self.hello(instance)
        payload = self.call(instance, {"id": "a", "op": "attach", "adopt": True})
        self.assertEqual(payload["type"], "res", payload)
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["owner"]["pid"], pid)
        self.assertFalse(payload["owner"]["ours"])
        self.assertEqual(payload["project"], os.path.basename(self.cwd))
        self.assertTrue(any("read-only adopt" in w for w in payload["warnings"]))
        self.assertEqual(self.ledger.as_list(), [])

    def test_an_adopted_session_refuses_to_post(self):
        pid = self.proc.add(8004)
        self.write_lock(pid)
        self.project_fixture([user(), ai()])
        instance = self.bridge()
        self.hello(instance)
        self.call(instance, {"id": "a", "op": "attach", "adopt": True})
        frame = self.assert_error(self.call(instance, {"id": "k", "op": "ask",
                                                      "turn_id": "t1",
                                                      "task": "do it",
                                                      "post": True}),
                                  "E_LOCK_FOREIGN")
        self.assertEqual(frame["detail"]["asked"], "post")
        self.assertIn("observe", frame["detail"]["instead"])

    def test_posting_defaults_off_for_a_session_we_do_not_own(self):
        pid = self.proc.add(8005)
        self.write_lock(pid)
        self.project_fixture([user(), ai()])
        instance = self.bridge()
        self.hello(instance)
        self.call(instance, {"id": "a", "op": "attach", "adopt": True})
        # A task is not required to observe: nothing is written. And `post` is
        # omitted on purpose — the default for a foreign session must be `False`.
        frame = self.call(instance, {"id": "o", "op": "observe", "turn_id": "t2"})
        self.assertEqual(frame["type"], "res", frame)
        self.assertEqual(frame["evidence"]["mode"], "observe")
        self.assertEqual(frame["reply"]["text"], "done")

    def test_the_attach_payload_cross_checks_the_records_own_project_root(self):
        pid = self.proc.add(8006)
        self.write_lock(pid)
        other = os.path.join(self.tmp, "somewhere-else")
        self.project_fixture([user(), ai()], project_root=other)
        instance = self.bridge()
        self.hello(instance)
        payload = self.call(instance, {"id": "a", "op": "attach", "adopt": True})
        self.assertTrue(any("projectRoot" in w for w in payload["warnings"]),
                        payload["warnings"])

    def test_attach_without_spawn_and_without_a_session_is_no_session(self):
        instance = self.bridge()
        self.hello(instance)
        self.assert_error(self.call(instance, {"id": "a", "op": "attach",
                                               "spawn": False}), "E_NO_SESSION")

    def test_ask_before_attach_is_no_session(self):
        instance = self.bridge()
        self.hello(instance)
        frame = self.assert_error(self.call(instance, {"id": "k", "op": "ask",
                                                      "turn_id": "t", "task": "x"}),
                                  "E_NO_SESSION")
        self.assertTrue(frame["fallback_eligible"])

    def test_status_reports_the_lock_without_a_session(self):
        pid = self.proc.add(8007)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        payload = self.call(instance, {"id": "s", "op": "status"})
        self.assertEqual(payload["lock"]["state"], bridge_mod.FOREIGN)
        self.assertIsNone(payload["session"])
        self.assertIsNone(payload["turn"])
        self.assertEqual(payload["queue_depth"], 0)

    def test_capture_without_a_spawned_child_is_no_session(self):
        pid = self.proc.add(8008)
        self.write_lock(pid)
        self.project_fixture([user(), ai()])
        instance = self.bridge()
        self.hello(instance)
        self.call(instance, {"id": "a", "op": "attach", "adopt": True})
        self.assert_error(self.call(instance, {"id": "c", "op": "capture"}),
                          "E_NO_SESSION")

    def test_spawning_is_only_reached_from_absent(self):
        # A live foreign lock plus `spawn: true` must still refuse: the spawn
        # branch is reached from ABSENT, never from FOREIGN.
        pid = self.proc.add(8009)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        spawned = []
        instance.spawner = lambda: spawned.append(True)
        self.assert_error(self.call(instance, {"id": "a", "op": "attach",
                                               "spawn": True}), "E_LOCK_FOREIGN")
        self.assertEqual(spawned, [])


# ── 4. the spawn path ────────────────────────────────────────────────────────

class SpawnTest(Base):

    def ready_tree(self, reply=True):
        calls = []

        def on_write(_text):
            if reply:
                self.append(self.chat_dir, ai(offset=4000, text="posted ok"))

        child = FakeChild(pid=9100, pgid=9100, on_write=on_write)
        self.chat_dir = self.project_fixture([user()])
        return child, calls

    def spawn_bridge(self, child, lock_on_spawn=False, **kwargs):
        """A bridge whose spawner returns `child`.

        With `lock_on_spawn`, the spawner writes the child's lock the way the real
        client does while it boots — which is what makes the lock "ours" by the
        time the ready check runs. Without it, the lock is not attributable and
        the attach must say so rather than claim the session.
        """
        instance = self.bridge(**kwargs)
        self.hello(instance)
        instance.lock_machine.own["pid"] = child.proc.pid

        def spawner():
            if lock_on_spawn:
                self.write_lock(child.proc.pid)
            return child

        instance.spawner = spawner
        return instance

    def test_a_spawn_that_never_paints_is_a_ready_timeout(self):
        child = FakeChild(pid=9101, pgid=9101, pane="")
        self.project_fixture([user(), ai()])
        instance = self.spawn_bridge(child, ready_timeout=0.05)
        frame = self.assert_error(self.call(instance, {"id": "a", "op": "attach"}),
                                  "E_READY_TIMEOUT")
        self.assertTrue(frame["fallback_eligible"])
        self.assertFalse(frame["detail"]["painted"])
        self.assertTrue(frame["detail"]["record_resolved"])
        self.assertTrue(child.terminated, "a half-started client was left running")
        self.assertEqual(self.ledger.as_list()[0]["pid"], 9101)

    def test_a_spawn_that_paints_but_writes_no_record_is_a_ready_timeout(self):
        child = FakeChild(pid=9102, pgid=9102)
        instance = self.spawn_bridge(child, ready_timeout=0.05)
        frame = self.assert_error(self.call(instance, {"id": "a", "op": "attach"}),
                                  "E_READY_TIMEOUT")
        self.assertTrue(frame["detail"]["painted"])
        self.assertFalse(frame["detail"]["record_resolved"])

    def test_a_spawn_that_exits_early_is_session_dead(self):
        child = FakeChild(pid=9103, pgid=9103, pane="")
        child.alive_flag = False
        self.project_fixture([user(), ai()])
        instance = self.spawn_bridge(child, ready_timeout=5)
        self.assert_error(self.call(instance, {"id": "a", "op": "attach"}),
                          "E_SESSION_DEAD")

    def test_a_ready_spawn_attaches_and_records_the_boot(self):
        child = FakeChild(pid=9104, pgid=9104)
        self.project_fixture([user(), ai()])
        instance = self.spawn_bridge(child, lock_on_spawn=True, ready_timeout=2)
        self.proc.add(child.proc.pid, pgrp=child.pgid)
        payload = self.call(instance, {"id": "a", "op": "attach"})
        self.assertEqual(payload["type"], "res", payload)
        self.assertEqual(payload["state"], bridge_mod.ATTACHED)
        self.assertFalse(payload["read_only"])
        self.assertIsNotNone(payload["boot_seconds"])
        events = [e["event"] for e in instance.lock_machine.events]
        self.assertIn("spawn", events)
        self.assertIn("ready", events)
        self.assertTrue(os.path.isfile(os.path.join(self.state_dir,
                                                    bridge_mod.SESSION_FILE)))

    def test_a_spawn_whose_lock_is_not_ours_warns_instead_of_claiming_it(self):
        child = FakeChild(pid=9105, pgid=9105)
        self.project_fixture([user(), ai()])
        instance = self.spawn_bridge(child, ready_timeout=2)
        payload = self.call(instance, {"id": "a", "op": "attach"})
        self.assertEqual(payload["type"], "res", payload)
        self.assertTrue(any("not attributable" in w for w in payload["warnings"]))
        self.assertTrue(payload["read_only"])


# ── 5. the turn: completion, verification, refusal ───────────────────────────

class TurnTest(Base):

    def test_ask_requires_a_turn_id_and_a_task_to_post(self):
        instance, _chat = self.attached()
        frame = self.assert_error(self.call(instance, {"op": "ask", "task": "x"}),
                                  "E_PROTOCOL")
        self.assertIn("turn_id", frame["message"])
        for request in ({"op": "ask", "turn_id": "t"},          # no task
                        {"op": "ask", "turn_id": "t", "task": "   "}):
            frame = self.assert_error(self.call(instance, request), "E_PROTOCOL")
            self.assertIn("non-empty task", frame["message"])
        # …but observing needs no task, because observing writes nothing.
        frame = self.call(instance, {"op": "observe", "turn_id": "t"})
        self.assertEqual(frame["type"], "res", frame)

    def test_a_posted_turn_round_trips_and_is_gated(self):
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7200)
        self.write_lock(pid)

        def on_write(_text):
            self.append(chat_dir, ai(offset=4000, text="the file says hello",
                                     tools=[tool_block(
                                         output="alpha beta gamma",
                                         input_={"paths": [os.path.join(self.cwd,
                                                                        "a.py")]})]))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        frame = self.call(instance, {"id": "k", "op": "ask", "turn_id": "t1",
                                     "task": "read the file",
                                     "expect": {"tool": "read_files",
                                                "check": "contains:beta"}})
        self.assertEqual(frame["type"], "res", frame)
        self.assertEqual(frame["reply"]["text"], "the file says hello")
        self.assertEqual(frame["evidence"]["mode"], "post")
        self.assertTrue(frame["evidence"]["declared"])
        self.assertTrue(frame["evidence"]["ok"])
        # No `arg`, so no file to re-read: the honest band is `observed`, and the
        # `reproduced` check is recorded as never applicable rather than failed.
        self.assertEqual(frame["evidence"]["verified"], "observed")
        reproduced = [c for c in frame["evidence"]["checks"]
                      if c["check"] == "reproduced"]
        self.assertFalse(reproduced and reproduced[0].get("ok") is True)
        self.assertEqual(len(child.writes), 1)
        self.assertIn(bridge_mod.PROMPT_OPEN, child.writes[0])
        self.assertIn(bridge_mod.PROMPT_CLOSE, child.writes[0])
        self.assertIn("read the file", child.writes[0])

    def test_a_reply_with_no_declared_expectation_is_not_verified(self):
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7201)
        self.write_lock(pid)

        def on_write(_text):
            self.append(chat_dir, ai(offset=4000, text="all done trust me"))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x"})
        self.assertEqual(frame["type"], "res", frame)
        self.assertEqual(frame["evidence"]["verified"], "not_declared")
        self.assertFalse(frame["evidence"]["declared"])
        self.assertIsNone(frame["evidence"]["level"])
        self.assertIn("nothing about it was verified",
                      frame["evidence"]["bounce_note"])

    def test_a_claim_about_a_tool_that_did_not_run_is_refused(self):
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7202)
        self.write_lock(pid)

        def on_write(_text):
            self.append(chat_dir, ai(offset=4000, text="looked at it",
                                     tools=[tool_block(name="run_terminal_command",
                                                       output="ok")]))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x",
                                 "expect": {"tool": "read_files",
                                            "check": "contains:ok"}})
        self.assertEqual(frame["type"], "err", frame)
        self.assertEqual(frame["code"], "E_REFUSED")
        self.assertFalse(frame["fallback_eligible"])
        self.assertIn("run_terminal_command", frame["detail"]["bounce_note"])
        self.assertEqual(frame["detail"]["tool_names"], ["run_terminal_command"])

    def test_a_vacuous_expectation_cannot_confirm_anything(self):
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7203)
        self.write_lock(pid)

        def on_write(_text):
            self.append(chat_dir, ai(offset=4000, text="ok",
                                     tools=[tool_block(output="ok")]))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x",
                                 "expect": {"tool": "read_files", "check": "ok"}})
        self.assertEqual(frame["type"], "err", frame)
        self.assertEqual(frame["code"], "E_REFUSED")
        self.assertEqual(frame["detail"]["verdict"], "refused")
        self.assertIn("not_vacuous", frame["detail"]["bounce_note"])

    def test_a_reader_claim_is_re_observed_from_the_file_itself(self):
        target = os.path.join(self.cwd, "note.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("beta is in here\n")
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7204)
        self.write_lock(pid)

        def on_write(_text):
            self.append(chat_dir, ai(offset=4000, text="read it", tools=[
                tool_block(input_={"paths": [target]},
                           output="1→beta is in here")]))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x",
                                 "expect": {"tool": "read_files", "arg": target,
                                            "check": "contains:beta"}})
        self.assertEqual(frame["type"], "res", frame)
        self.assertEqual(frame["evidence"]["level"], "invariant",
                         "our own read of the file did not promote the claim")
        self.assertIn("reproduced", frame["evidence"]["how_verified"])

    def test_a_reader_claim_the_file_does_not_support_is_refused(self):
        target = os.path.join(self.cwd, "note.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("nothing to see\n")
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7205)
        self.write_lock(pid)

        def on_write(_text):
            self.append(chat_dir, ai(offset=4000, text="read it", tools=[
                tool_block(input_={"paths": [target]}, output="gamma is here")]))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x",
                                 "expect": {"tool": "read_files", "arg": target,
                                            "check": "contains:gamma"}})
        self.assertEqual(frame["type"], "err", frame)
        self.assertEqual(frame["code"], "E_REFUSED")
        self.assertIn("reproduced", frame["detail"]["bounce_note"])

    def test_a_failed_tool_output_is_never_a_verified_observation(self):
        import loop_guard
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7206)
        self.write_lock(pid)

        def on_write(_text):
            self.append(chat_dir, ai(offset=4000, text="it failed", tools=[
                tool_block(output=loop_guard.fail("read_files exploded"))]))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x",
                                 "expect": {"tool": "read_files",
                                            "check": "contains:exploded"}})
        self.assertEqual(frame["type"], "err", frame)
        self.assertIn("not_failure", frame["detail"]["bounce_note"])

    def test_an_idempotent_retry_returns_the_record_and_posts_nothing(self):
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7207)
        self.write_lock(pid)
        writes = []

        def on_write(text):
            writes.append(text)
            self.append(chat_dir, ai(offset=4000, text="once"))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = child
        request = {"v": 1, "id": "k", "type": "req", "op": "ask", "turn_id": "t1",
                   "task": "x"}
        first = instance.handle(request)
        second = instance.handle(dict(request, id="k2"))
        self.assertEqual(first["reply"]["text"], "once")
        self.assertEqual(second["reply"]["text"], "once")
        self.assertEqual(len(writes), 1, "the retry posted a second prompt")

    def test_an_observe_turn_never_writes_and_never_returns_the_in_flight_turn(self):
        # The last user prompt has an answer, and after it an unfinished ai
        # message is being written. Observe must resolve the *settled* one.
        chat_dir = self.project_fixture([user(), ai(offset=100, text="first answer"),
                                         user(offset=200), ai(offset=300, text=None,
                                                              complete=None)])
        pid = self.proc.add(7208)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        target = os.path.join(self.cwd, "obs.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("observed delta\n")
        self.append(chat_dir, ai(offset=400, text="second answer",
                                 tools=[tool_block(input_={"paths": [target]},
                                                   output="delta seen")]))
        frame = instance.handle({"v": 1, "id": "o", "type": "req", "op": "observe",
                                 "turn_id": "obs-1",
                                 "expect": {"tool": "read_files", "arg": target,
                                            "check": "contains:delta"}})
        self.assertEqual(frame["type"], "res", frame)
        self.assertEqual(frame["evidence"]["mode"], "observe")
        self.assertEqual(frame["reply"]["text"], "second answer")
        self.assertNotEqual(frame["reply"]["text"], "first answer")
        self.assertIsNone(instance.session.child)

    def test_an_unreadable_record_is_reported_with_the_raw_capture(self):
        chat_dir = self.project_fixture([user(), ai()])
        pid = self.proc.add(7209)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = FakeChild(pid=pid, pgid=pid, pane="pane says hi")
        os.remove(self.record_path(chat_dir))          # the record is gone
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x"})
        self.assertEqual(frame["type"], "err", frame)
        self.assertEqual(frame["code"], "E_UNREADABLE")
        self.assertIn("raw_capture", frame["detail"])
        self.assertEqual(frame["detail"]["raw_capture"], "pane says hi")

    def test_a_torn_record_is_unreadable_rather_than_waited_on_forever(self):
        chat_dir = self.project_fixture([user(), ai()])
        pid = self.proc.add(7210)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        instance.session.child = FakeChild(pid=pid, pgid=pid, pane="torn pane")
        with open(self.record_path(chat_dir), "w", encoding="utf-8") as handle:
            handle.write("[{")                              # mid-rewrite, no `]`
        original = bridge_mod.TORN_LIMIT
        bridge_mod.TORN_LIMIT = 1
        bridge_mod.POLL_S = 0.01
        self.addCleanup(setattr, bridge_mod, "TORN_LIMIT", original)
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x"})
        self.assertEqual(frame["type"], "err", frame)
        self.assertEqual(frame["code"], "E_UNREADABLE")
        self.assertIn("mid-rewrite", frame["message"])

    def test_a_turn_whose_session_died_is_session_dead(self):
        chat_dir = self.project_fixture([user(), ai()])
        pid = self.proc.add(7211)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        child = FakeChild(pid=pid, pgid=pid)
        child.alive_flag = False
        instance.session.child = child
        frame = instance.handle({"v": 1, "id": "k", "type": "req", "op": "ask",
                                 "turn_id": "t1", "task": "x"})
        self.assertEqual(frame["type"], "err", frame)
        self.assertEqual(frame["code"], "E_SESSION_DEAD")

    def test_a_session_marked_over_refuses_to_post(self):
        # The marker must be *newer* than the record's last write or the reader
        # treats it as stale (M0's fix: progress beats a marker). A future
        # timestamp is the deterministic way to say "nothing happened since".
        chat_dir = self.project_fixture(
            [user(), ai()],
            log_lines=[{"level": "info", "msg": measure.SESS_OVER,
                        "timestamp": "2099-01-01T00:00:00.000Z"}])
        pid = self.proc.add(7212)
        self.write_lock(pid)
        instance = self.bridge()
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.handle({"v": 1, "id": "a", "type": "req", "op": "attach",
                         "spawn": False})
        self.assertEqual(instance.session.axis, bridge_mod.SESSION_OVER)
        instance.session.child = FakeChild(pid=pid, pgid=pid)
        frame = self.assert_error(self.call(instance, {"id": "k", "op": "ask",
                                                      "turn_id": "t1",
                                                      "task": "x"}),
                                  "E_SESSION_DEAD")
        self.assertIn("observe", frame["detail"]["instead"])


# ── 6. the queue (`§18.5`) ───────────────────────────────────────────────────

class QueueTest(Base):

    def test_a_full_queue_is_refused_and_is_not_fallback_eligible(self):
        instance, _chat = self.attached(inline=False)
        original = bridge_mod.QUEUE_MAX
        bridge_mod.QUEUE_MAX = 1
        self.addCleanup(setattr, bridge_mod, "QUEUE_MAX", original)
        instance._queue.put((1, 0, {"turn_id": "filler"}))
        frame = self.assert_error(self.call(instance, {"id": "k", "op": "ask",
                                                      "turn_id": "t", "task": "x"}),
                                  "E_QUEUE_FULL")
        self.assertFalse(frame["fallback_eligible"],
                         "a full bridge was reported as the brain's problem")
        self.assertIsNone(instance._worker, "a refused turn still started the worker")

    def test_cli_work_is_served_ahead_of_loop_work(self):
        instance, _chat = self.attached()
        instance._queue.put((1, 1, {"turn_id": "loop-1", "priority": "loop"}))
        instance._queue.put((0, 2, {"turn_id": "cli-1", "priority": "cli"}))
        order = [instance._queue.get()[2]["turn_id"] for _ in range(2)]
        self.assertEqual(order, ["cli-1", "loop-1"])

    def test_two_asks_are_serialized_and_both_answered(self):
        chat_dir = self.project_fixture([user()])
        pid = self.proc.add(7300)
        self.write_lock(pid)
        posted = []

        def on_write(text):
            posted.append(text)
            self.append(chat_dir, ai(text="answer %d" % len(posted)))

        child = FakeChild(pid=pid, pgid=pid, on_write=on_write)
        # `inline=False`: this is the queueing path, where the answer arrives as a
        # `res` frame from the worker rather than from `handle`.
        instance = self.bridge(inline=False)
        self.hello(instance)
        instance.lock_machine.own["pid"] = pid
        instance.spawner = lambda: child
        self.call(instance, {"id": "a", "op": "attach", "spawn": False})
        instance.session.child = child
        first = self.call(instance, {"id": "k1", "op": "ask", "turn_id": "t1",
                                     "task": "one"})
        second = self.call(instance, {"id": "k2", "op": "ask", "turn_id": "t2",
                                      "task": "two"})
        self.assertEqual(first["type"], "evt")
        self.assertEqual(first["op"], "accepted")
        self.assertEqual(first["turn_id"], "t1")
        self.assertEqual(second["type"], "evt")
        self.assertTrue(self.wait_for(lambda: len(posted) >= 2),
                        "the queued turn was never served")
        self.assertTrue(self.wait_for(
            lambda: len([f for f in self.frames(instance)
                         if f.get("type") == "res"]) >= 2),
            "a queued turn got no response frame")
        answers = [f for f in self.frames(instance) if f.get("type") == "res"
                   and "reply" in f]
        self.assertEqual([f["reply"]["text"] for f in answers],
                         ["answer 1", "answer 2"])
        self.assertEqual(len(posted), 2, "prompts interleaved")
        queued = [f for f in self.frames(instance) if f.get("op") == "queued"]
        self.assertTrue(queued)


# ── 7. the record reader as the bridge uses it ───────────────────────────────

class RecordTest(Base):

    def test_a_settled_turn_resolves_and_the_in_flight_one_is_re_read(self):
        chat_dir = self.project_fixture([user(), ai(offset=100, text="first")])
        instance, _ = None, None
        session = bridge_mod.Session(self.home, self.cwd, bridge_mod.ATTACHED,
                                     chat_dir=chat_dir, session_id="x", ours=True)
        status, _messages, watermark, _detail = session.read()
        self.assertEqual(status, "initial")
        settled_bytes = watermark.prefix_bytes
        # The in-flight message grows: the settled boundary must not move.
        self.append(chat_dir, ai(offset=200, text=None, complete=None))
        status, _messages, watermark2, _detail = session.read()
        self.assertEqual(status, "delta")
        self.assertEqual(watermark2.prefix_bytes, settled_bytes,
                         "an unfinished message moved the watermark")
        self.append(chat_dir, ai(offset=200, text="second", complete=None))
        status, _messages, watermark3, _detail = session.read()
        self.assertEqual(status, "delta")
        self.assertEqual(watermark3.prefix_bytes, settled_bytes)
        self.append(chat_dir, ai(offset=200, text="second", complete=True))
        status, _messages, watermark4, _detail = session.read()
        self.assertEqual(status, "delta")
        self.assertGreater(watermark4.prefix_bytes, settled_bytes,
                           "a settled message did not advance the watermark")

    def test_a_prefix_that_moves_is_a_resync_and_is_counted(self):
        chat_dir = self.project_fixture([user(), ai(offset=100, text="first")])
        session = bridge_mod.Session(self.home, self.cwd, bridge_mod.ATTACHED,
                                     chat_dir=chat_dir, session_id="x", ours=True)
        session.read()
        path = self.record_path(chat_dir)
        with open(path, "rb") as handle:
            raw = handle.read()
        with open(path, "wb") as handle:      # rewrite the settled region in place
            handle.write(raw.replace(b"first", b"FIRST"))
        status, _messages, _watermark, detail = session.read()
        self.assertEqual(status, "resync")
        self.assertIn("prefix hash moved", detail["resync_reason"])
        self.assertEqual(session.resyncs, 1)

    def test_a_reply_in_the_same_millisecond_is_still_after_the_prompt(self):
        # Ids are stamped to the millisecond, so a reply can share the prompt's
        # millisecond. Position is then the only signal — and without it the turn
        # would resolve to whatever assistant message happened to be in the record
        # when the prompt was posted, which is how a bridge reports the previous
        # answer as this one's.
        epoch = int(time.time() * 1000)
        earlier = {"id": "ai-%d" % epoch, "variant": "ai", "isComplete": True,
                   "blocks": [text_block("the earlier answer")]}
        answering = {"id": "ai-%d" % epoch, "variant": "ai", "isComplete": True,
                     "blocks": [text_block("the answer to this prompt")]}
        messages = [earlier, answering]
        _verdict, reply, _detail = measure.resolve_turn(messages, epoch, quiet_ms=0)
        self.assertEqual(reply["text"], "the earlier answer")
        _verdict, reply, detail = measure.resolve_turn(messages, epoch, quiet_ms=0,
                                                       posted_index=1)
        self.assertEqual(reply["text"], "the answer to this prompt")
        self.assertEqual(detail["posted_index"], 1)

    def test_observe_prefers_the_last_settled_turn_over_the_in_flight_one(self):
        # The ids are captured, never recomputed: `mid()` stamps the current
        # millisecond, so asking for the "same" id twice is two different ids.
        q1 = user(offset=0, text="q1")
        a1 = ai(offset=100, text="a1")
        q2 = user(offset=200, text="q2")
        pending = ai(offset=300, text=None, complete=None)
        epoch, prompt_id, verdict, reply, detail = bridge_mod.resolve_last_turn(
            [q1, a1, q2, pending])
        self.assertEqual(verdict, "complete")
        self.assertEqual(reply["text"], "a1")
        self.assertEqual(prompt_id, q1["id"])
        self.assertTrue(detail["in_flight"], "a mid-turn session read as settled")
        self.assertEqual(detail["newer_prompt_pending"], q2["id"])

    def test_observe_never_answers_with_a_turn_that_has_not_finished(self):
        messages = [user(offset=0, text="q1"), ai(offset=100, text=None,
                                                   complete=None)]
        epoch, _prompt_id, verdict, reply, detail = bridge_mod.resolve_last_turn(messages)
        self.assertEqual(verdict, "streaming")
        self.assertIsNone(reply)
        self.assertIsNotNone(epoch)
        self.assertIn("no prompt in this record has a finished reply",
                      detail["reason"])

    def test_observe_reports_unreadable_when_there_is_no_user_turn(self):
        epoch, prompt_id, verdict, reply, detail = bridge_mod.resolve_last_turn(
            [ai(offset=100, text="hello")])
        self.assertIsNone(epoch)
        self.assertEqual(verdict, "unreadable")
        self.assertIn("no user message", detail["reason"])

    def test_session_usable_gates_over_but_reports_reconnecting(self):
        chat_dir = self.project_fixture(
            [user(), ai()],
            log_lines=[{"level": "info", "msg": measure.SESS_RECONNECTED,
                        "timestamp": "2026-09-23T10:00:00.000Z"}])
        session = bridge_mod.Session(self.home, self.cwd, bridge_mod.ATTACHED,
                                     chat_dir=chat_dir, session_id="x", ours=True)
        axis, _detail = session.session_axis_now()
        self.assertEqual(axis, bridge_mod.RECONNECTING_AXIS)
        self.assertTrue(session.usable(),
                        "a measured reconnecting axis was treated as fatal")

    def test_capture_marks_the_echo_of_our_own_prompt(self):
        instance, _chat = self.attached()
        instance.session.child = FakeChild(pid=99, pgid=99)
        frame = instance.handle({"v": 1, "id": "c", "type": "req", "op": "capture"})
        self.assertFalse(frame["echo"])
        instance.session.child.pane = "%s do it %s\n" % (bridge_mod.PROMPT_OPEN,
                                                         bridge_mod.PROMPT_CLOSE)
        frame = instance.handle({"v": 1, "id": "c2", "type": "req", "op": "capture"})
        self.assertTrue(frame["echo"])
        self.assertIn("excluded from reply assembly", frame["note"])

    def test_capture_truncates_from_the_end_and_says_so(self):
        instance, _chat = self.attached()
        instance.session.child = FakeChild(pid=99, pgid=99, pane="x" * 5000)
        frame = instance.handle({"v": 1, "id": "c", "type": "req", "op": "capture",
                                 "bytes": 100})
        self.assertTrue(frame["truncated"])
        self.assertEqual(len(frame["raw"]), 100)
        self.assertEqual(frame["pane_bytes"], 5000)


# ── 8. the gate, called directly ─────────────────────────────────────────────

class GateTest(Base):

    def test_an_expectation_naming_no_tool_is_cannot_verify(self):
        gate = bridge_mod.Gate(self.cwd)
        verdict, refusal = gate.judge({"tools": [tool_block()]}, {"check": "ok"})
        self.assertEqual(refusal, "cannot_verify")
        self.assertEqual(verdict["verified"], "cannot_verify")
        self.assertFalse(verdict["ok"])
        self.assertIn("names no tool", verdict["bounce_note"])

    def test_a_name_mismatch_reports_the_names_that_were_observed(self):
        gate = bridge_mod.Gate(self.cwd)
        reply = {"tools": [tool_block(name="run_terminal_command"),
                           tool_block(name="memory_record")]}
        verdict, refusal = gate.judge(reply, {"tool": "read_file",
                                              "check": "contains:x"})
        self.assertEqual(refusal, "cannot_verify")
        self.assertIn("memory_record", verdict["bounce_note"])
        self.assertIn("run_terminal_command", verdict["bounce_note"])

    def test_an_argument_that_no_call_carried_is_a_mismatch(self):
        gate = bridge_mod.Gate(self.cwd)
        reply = {"tools": [tool_block(input_={"paths": ["other.py"]}, output="x")]}
        verdict, _refusal = gate.judge(reply, {"tool": "read_files", "arg": "needle.py",
                                               "check": "contains:x"})
        self.assertEqual(verdict["verified"], "cannot_verify")
        self.assertIn("no call carried the argument", verdict["bounce_note"])

    def test_a_turn_with_no_expectation_reports_not_declared(self):
        gate = bridge_mod.Gate(self.cwd)
        verdict, refusal = gate.judge({"tools": [tool_block()]}, None)
        self.assertIsNone(refusal)
        self.assertFalse(verdict["declared"])
        self.assertEqual(verdict["verified"], "not_declared")
        self.assertFalse(verdict["ok"])
        self.assertIsNone(verdict["level"])

    def test_the_gate_cannot_read_outside_the_declared_tool(self):
        # A claim about a non-reader tool is confirmed at most at `observed`:
        # there is nothing to re-observe, and the verdict says so.
        gate = bridge_mod.Gate(self.cwd)
        reply = {"tools": [tool_block(name="run_terminal_command",
                                      output="alpha")]}
        verdict, refusal = gate.judge(reply, {"tool": "run_terminal_command",
                                              "check": "contains:alpha"})
        self.assertIsNone(refusal)
        self.assertEqual(verdict["level"], "observed")
        self.assertNotIn("reproduced", verdict["how_verified"])

    def test_a_reader_argument_is_resolved_relative_to_the_attach_cwd(self):
        os.makedirs(os.path.join(self.cwd, "sub"), exist_ok=True)
        with open(os.path.join(self.cwd, "sub", "rel.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("relative content\n")
        gate = bridge_mod.Gate(self.cwd)
        reply = {"tools": [tool_block(input_={"paths": ["sub/rel.txt"]},
                                      output="relative content")]}
        verdict, _refusal = gate.judge(reply, {"tool": "read_files",
                                               "arg": "sub/rel.txt",
                                               "check": "contains:relative"})
        self.assertEqual(verdict["level"], "invariant")


# ── 9. the CLI's own contract ────────────────────────────────────────────────

class CliTest(Base):

    def test_the_self_test_exits_zero_and_keeps_hello_first(self):
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = bridge_mod.main(["--self-test"])
        self.assertEqual(code, 0)
        report = json.loads(buffer.getvalue())
        frames = report["frames"]
        self.assertEqual(frames[0]["type"], "err")
        self.assertEqual(frames[0]["code"], "E_PROTOCOL")
        self.assertEqual(frames[1]["type"], "res")
        self.assertTrue(report["events"])

    def test_a_call_sequence_reports_the_frames(self):
        import contextlib
        home = os.path.join(self.tmp, "cli-home")
        os.makedirs(home)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = bridge_mod.main(["--call", "hello", "--call", "status",
                                    "--home", home, "--state-dir", self.state_dir,
                                    "--no-spawn"])
        self.assertEqual(code, 0)
        frames = json.loads(buffer.getvalue())
        self.assertEqual(frames[0]["type"], "res")
        self.assertEqual(frames[1]["type"], "res")


if __name__ == "__main__":
    unittest.main(verbosity=1)
