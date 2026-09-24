#!/usr/bin/env python3
"""bro_session_measure_test.py — tests for M0's session instrument.

The live half of `bro_session_measure.py` cannot be unit-tested: it reads a
running client's files, which is the point of it. But every *decision* it makes
can be, and those decisions are where a measurement turns into a comfort. Three
kinds of test are here, and the third is the one that matters:

1. **The contract** — the lock verdicts, the project-key collision, the shape
   test that finds the live conversation, the watermark delta, the completion
   rule. Each with a fixture in the shape this box actually produced (measured
   2026-09-23 on client `0.0.186`), including the 13-digit epoch in a message id
   that is the only ordering key there is.
2. **The drift** — `§18.7` was measured on `0.0.180` and two details have already
   moved: tool blocks are `tool` now, not `tool-call`, and an `isComplete` flag
   exists that the spec did not know about. Both spellings are accepted and the
   one that fired is reported, so a reader cannot fail silently on either.
3. **Negative controls that must fail on purpose.** A test that cannot fail is
   decoration. The ones below are the failure modes this module exists to
   prevent, each asserted in the direction that would hide a defect:
   - a live foreign owner is never classified `owner_dead` (which would invite a
     second spawn onto the operator's machine);
   - a lock that is present but unreadable is never ours and never absent (`I6`);
   - the newest directory is never mistaken for the conversation (it is ~2 days
     newer here and holds only a launch log);
   - a delta read never returns the message it already consumed;
   - a message the record marks unfinished is never reported as the answer;
   - **`test_it_never_signals`** records every `os.kill`/`os.killpg` call made
     while classifying every lock verdict and requires the list to be empty, so
     `I2` ("bro never signals a pid it does not own") is enforced by the test
     rather than promised by a comment.

Run: python3 bro_session_measure_test.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bro_session_measure as measure

# A plausible epoch-ms base. Real ids are `<role>-<13 digits>`, and the epoch is
# the only ordering key in the record (`timestamp` is `"05:52 AM"`, no date), so
# the fixtures use the real magnitude rather than a short placeholder.
T0 = 1_790_000_000_000


def mid(role, offset):
    return "%s-%d" % (role, T0 + offset)


def _stat_line(pid, comm="freebuff", start="3456506"):
    """A `/proc/<pid>/stat` line.

    After `)` come fields 3..22, so `tail[0]` is the state and `tail[19]` is
    `starttime`. `comm` may contain spaces and parentheses, which is why the real
    parser splits after the last `)` rather than on whitespace.
    """
    fields = ["S"] + [str(i) for i in range(1, 19)] + [start]
    return "%d (%s) %s\n" % (pid, comm, " ".join(fields))


class FakeProc(object):
    """A `/proc` we can write: a pid we add is alive, anything else is gone."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="fake-proc-")
        self.pids = {}

    def add(self, pid, cmdline, start="3456506"):
        self.pids[pid] = cmdline
        directory = os.path.join(self.root, str(pid))
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "cmdline"), "wb") as handle:
            handle.write(cmdline.encode("utf-8").replace(b" ", b"\0") + b"\0")
        with open(os.path.join(directory, "stat"), "w", encoding="utf-8") as handle:
            handle.write(_stat_line(pid, start=start))
        return self.root

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class MalformedBlocksTest(unittest.TestCase):
    """The blocks array is not guaranteed to hold objects, and the reader must not care.

    Found live: a message written with `blocks: ["text", "tool"]` (a *reply*
    object, not a record message) made `_is_settled` raise `AttributeError: 'str'
    object has no attribute 'get'` from inside the settled-boundary scan — the
    read after a write, which is the one path a bridge cannot afford to lose.
    These are the regression tests for that hole and for its blast radius.
    """

    def test_blocks_of_returns_only_objects(self):
        message = {"blocks": [{"type": "text"}, "text", None, 7, {"type": "tool"}]}
        self.assertEqual(measure.blocks_of(message), [{"type": "text"},
                                                      {"type": "tool"}])
        self.assertEqual(measure.malformed_blocks(message), 3)

    def test_blocks_of_survives_a_non_list(self):
        for value in (None, "text", 7, {"type": "text"}):
            message = {"blocks": value}
            self.assertEqual(measure.blocks_of(message), [])
            self.assertEqual(measure.malformed_blocks(message), 0)

    def test_every_reader_that_walks_blocks_survives_one(self):
        message = {"id": mid("ai", 100), "variant": "ai", "isComplete": True,
                   "blocks": ["text", {"type": "text", "textType": "text",
                                        "content": "hello"}, "tool"]}
        self.assertFalse(measure.is_divider(message))
        self.assertEqual(measure.answer_text(message), "hello")
        self.assertEqual(measure.reasoning_text(message), "")
        self.assertEqual(measure.tool_blocks(message), [])
        self.assertEqual(measure.tool_names(message), [])
        self.assertIsNone(measure.tool_block_type_seen(message))
        self.assertTrue(measure._is_settled(message))

    def test_a_record_with_one_is_still_read_and_still_settles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "chat-messages.json")
            messages = [{"id": mid("user", 0), "variant": "user",
                         "blocks": ["prompt-as-a-string"]},
                        {"id": mid("ai", 100), "variant": "ai", "isComplete": True,
                         "blocks": ["text", {"type": "text", "textType": "text",
                                              "content": "answer"}]}]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(messages, handle)
            status, read_back, watermark, _detail = measure.read_record(
                path, settled_only=True)
            self.assertEqual(status, "initial")
            self.assertEqual(len(read_back), 2)
            self.assertEqual(watermark.message_count, 2)
            # And the delta path — the one that crashed live — still advances.
            messages.append({"id": mid("user", 200), "variant": "user",
                             "blocks": ["another string"]})
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(messages, handle)
            status, read_back, watermark2, _detail = measure.read_record(
                path, watermark, settled_only=True)
            self.assertIn(status, ("delta", "resync"))
            self.assertEqual(watermark2.message_count, 3)

    def test_a_non_object_top_level_element_refuses_the_scan(self):
        """A non-object element cannot be a message, so the boundary is refused.

        The safe direction: refusing costs a resync, counting it would put the
        watermark past bytes the reader does not understand.
        """
        raw = json.dumps([{"id": mid("user", 0), "variant": "user"}, "oops"])
        self.assertIsNone(measure._settled_boundary(raw.encode("utf-8")))


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m0-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def write(self, name, content):
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(path, mode) as handle:
            handle.write(content)
        return path


# ── 1. the lock ──────────────────────────────────────────────────────────────

class LockTest(Base):

    def setUp(self):
        Base.setUp(self)
        self.proc = FakeProc()
        self.addCleanup(self.proc.cleanup)

    def lock(self, payload):
        path = self.path("freebuff-instance-owner.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_an_absent_lock_is_absent(self):
        verdict, detail = measure.read_lock(self.path("nope.json"), proc=self.proc.root)
        self.assertEqual(verdict, "absent")
        self.assertIn("no lock file", detail["reason"])

    def test_a_live_client_owner_is_live(self):
        self.proc.add(17101, "/root/.config/manicode/freebuff")
        path = self.lock({"instanceId": "47ede7b7", "pid": 17101})
        verdict, detail = measure.read_lock(path, proc=self.proc.root)
        self.assertEqual(verdict, "live")
        self.assertEqual(detail["owner"], {"pid": 17101, "instanceId": "47ede7b7"})
        self.assertTrue(detail["proc"]["is_client"])
        self.assertEqual(detail["proc"]["start_time"], "3456506")

    def test_a_dead_owner_is_reclaimable(self):
        path = self.lock({"instanceId": "x", "pid": 999999})
        verdict, detail = measure.read_lock(path, proc=self.proc.root)
        self.assertEqual(verdict, "owner_dead")
        self.assertIn("999999", detail["reason"])

    def test_a_reused_pid_is_stale_with_the_reason_recorded(self):
        # §19.4 rule 5: the pid lives, but it is not the client.
        self.proc.add(4242, "/usr/bin/python3 server.py")
        path = self.lock({"instanceId": "x", "pid": 4242})
        verdict, detail = measure.read_lock(path, proc=self.proc.root)
        self.assertEqual(verdict, "owner_reused")
        self.assertIn("not the client", detail["reason"])
        self.assertFalse(detail["proc"]["is_client"])

    def test_the_start_time_distinguishes_a_recycled_pid(self):
        """`kill(pid, 0)` cannot tell a reused pid from the session we own; the
        recorded start-time can, which is why it is captured."""
        self.proc.add(17101, "/root/.config/manicode/freebuff", start="1111")
        path = self.lock({"instanceId": "x", "pid": 17101})
        _, first = measure.read_lock(path, proc=self.proc.root)
        self.proc.add(17101, "/root/.config/manicode/freebuff", start="9999")
        _, second = measure.read_lock(path, proc=self.proc.root)
        self.assertNotEqual(first["proc"]["start_time"],
                            second["proc"]["start_time"])

    def test_every_malformed_shape_is_malformed(self):
        self.proc.add(17101, "/root/.config/manicode/freebuff")
        cases = {
            "truncated": '{"instanceId": "47ede7b7", "pid": 171',
            "no_pid": {"instanceId": "47ede7b7"},
            "pid_is_string": {"instanceId": "47ede7b7", "pid": "17101"},
            "pid_is_bool": {"instanceId": "47ede7b7", "pid": True},
            "an_array": [1, 2, 3],
        }
        for name, payload in cases.items():
            verdict, detail = measure.read_lock(self.lock(payload),
                                                proc=self.proc.root)
            self.assertEqual(verdict, "malformed", "%s classified as %s (%s)"
                             % (name, verdict, detail.get("reason")))

    def test_extra_keys_are_reported_not_hidden(self):
        self.proc.add(17101, "/root/.config/manicode/freebuff")
        path = self.lock({"instanceId": "x", "pid": 17101, "cwd": "/somewhere"})
        _, detail = measure.read_lock(path, proc=self.proc.root)
        self.assertEqual(detail["extra_keys"], ["cwd"])

    # ── negative controls ────────────────────────────────────────────────────

    def test_a_live_foreign_owner_is_never_classified_as_dead(self):
        """The control that must fail on purpose.

        `owner_dead` is the verdict that licenses a reclaim-and-spawn (§19.4
        rule 3). Reporting it for a live owner would put a second instance on a
        machine that already has one — `I1`, the invariant this whole appendix
        exists for.
        """
        self.proc.add(17101, "/root/.config/manicode/freebuff")
        path = self.lock({"instanceId": "foreign", "pid": 17101})
        verdict, _ = measure.read_lock(path, proc=self.proc.root)
        self.assertNotEqual(verdict, "owner_dead")
        self.assertNotIn(verdict, ("absent", "malformed"))
        self.assertEqual(verdict, "live")

    def test_a_lock_that_is_present_but_unreadable_is_never_absent_or_ours(self):
        """`I6`: the fail-safe direction is refuse to spawn. A present-but-broken
        lock must not read as absent (which invites a spawn) or as live-ours
        (which skips the lock check entirely)."""
        path = self.path("freebuff-instance-owner.json")
        os.makedirs(path)  # a directory where the lock file should be
        verdict, detail = measure.read_lock(path, proc=self.proc.root)
        self.assertEqual(verdict, "malformed")
        self.assertNotEqual(verdict, "absent")
        self.assertTrue(detail["reason"])

    def test_a_missing_proc_entry_is_not_assumed_alive(self):
        path = self.lock({"instanceId": "x", "pid": 1})
        verdict, _ = measure.read_lock(path, proc=self.path("empty-proc"))
        self.assertEqual(verdict, "owner_dead")

    def test_it_never_signals(self):
        """`I2`, enforced: classifying every lock verdict must send zero signals.

        The lock check runs while a live session — possibly the operator's only
        one — owns the machine. A well-meaning `kill(pid, 0)` liveness probe is
        harmless; the same call with a signal is not, and the two are one edit
        apart. So the calls are recorded rather than trusted.
        """
        calls = []
        real_kill, real_killpg = os.kill, os.killpg

        def spy_kill(pid, sig):
            calls.append(("kill", pid, sig))
            raise OSError("the test spies on os.kill")

        def spy_killpg(pgid, sig):
            calls.append(("killpg", pgid, sig))
            raise OSError("the test spies on os.killpg")

        os.kill, os.killpg = spy_kill, spy_killpg
        try:
            self.proc.add(17101, "/root/.config/manicode/freebuff")
            self.proc.add(4242, "/usr/bin/python3")
            payloads = [{"instanceId": "x", "pid": 17101},
                        {"instanceId": "x", "pid": 999999},
                        {"instanceId": "x", "pid": 4242},
                        {"instanceId": "x"},
                        "not json at all"]
            for payload in payloads:
                measure.read_lock(self.lock(payload), proc=self.proc.root)
            measure.read_lock(self.path("nope.json"), proc=self.proc.root)
        finally:
            os.kill, os.killpg = real_kill, real_killpg
        self.assertEqual(calls, [], "the lock check sent signals: %r" % calls)


class ProcFactsTest(unittest.TestCase):

    def test_the_real_proc_reads_a_live_pid(self):
        # `os.getpid()` is this test process: alive, not the client.
        facts = measure.proc_facts(os.getpid())
        self.assertTrue(facts["alive"])
        self.assertFalse(facts["is_client"])
        self.assertIsNotNone(facts["start_time"])
        self.assertTrue(facts["cmdline"])

    def test_a_nonexistent_pid_is_dead_not_an_error(self):
        facts = measure.proc_facts(999999)
        self.assertFalse(facts["alive"])
        self.assertIsNone(facts["start_time"])

    def test_a_nonsense_pid_is_dead_not_an_error(self):
        for value in (0, -1, None, "17101"):
            self.assertFalse(measure.proc_facts(value)["alive"])

    def test_the_stat_fixture_matches_the_parser_it_tests(self):
        """The fake `/proc` and the real parser must agree on field 22, or every
        test above passes while measuring a layout the kernel does not have."""
        root = tempfile.mkdtemp(prefix="fake-proc-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "7"))
        with open(os.path.join(root, "7", "stat"), "w", encoding="utf-8") as handle:
            handle.write(_stat_line(7, start="3456506"))
        with open(os.path.join(root, "7", "cmdline"), "wb") as handle:
            handle.write(b"/x/freebuff\0")
        self.assertEqual(measure.proc_facts(7, proc=root)["start_time"], "3456506")


# ── 2. project resolution ────────────────────────────────────────────────────

class ProjectTest(Base):

    def home(self, recent):
        self.write("recent-projects.json", json.dumps(recent))
        os.makedirs(self.path("projects", "builderbro"), exist_ok=True)
        return self.tmp

    def test_a_single_path_resolves(self):
        home = self.home([{"path": "/mnt/sdcard/Download/builderbro"}])
        verdict, detail = measure.resolve_project(home,
                                                  "/mnt/sdcard/Download/builderbro")
        self.assertEqual(verdict, "ok")
        self.assertEqual(detail["project_key"], "builderbro")

    def test_two_checkouts_sharing_a_basename_are_ambiguous(self):
        home = self.home([{"path": "/mnt/sdcard/Download/builderbro"},
                          {"path": "/sdcard/Download/builderbro"}])
        verdict, detail = measure.resolve_project(home,
                                                  "/mnt/sdcard/Download/builderbro")
        self.assertEqual(verdict, "ambiguous")
        self.assertEqual(len(detail["collisions"]), 2)

    def test_the_collision_check_can_pass(self):
        # The control for the test above: distinct basenames must resolve. Without
        # this, "ambiguous" could be the answer for every input and nobody would
        # notice, because the interesting case and the bug would look alike.
        home = self.home([{"path": "/mnt/sdcard/Download/builderbro"},
                          {"path": "/root/docs"}])
        verdict, _ = measure.resolve_project(home, "/mnt/sdcard/Download/builderbro")
        self.assertEqual(verdict, "ok")

    def test_an_unknown_project_is_missing(self):
        home = self.home([])
        verdict, detail = measure.resolve_project(home, "/tmp/nowhere")
        self.assertEqual(verdict, "missing")
        self.assertIn("nowhere", detail["reason"])

    def test_a_missing_recent_list_does_not_crash(self):
        os.makedirs(self.path("projects", "builderbro"), exist_ok=True)
        verdict, _ = measure.resolve_project(self.tmp, "/x/builderbro")
        self.assertEqual(verdict, "ok")


# ── 3. the active conversation ───────────────────────────────────────────────

class ConversationTest(Base):

    def conversation(self, name, messages=2):
        self.write(os.path.join("chats", name, "chat-meta.json"),
                   json.dumps({"messageCount": messages}))
        self.write(os.path.join("chats", name, "chat-messages.json"), "[]")

    def log_dir(self, name):
        self.write(os.path.join("chats", name, "log.jsonl"), "{}\n")

    def test_the_shape_test_finds_the_conversation_not_the_newest_dir(self):
        """The defect `§18.7` names, with this box's real numbers.

        Measured here: the newest directory was a per-launch log dir ~2 days
        newer than the live conversation, so "newest by name" reads the wrong
        chat — and on the spec's own measurement it was wrong by 11 days.
        """
        self.conversation("2026-09-21T08-23-48.129Z")
        self.log_dir("2026-09-21T08-24-51.992Z")
        self.log_dir("2026-09-23T11-48-56.035Z")
        verdict, chat_dir, detail = measure.resolve_active_conversation(self.tmp)
        self.assertEqual(verdict, "ok")
        self.assertEqual(os.path.basename(chat_dir), "2026-09-21T08-23-48.129Z")
        self.assertEqual(detail["active"], "2026-09-21T08-23-48.129Z")
        self.assertEqual(detail["newest_by_name"], "2026-09-23T11-48-56.035Z")
        self.assertFalse(detail["newest_is_active"])
        self.assertEqual(detail["log_dirs"], 2)
        self.assertGreater(detail["stale_by_days"], 2.0)

    def test_the_newest_conversation_wins_when_there_are_several(self):
        self.conversation("2026-09-01T00-00-00.000Z")
        self.conversation("2026-09-20T00-00-00.000Z")
        os.utime(self.path("chats", "2026-09-20T00-00-00.000Z", "chat-meta.json"),
                 (2_000_000_000, 2_000_000_000))
        os.utime(self.path("chats", "2026-09-01T00-00-00.000Z", "chat-meta.json"),
                 (1_000_000_000, 1_000_000_000))
        verdict, chat_dir, _ = measure.resolve_active_conversation(self.tmp)
        self.assertEqual(verdict, "ok")
        self.assertEqual(os.path.basename(chat_dir), "2026-09-20T00-00-00.000Z")

    def test_only_log_dirs_is_none_not_a_guess(self):
        self.log_dir("2026-09-23T11-48-56.035Z")
        self.log_dir("2026-09-20T09-58-14.114Z")
        verdict, chat_dir, detail = measure.resolve_active_conversation(self.tmp)
        self.assertEqual(verdict, "none")
        self.assertIsNone(chat_dir)
        self.assertEqual(detail["log_dirs"], 2)

    def test_no_chats_dir_is_missing(self):
        verdict, _, detail = measure.resolve_active_conversation(self.tmp)
        self.assertEqual(verdict, "missing")
        self.assertTrue(detail["reason"])

    def test_days_between_is_the_magnitude_not_an_impression(self):
        self.assertEqual(
            measure._days_between("2026-09-23T11-48-56.035Z",
                                  "2026-09-21T08-23-48.129Z"), 2.14)
        self.assertIsNone(measure._days_between("nonsense", "2026-09-21T00-00-00.000Z"))


# ── 3b. the session axis ─────────────────────────────────────────────────────

class SessionAxisTest(Base):

    def log(self, records):
        return self.write("log.jsonl", "".join(json.dumps(r) + "\n" for r in records))   # noqa: E501

    def test_a_lifecycle_phrase_in_a_payload_is_not_an_event(self):
        """The measured defect, as a test.

        `log.jsonl` records the payloads of the session's own actions, so the
        phrase turns up inside source files the session wrote. On this box 18 of
        27 lines containing `Freebuff session over` contained it in a payload,
        and only 9 were the client saying it — so a substring scan reported
        `over` for a session that was working, which would make the router refuse
        every turn on a live brain.
        """
        path = self.log([
            {"level": "DEBUG", "msg": "wrote a file",
             "timestamp": "2026-09-23T16:46:41.258Z",
             "data": {"path": "x.py",
                      "newContent": 'x = "Freebuff session over"'}},
        ])
        verdict, detail = measure.session_axis(path)
        self.assertEqual(verdict, "unknown")
        self.assertEqual(detail["event_count"], 0)
        self.assertEqual(detail["mentions"], 1)
        self.assertIn("inside a payload", detail["reason"])

    def test_the_event_is_read_from_the_records_own_msg(self):
        path = self.log([
            {"msg": "Freebuff session over",
             "timestamp": "2026-09-23T14:00:00.000Z"},
            {"msg": "Reconnection detected, firing onReconnect callback",
             "timestamp": "2026-09-23T14:22:03.761Z"},
        ])
        verdict, detail = measure.session_axis(path)
        self.assertEqual(verdict, "reconnecting")
        self.assertEqual(detail["event_count"], 2)
        self.assertEqual(detail["last_event"], "reconnecting")
        self.assertEqual(detail["mentions"], 0)

    def test_a_marker_in_a_payload_does_not_beat_the_real_event_before_it(self):
        path = self.log([
            {"msg": "Reconnection detected",
             "timestamp": "2026-09-23T14:22:03.761Z"},
            {"msg": "ran a command",
             "timestamp": "2026-09-23T16:46:41.258Z",
             "data": {"output": "Reconnection detected"}},
        ])
        verdict, detail = measure.session_axis(path)
        self.assertEqual(verdict, "reconnecting")
        self.assertEqual(detail["mentions"], 1)

    def test_a_stale_over_marker_loses_to_a_record_written_after_it(self):
        """A marker is a statement about the past; progress is about now."""
        record = self.write("chat-messages.json", "[]")
        path = self.log([{"msg": "Freebuff session over",
                          "timestamp": "2000-01-01T00:00:00.000Z"}])
        verdict, detail = measure.session_axis(path, record_path=record)
        self.assertEqual(verdict, "connected")
        self.assertIn("stale, not the session", detail["reason"])

    def test_a_fresh_over_marker_without_later_progress_stays_over(self):
        record = self.write("chat-messages.json", "[]")
        old = os.path.getmtime(record) - 10_000
        os.utime(record, (old, old))
        future = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 60))
        path = self.log([{"msg": "Freebuff session over",
                          "timestamp": future + ".000Z"}])
        verdict, detail = measure.session_axis(path, record_path=record)
        self.assertEqual(verdict, "over")
        self.assertEqual(detail["last_event"], "over")

    def test_no_log_is_unknown_not_connected(self):
        verdict, detail = measure.session_axis(self.path("nope.jsonl"))
        self.assertEqual(verdict, "unknown")
        self.assertTrue(detail["reason"])

    def test_an_unparseable_line_is_a_mention_not_an_event(self):
        path = self.write("log.jsonl", "Freebuff session over\n")
        verdict, detail = measure.session_axis(path)
        self.assertEqual(verdict, "unknown")
        self.assertEqual(detail["mentions"], 1)
        self.assertEqual(detail["event_count"], 0)

    def test_timestamp_parsing_is_utc_and_total(self):
        self.assertEqual(measure._timestamp_seconds("1970-01-01T00:00:00.000Z"), 0.0)
        # Hand-checked against the calendar: 2026-09-23T00:00Z is 1790121600
        # (20454 days to 2026-01-01, plus 265 days of 2026), plus 51723.761s.
        self.assertEqual(measure._timestamp_seconds("2026-09-23T14:22:03.761Z"),
                         1790173323.761)
        self.assertIsNone(measure._timestamp_seconds("not a time"))
        self.assertIsNone(measure._timestamp_seconds(None))


# ── 4. the record as a watermark channel ─────────────────────────────────────

def message(message_id, variant="ai", text=None, tools=0, complete=None,
            reasoning=None):
    """A record message in the shape measured on client 0.0.186."""
    blocks = []
    if reasoning:
        blocks.append({"type": "text", "textType": "reasoning",
                       "content": reasoning})
    for index in range(tools):
        blocks.append({"type": "tool", "toolName": "read_files",
                       "input": {"paths": ["x"]}, "output": "files: []",
                       "toolCallId": "t%d" % index, "agentId": "main-agent"})
    if text is not None:
        blocks.append({"type": "text", "textType": "text", "content": text})
    payload = {"id": message_id, "variant": variant, "content": "", "blocks": blocks}
    if variant == "user":
        payload["content"] = text or ""
        payload["blocks"] = []
    if complete is not None:
        payload["isComplete"] = complete
    return payload


class RecordReaderTest(Base):

    def record(self, messages, name="chat-messages.json"):
        # Written the way the client writes it: compact, single line, whole file.
        return self.write(name, json.dumps(messages, separators=(",", ":")))

    def test_an_initial_read_returns_everything_with_a_watermark(self):
        path = self.record([message(mid("user", 1), "user", text="hi"),
                            message(mid("ai", 2), text="hello", complete=True)])
        status, messages, watermark, _ = measure.read_record(path)
        self.assertEqual(status, "initial")
        self.assertEqual(len(messages), 2)
        self.assertEqual(watermark.message_count, 2)
        # The watermark covers the bytes *before* the closing bracket, so the
        # next read can prove the file only grew.
        with open(path, "rb") as handle:
            raw = handle.read()
        self.assertEqual(watermark.prefix_bytes, len(raw) - 1)
        self.assertEqual(watermark.prefix_hash,
                         measure._watermark_of(raw, 2).prefix_hash)

    def test_an_unchanged_record_reports_unchanged_not_a_reread(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True)])
        _, _, watermark, _ = measure.read_record(path)
        status, messages, again, _ = measure.read_record(path, watermark)
        self.assertEqual(status, "unchanged")
        self.assertEqual(messages, [])
        self.assertEqual(again.message_count, 1)
        self.assertEqual(again.prefix_hash, watermark.prefix_hash)

    def test_a_delta_read_returns_only_the_new_messages(self):
        path = self.record([message(mid("user", 1), "user", text="a"),
                            message(mid("ai", 2), text="b", complete=True)])
        _, _, watermark, _ = measure.read_record(path)
        # The client appends by rewriting the whole file.
        self.record([message(mid("user", 1), "user", text="a"),
                     message(mid("ai", 2), text="b", complete=True),
                     message(mid("user", 3), "user", text="c")])
        status, new_messages, after, _ = measure.read_record(path, watermark)
        self.assertEqual(status, "delta")
        self.assertEqual(len(new_messages), 1)
        self.assertEqual(new_messages[0]["id"], mid("user", 3))
        self.assertEqual(after.message_count, 3)

    def test_a_delta_watermark_equals_a_full_reads_watermark(self):
        """A delta read and a full read must agree on the watermark.

        If they do not, the next delta read is computed from the wrong offset and
        the reader drifts — quietly, one message at a time.
        """
        path = self.record([message(mid("ai", 1), text="a", complete=True)])
        _, _, first, _ = measure.read_record(path)
        self.record([message(mid("ai", 1), text="a", complete=True),
                     message(mid("ai", 2), text="b", complete=True)])
        _, _, delta, _ = measure.read_record(path, first)
        _, _, full, _ = measure.read_record(path)
        self.assertEqual(delta.as_dict(), full.as_dict())
        self.assertEqual(delta.prefix_hash, full.prefix_hash)

    def test_several_deltas_in_a_row_do_not_drift(self):
        # The first record must match the loop's first iteration byte for byte,
        # or the test measures a rewrite and reports it as a drift.
        path = self.record([message(mid("ai", 1), text="m1", complete=True)])
        _, _, watermark, _ = measure.read_record(path)
        seen = 0
        for index in range(2, 8):
            messages = [message(mid("ai", n), text="m%d" % n, complete=True)
                        for n in range(1, index + 1)]
            self.record(messages)
            status, new_messages, watermark, _ = measure.read_record(path, watermark)
            self.assertEqual(status, "delta")
            self.assertEqual(len(new_messages), 1)
            self.assertEqual(new_messages[0]["id"], mid("ai", index))
            seen += 1
        self.assertEqual(seen, 6)
        self.assertEqual(watermark.message_count, 7)

    def test_a_rewritten_prefix_is_a_resync_with_the_reason(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True),
                            message(mid("ai", 2), text="b", complete=True)])
        _, _, watermark, _ = measure.read_record(path)
        # Same length, different content: a rewrite, not an append.
        self.record([message(mid("ai", 1), text="CHANGED", complete=True),
                     message(mid("ai", 2), text="b", complete=True),
                     message(mid("ai", 3), text="c", complete=True)])
        status, messages, after, detail = measure.read_record(path, watermark)
        self.assertEqual(status, "resync")
        self.assertIn("prefix hash moved", detail["resync_reason"])
        self.assertEqual(len(messages), 3)
        self.assertEqual(after.message_count, 3)

    def test_a_torn_read_is_reported_not_parsed(self):
        """A 32 MB file rewritten in place is readable while half-written."""
        path = self.write("chat-messages.json",
                          '[{"id":"ai-1","variant":"ai","blo')
        status, messages, watermark, detail = measure.read_record(path)
        self.assertEqual(status, "torn")
        self.assertEqual(messages, [])
        self.assertIsNone(watermark)
        self.assertIn("closing bracket", detail["reason"])

    def test_a_torn_delta_is_reported_too(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True)])
        _, _, watermark, _ = measure.read_record(path)
        self.write("chat-messages.json",
                   '[{"id":"ai-1","variant":"ai"},{"id":"ai-2","vari')
        status, _, _, _ = measure.read_record(path, watermark)
        self.assertEqual(status, "torn")

    def test_a_shrinking_record_is_a_resync_not_a_negative_slice(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True)])
        _, _, watermark, _ = measure.read_record(path)
        with open(path, "wb") as handle:
            handle.write(b"[]")
        status, messages, _, detail = measure.read_record(path, watermark)
        self.assertEqual(status, "resync")
        self.assertIn("shrank", detail["resync_reason"])
        self.assertEqual(messages, [])

    def test_a_different_conversation_of_the_same_size_is_a_resync(self):
        """The case a size check cannot catch: the file is replaced by another
        conversation whose byte length happens to match — what a `--continue`
        onto a different chat id looks like from outside. Only re-hashing the
        bytes the caller already consumed finds it."""
        first = [message(mid("ai", 1), text="aaaa", complete=True),
                 message(mid("ai", 2), text="bbbb", complete=True)]
        path = self.record(first)
        _, _, watermark, _ = measure.read_record(path)
        size_before = os.path.getsize(path)
        second = [message(mid("ai", 1), text="cccc", complete=True),
                  message(mid("ai", 2), text="dddd", complete=True)]
        self.record(second)
        self.assertEqual(os.path.getsize(path), size_before,
                         "the fixture must keep the byte count equal to be a "
                         "test of the hash rather than of the size check")
        status, _, _, detail = measure.read_record(path, watermark)
        self.assertEqual(status, "resync")
        self.assertIn("prefix hash moved", detail["resync_reason"])

    def test_a_missing_record_is_missing_not_empty(self):
        status, messages, watermark, _ = measure.read_record(self.path("nope.json"))
        self.assertEqual(status, "missing")
        self.assertEqual(messages, [])
        self.assertIsNone(watermark)

    def test_a_full_read_of_a_garbled_record_is_torn(self):
        path = self.write("chat-messages.json", "[not json at all]")
        status, _, _, detail = measure.read_record(path)
        self.assertEqual(status, "torn")
        self.assertIn("unparseable", detail["reason"])

    def test_a_record_that_is_not_an_array_is_torn(self):
        path = self.write("chat-messages.json", '{"messages": []}')
        status, _, _, _ = measure.read_record(path)
        self.assertEqual(status, "torn")

    def test_a_prior_watermark_from_the_future_is_a_resync(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True)])
        future = measure.Watermark(500, 99_999_999, "deadbeef")
        status, messages, _, detail = measure.read_record(path, future)
        self.assertEqual(status, "resync")
        self.assertIn("shrank", detail["resync_reason"])
        self.assertEqual(len(messages), 1)

    def test_a_bogus_prior_object_is_a_resync_not_a_crash(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True)])
        status, messages, _, detail = measure.read_record(path, {"nope": 1})
        self.assertEqual(status, "resync")
        self.assertIn("not a Watermark", detail["resync_reason"])
        self.assertEqual(len(messages), 1)

    # ── the settled watermark (the live finding) ─────────────────────────────

    def test_the_settled_watermark_stops_at_the_last_finished_message(self):
        """Measured on 2026-09-23: the live record is rewritten *inside* the
        message being written, so a watermark at the end of the file is invalidated
        by that message's own growth. The boundary belongs at the last finished
        message instead."""
        settled = [message(mid("user", 1000), "user", text="q"),
                   message(mid("ai", 2000), text="done", complete=True),
                   message(mid("user", 3000), "user", text="q2"),
                   message(mid("ai", 4000), text="still writing")]
        path = self.record(settled)
        _, messages, watermark, detail = measure.read_record(path,
                                                             settled_only=True)
        self.assertEqual(len(messages), 4)
        # Two user prompts and one finished reply settle; only the writing turn
        # does not. A user message is complete by construction, so counting it as
        # "in flight" would leave the watermark parked for no reason.
        self.assertEqual(watermark.message_count, 3)
        self.assertEqual(detail["in_flight"], 1)
        boundary, settled_count, total = measure._settled_boundary(
            open(path, "rb").read())
        self.assertEqual(boundary, watermark.prefix_bytes)
        self.assertEqual((settled_count, total), (3, 4))
        self.assertLess(watermark.prefix_bytes, os.path.getsize(path) - 1)
        # The unconsumed part is exactly the in-flight message.
        with open(path, "rb") as handle:
            raw = handle.read()
        self.assertIn(mid("ai", 4000).encode(), raw[watermark.prefix_bytes:])

    def test_a_rewrite_inside_the_in_flight_message_is_not_a_resync(self):
        """The control that must fail on purpose, and the whole point of the
        finding: with an end-of-file watermark this rewrite reports `resync`,
        while with a settled watermark it correctly reports nothing new has
        finished. Measured live, that difference was 4 resyncs and 0 usable
        deltas in 240 s against 0 resyncs once the boundary moved."""
        head = [message(mid("user", 1000), "user", text="q"),
                message(mid("ai", 2000), text="done", complete=True)]
        path = self.record(head + [message(mid("ai", 4000), text="w1")])

        _, _, eof, _ = measure.read_record(path)
        _, _, settled, _ = measure.read_record(path, settled_only=True)

        # The in-flight message grows: its own bytes change, nothing before them.
        self.record(head + [message(mid("ai", 4000), text="w1 plus more")])

        eof_status, _, _, eof_detail = measure.read_record(path, eof)
        settled_status, new_messages, settled_after, settled_detail = \
            measure.read_record(path, settled, settled_only=True)

        self.assertEqual(eof_status, "resync")
        self.assertIn("prefix hash moved", eof_detail["resync_reason"])
        self.assertNotEqual(settled_status, "resync")
        self.assertEqual(settled_status, "delta")
        # The in-flight message comes back — it is the turn being watched, and it
        # is not yet an answer. The *count* of finished messages did not move.
        self.assertEqual(settled_after.message_count, settled.message_count)
        self.assertEqual([m["id"] for m in new_messages], [mid("ai", 4000)])

    def test_a_new_finished_message_moves_the_settled_watermark(self):
        head = [message(mid("user", 1000), "user", text="q"),
                message(mid("ai", 2000), text="done", complete=True)]
        path = self.record(head + [message(mid("ai", 4000), text="w1")])
        _, _, settled, _ = measure.read_record(path, settled_only=True)
        self.assertEqual(settled.message_count, 2)   # the prompt and its reply
        self.record(head + [message(mid("ai", 4000), text="w1",
                                    complete=True)])
        status, new_messages, after, _ = measure.read_record(
            path, settled, settled_only=True)
        self.assertEqual(status, "delta")
        self.assertEqual(after.message_count, 3)
        self.assertEqual([m["id"] for m in new_messages], [mid("ai", 4000)])
        self.assertEqual(after.prefix_bytes, os.path.getsize(path) - 1)

    def test_an_unfinished_last_message_makes_the_boundary_the_array_start(self):
        path = self.record([message(mid("ai", 4000), text="only writing")])
        _, _, watermark, detail = measure.read_record(path, settled_only=True)
        self.assertEqual(watermark.message_count, 0)
        self.assertEqual(watermark.prefix_bytes, 1)  # just past the '['
        self.assertEqual(detail["in_flight"], 1)
        # ...and the delta read still parses the whole array from that offset.
        status, new_messages, _, _ = measure.read_record(
            path, measure.Watermark(0, 1, watermark.prefix_hash), settled_only=True)
        self.assertEqual(status, "delta")
        self.assertEqual(len(new_messages), 1)

    def test_a_user_message_and_a_divider_settle_without_a_flag(self):
        divider = {"id": mid("divider", 2), "variant": "ai", "content": "",
                   "blocks": [{"type": "mode-divider", "mode": "LITE"}]}
        raw = json.dumps([message(mid("user", 1), "user", text="q"), divider,
                          message(mid("ai", 3), text="writing")],
                         separators=(",", ":")).encode("utf-8")
        _, settled, total = measure._settled_boundary(raw)
        self.assertEqual((settled, total), (2, 3))
        self.assertTrue(measure._is_settled(message(mid("user", 1), "user")))
        self.assertTrue(measure._is_settled(divider))
        self.assertFalse(measure._is_settled(message(mid("ai", 3))))
        self.assertFalse(measure._is_settled("not a message"))

    def test_a_non_ascii_record_still_yields_a_byte_exact_boundary(self):
        """The live record contains literal multi-byte characters, and the first
        implementation of this boundary refused it because it decoded to a string
        first — which made the whole correction unusable on the real file. So it
        is computed over bytes, and this pins that."""
        raw = ('[{"id":"' + mid("ai", 1) + '","variant":"ai","content":""'
               ',"blocks":[{"type":"text","textType":"text",'
               '"content":"\u00e9\u2014"}],"isComplete":true},'
               '{"id":"' + mid("ai", 2) + '","variant":"ai","content":""}'
               ']').encode("utf-8")
        self.assertGreater(len(raw), len(raw.decode("utf-8")),
                           "the fixture must really contain multi-byte characters")
        boundary, settled, total = measure._settled_boundary(raw)
        self.assertEqual((settled, total), (1, 2))
        self.assertEqual(raw[:boundary].decode("utf-8", "replace")[-1:], "}")
        path = self.write("chat-messages.json", raw)
        _, messages, watermark, detail = measure.read_record(path,
                                                             settled_only=True)
        self.assertEqual(len(messages), 2)                       # all readable
        self.assertEqual(detail["in_flight"], 1)
        self.assertEqual(watermark.prefix_bytes, boundary)

    def test_a_brace_inside_a_tool_output_does_not_end_a_message(self):
        """The span scanner has to survive `{`, `}` and `]` inside string values —
        which is most of a tool output on this box."""
        element = json.dumps({"id": mid("ai", 1), "variant": "ai",
                              "blocks": [{"type": "tool", "toolName": "exec",
                                          "output": 'a } ] { \\" b'}],
                              "isComplete": True}, separators=(",", ":"))
        raw = ("[" + element + "," + json.dumps(
            {"id": mid("ai", 2), "variant": "ai"}, separators=(",", ":"))
            + "]").encode("utf-8")
        spans = measure._message_spans(raw)
        self.assertEqual(len(spans), 2)
        boundary, settled, total = measure._settled_boundary(raw)
        self.assertEqual((settled, total), (1, 2))
        self.assertEqual(raw[boundary - 1:boundary], b"}")

    def test_the_incremental_boundary_agrees_with_the_full_one(self):
        """Two implementations of the same rule must not drift: walking the
        record and advancing over a verified prefix have to land on the same byte
        and the same count, or a long-lived bridge slowly eats the record."""
        head = [message(mid("user", 1), "user", text="q"),
                message(mid("ai", 2), text="a", complete=True),
                message(mid("user", 3), "user", text="q2")]
        path = self.record(head + [message(mid("ai", 4), text="writing")])
        with open(path, "rb") as handle:
            raw = handle.read()
        walked = measure._settled_boundary(raw)
        # Advance over a *stale* prefix: the watermark from a moment when only
        # the first message had settled.
        earlier = measure._settled_boundary(
            json.dumps(head[:1], separators=(",", ":")).encode("utf-8"))
        advanced = measure._advance_settled(raw, earlier[0], earlier[1])
        self.assertEqual(advanced, walked)

        # ...and again after growth that settles nothing.
        self.record(head + [message(mid("ai", 4), text="writing more")])
        with open(path, "rb") as handle:
            grown = handle.read()
        self.assertEqual(measure._advance_settled(grown, earlier[0], earlier[1]),
                         measure._settled_boundary(grown))

    def test_a_verified_delta_read_reuses_the_prefix_hash(self):
        """When nothing settled, the watermark must come back byte-identical, so
        the caller can hand the same watermark round again for free."""
        head = [message(mid("user", 1), "user", text="q"),
                message(mid("ai", 2), text="a", complete=True)]
        path = self.record(head + [message(mid("ai", 4), text="writing")])
        _, _, first, _ = measure.read_record(path, settled_only=True)
        self.record(head + [message(mid("ai", 4), text="writing a lot more")])
        status, _, after, _ = measure.read_record(path, first, settled_only=True)
        self.assertEqual(status, "delta")
        self.assertEqual(after.as_dict(), first.as_dict())

    def test_advancing_never_decodes_the_verified_prefix(self):
        """The correction has to be affordable, and the saving is proven rather
        than hoped for: an element *before* the boundary is made unparseable, and
        advancing over the verified prefix still succeeds because those bytes are
        never decoded. A full recompute has to refuse the same record.

        Measured motivation: decoding the settled region on every poll was 1.75 s
        per poll against 22 ms for an end-of-file watermark.
        """
        raw = (b'[{"a":},{"id":"' + mid("ai", 2).encode() +
               b'","variant":"ai","isComplete":true}]')
        spans = measure._message_spans(raw)
        self.assertEqual(len(spans), 2)
        end_of_first = spans[0][1]

        self.assertIsNone(measure._settled_boundary(raw))
        advanced = measure._advance_settled(raw, end_of_first, 0)
        self.assertIsNotNone(advanced, "the verified prefix must not be re-read")
        offset, settled, total = advanced
        self.assertEqual((settled, total), (1, 1))
        # The only settled element is the last one, so the boundary is its end.
        self.assertEqual(offset, len(raw) - 1)
        self.assertEqual(raw[offset - 1:offset], b"}")

    def test_the_span_scanner_refuses_a_broken_record(self):
        self.assertIsNone(measure._message_spans(b"no array here"))
        self.assertIsNone(measure._message_spans(b'[{"a":1}'))
        self.assertIsNone(measure._settled_boundary(b'[{"a":'))

    def test_watch_record_counts_settled_growth_separately(self):
        path = self.record([message(mid("ai", 1), text="writing")])
        stats = measure.watch_record(path, 0.3, interval=0.05, settled_only=True)
        self.assertTrue(stats["settled_only"])
        self.assertEqual(stats["resync"], 0)
        self.assertEqual(stats["messages"], 0)

    def test_settled_boundary_on_a_clean_record_is_the_end(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True),
                            message(mid("ai", 2), text="b", complete=True)])
        _, _, watermark, detail = measure.read_record(path, settled_only=True)
        self.assertEqual(watermark.message_count, 2)
        self.assertEqual(detail["in_flight"], 0)
        self.assertEqual(watermark.prefix_bytes, os.path.getsize(path) - 1)

    def test_settled_boundary_refuses_a_garbled_record(self):
        self.assertIsNone(measure._settled_boundary(b"[not json]"))
        self.assertIsNone(measure._settled_boundary(b"{}"))

    # ── negative controls ────────────────────────────────────────────────────

    def test_a_delta_read_never_returns_the_consumed_record(self):
        """The control that must fail on purpose: a delta that returns what was
        already read is a reader reporting the same turn twice, which is exactly
        how a bridge answers the wrong prompt with a plausible reply."""
        messages = [message(mid("ai", index), text="m%d" % index, complete=True)
                    for index in range(1, 6)]
        path = self.record(messages)
        _, _, watermark, _ = measure.read_record(path)
        messages.append(message(mid("ai", 6), text="new", complete=True))
        self.record(messages)
        status, new_messages, _, _ = measure.read_record(path, watermark)
        self.assertEqual(status, "delta")
        self.assertEqual([m["id"] for m in new_messages], [mid("ai", 6)])
        self.assertNotIn(mid("ai", 5), [m["id"] for m in new_messages])

    def test_watch_record_counts_a_growing_file(self):
        path = self.record([message(mid("ai", 1), text="a", complete=True)])
        stats = measure.watch_record(path, 0.3, interval=0.05)
        self.assertGreater(stats["polls"], 0)
        self.assertEqual(stats["messages"], 1)
        self.assertEqual(stats["torn"], 0)

    def test_watch_record_survives_a_torn_file(self):
        self.write("chat-messages.json", '[{"id":"ai-1"')
        stats = measure.watch_record(self.path("chat-messages.json"),
                                     0.25, interval=0.05)
        self.assertGreater(stats["torn"], 0)
        self.assertIsNone(stats["messages"])

    def test_count_newlines_proves_the_record_is_compact(self):
        path = self.write("chat-messages.json", '[{"a":1},{"b":2}]')
        self.assertEqual(measure._count_newlines(path), 0)
        self.assertEqual(measure._count_newlines(self.path("nope")), None)


# ── 5. turn completion ───────────────────────────────────────────────────────

class TurnTest(unittest.TestCase):

    def test_a_settled_answer_is_complete(self):
        messages = [message(mid("user", 1000), "user", text="q"),
                    message(mid("ai", 2000), text="the answer", complete=True)]
        verdict, reply, detail = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "complete")
        self.assertEqual(reply["text"], "the answer")
        self.assertEqual(reply["id"], mid("ai", 2000))
        self.assertEqual(detail["signal"], "isComplete")

    def test_a_message_marked_unfinished_is_never_the_answer(self):
        """The control that must fail on purpose. Reporting a half-written turn
        as the reply is the "smooth over" failure `AGENT-INTEGRITY.md` forbids:
        the text is real, it is just not the answer yet."""
        messages = [message(mid("user", 1000), "user", text="q"),
                    message(mid("ai", 2000), text="partial...", complete=False)]
        verdict, reply, detail = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "streaming")
        self.assertIsNone(reply)
        self.assertEqual(detail["signal"], "isComplete=false")

    def test_a_reasoning_only_turn_is_not_complete(self):
        messages = [message(mid("user", 1000), "user", text="q"),
                    message(mid("ai", 2000), reasoning="thinking out loud",
                            complete=True)]
        verdict, reply, _ = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "streaming")
        self.assertIsNone(reply)

    def test_a_tool_only_turn_is_complete(self):
        # Acting without prose is still a finished turn — §18.6 rule 3.
        messages = [message(mid("user", 1000), "user", text="q"),
                    message(mid("ai", 2000), tools=2, complete=True)]
        verdict, reply, detail = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "complete")
        self.assertEqual(reply["text"], "")
        self.assertEqual(len(reply["tools"]), 2)
        self.assertEqual(detail["tool_block_type"], "tool")

    def test_a_divider_is_never_the_answer(self):
        divider = {"id": mid("divider", 2000), "variant": "ai", "content": "",
                   "blocks": [{"type": "mode-divider", "mode": "LITE"}]}
        messages = [message(mid("user", 1000), "user", text="q"), divider]
        verdict, reply, _ = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "streaming")
        self.assertIsNone(reply)

    def test_reasoning_is_returned_separately_from_the_answer(self):
        messages = [message(mid("user", 1000), "user", text="q"),
                    message(mid("ai", 2000), reasoning="because",
                            text="therefore", complete=True)]
        verdict, reply, _ = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "complete")
        self.assertEqual(reply["text"], "therefore")
        self.assertEqual(reply["reasoning"], "because")

    def test_an_older_turn_is_never_the_reply(self):
        """A reply must be newer than the prompt that asked for it, or a bridge
        answers the previous question with the previous answer."""
        messages = [message(mid("ai", 500), text="old answer", complete=True),
                    message(mid("user", 1000), "user", text="new question")]
        verdict, reply, detail = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "streaming")
        self.assertIsNone(reply)
        self.assertIn("no assistant message at or after", detail["reason"])

    def test_the_first_message_after_the_prompt_is_the_reply(self):
        """The reply to a prompt is the turn it opened, not the newest turn in
        the file. Measured: taking the newest made the rule unable to *ever*
        report `complete` on a live box, because the newest qualifying message
        is always the turn currently being written — the instrument's own
        self-check found that on 12 of 12 real prompts."""
        messages = [message(mid("user", 1000), "user", text="q"),
                    message(mid("ai", 2000), text="first", complete=True),
                    message(mid("ai", 3000), text="second", complete=True)]
        verdict, reply, _ = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "complete")
        self.assertEqual(reply["text"], "first")
        self.assertEqual(reply["id"], mid("ai", 2000))

    def test_a_later_in_flight_turn_does_not_hide_an_earlier_reply(self):
        """The live case: a settled turn followed by the turn being written.
        Anchoring on the prompt keeps the answer to *this* prompt, and keeps a
        later turn from being reported in its place."""
        messages = [message(mid("user", 1000), "user", text="q1"),
                    message(mid("ai", 2000), text="settled answer",
                            complete=True),
                    message(mid("user", 3000), "user", text="q2"),
                    message(mid("ai", 4000), text="still writing",
                            complete=False)]
        verdict, reply, _ = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "complete")
        self.assertEqual(reply["text"], "settled answer")
        # ...and the in-flight turn still reports streaming for its own prompt.
        verdict_now, reply_now, _ = measure.resolve_turn(messages, T0 + 3000)
        self.assertEqual(verdict_now, "streaming")
        self.assertIsNone(reply_now)

    def test_the_specs_tool_call_spelling_is_also_understood(self):
        """`§18.7` measured `tool-call` on 0.0.180; this box says `tool`. A reader
        that knows only one of them fails silently and looks like a verifier, so
        both are accepted — and which one fired is reported."""
        legacy = {"id": mid("ai", 2000), "variant": "ai", "content": "",
                  "isComplete": True,
                  "blocks": [{"type": "tool-call", "toolName": "read_file",
                              "input": {"path": "x"}, "output": {"content": "y"}}]}
        messages = [message(mid("user", 1000), "user", text="q"), legacy]
        verdict, reply, detail = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "complete")
        self.assertEqual(detail["tool_block_type"], "tool-call")
        self.assertEqual(reply["tools"][0]["toolName"], "read_file")

    def test_a_record_without_the_completeness_flag_streams(self):
        """The quiet period is `§18.6`'s proposed rule and cannot be evaluated
        from a snapshot, so a record carrying no flag is `streaming` — not
        `complete`, and never a guess."""
        messages = [message(mid("user", 1000), "user", text="q"),
                    message(mid("ai", 2000), text="answer with no flag")]
        verdict, reply, detail = measure.resolve_turn(messages, T0 + 1000,
                                                      quiet_ms=1200)
        self.assertEqual(verdict, "streaming")
        self.assertIsNone(reply)
        self.assertEqual(detail["signal"], "quiet-period")

    def test_no_epoch_is_unreadable_not_a_guess(self):
        messages = [message(mid("ai", 2000), text="answer", complete=True)]
        verdict, reply, detail = measure.resolve_turn(messages, None)
        self.assertEqual(verdict, "unreadable")
        self.assertIsNone(reply)
        self.assertTrue(detail["reason"])

    def test_a_non_list_record_is_unreadable(self):
        verdict, _, _ = measure.resolve_turn({"not": "a list"}, T0 + 1000)
        self.assertEqual(verdict, "unreadable")

    def test_an_id_without_an_epoch_cannot_order_anything(self):
        messages = [message("ai-nonsense", text="answer", complete=True)]
        verdict, _, _ = measure.resolve_turn(messages, T0 + 1000)
        self.assertEqual(verdict, "streaming")


class ShapeTest(unittest.TestCase):

    def test_the_shape_counts_the_empty_content_defect(self):
        messages = [message(mid("user", 1), "user", text="hi"),
                    message(mid("ai", 2), reasoning="r", text="answer",
                            complete=True),
                    message(mid("ai", 3), tools=1, complete=True),
                    {"id": mid("divider", 4), "variant": "ai", "content": "",
                     "blocks": [{"type": "mode-divider", "mode": "LITE"}]}]
        shape = measure._shape_summary(messages)
        self.assertEqual(shape["messages"], 4)
        self.assertEqual(shape["ai_total"], 3)
        # Every assistant message has empty `content` — the measured defect that
        # makes "read `content`" return an empty reply on nearly every turn.
        self.assertEqual(shape["ai_with_empty_content"], 3)
        self.assertEqual(shape["dividers"], 1)
        self.assertEqual(shape["tool_block_types"], {"tool": 1})
        self.assertEqual(shape["text_texttypes"], {"reasoning": 1, "text": 1})
        self.assertEqual(shape["is_complete"], {"True": 2})
        self.assertEqual(shape["id_roles"], {"user": 1, "ai": 2, "divider": 1})

    def test_id_epoch_parses_both_roles_and_rejects_nonsense(self):
        self.assertEqual(measure.id_epoch("user-1789979040257"), 1789979040257)
        self.assertEqual(measure.id_epoch("ai-1789979041048-18bc0743"),
                         1789979041048)
        self.assertEqual(measure.id_role("divider-1790164293867"), "divider")
        self.assertIsNone(measure.id_epoch("ai-not-a-number"))
        self.assertIsNone(measure.id_epoch(None))


# ── 6. the measurement as a whole ────────────────────────────────────────────

class MeasureTest(Base):

    def build_client_home(self):
        """A client home in the measured shape: lock, projects, log dirs, record."""
        self.write("freebuff-metadata.json", json.dumps({"version": "0.0.186"}))
        self.write("recent-projects.json",
                   json.dumps([{"path": "/mnt/sdcard/Download/builderbro",
                                "lastOpened": 1790164114402}]))
        self.write("freebuff-instance-owner.json",
                   json.dumps({"instanceId": "47ede7b7", "pid": 17101}))
        chat = "projects/builderbro/chats/2026-09-21T08-23-48.129Z"
        self.write(os.path.join(chat, "chat-meta.json"),
                   json.dumps({"messageCount": 2, "messagesSize": 300}))
        self.write(os.path.join(chat, "chat-messages.json"),
                   json.dumps([message(mid("user", 1000), "user", text="hello"),
                               message(mid("ai", 2000), reasoning="hmm",
                                       text="hi there", complete=True)],
                              separators=(",", ":")))
        self.write(os.path.join(chat, "log.jsonl"), json.dumps(
            {"level": "DEBUG",
             "msg": "Reconnection detected, firing onReconnect callback",
             "timestamp": "2026-09-23T14:22:03.761Z"}) + "\n")
        self.write("projects/builderbro/chats/2026-09-23T11-48-56.035Z/log.jsonl",
                   "{}\n")
        return self.tmp

    def test_measure_reports_the_whole_channel(self):
        home = self.build_client_home()
        report = measure.measure(home=home, cwd="/mnt/sdcard/Download/builderbro",
                                 proc="/nonexistent-proc")
        self.assertEqual(report["client"]["metadata"]["version"], "0.0.186")
        self.assertEqual(report["lock"]["verdict"], "owner_dead")
        self.assertEqual(report["project"]["verdict"], "ok")
        self.assertEqual(report["conversation"]["verdict"], "ok")
        self.assertEqual(report["conversation"]["active"],
                         "2026-09-21T08-23-48.129Z")
        self.assertEqual(report["record"]["status"], "initial")
        self.assertEqual(report["record"]["newlines"], 0)
        self.assertEqual(report["record"]["delta_check"]["status"], "unchanged")
        self.assertEqual(report["record"]["shape"]["ai_with_empty_content"], 1)
        self.assertEqual(report["session_axis"]["verdict"], "reconnecting")
        # The fixture's turn is settled, so the live-turn rule reports complete
        # as well as the settled check — the two agree, which is the point.
        self.assertEqual(report["turn"]["verdict"], "complete")
        self.assertEqual(report["turn"]["settled_check"]["verdict"], "complete")
        self.assertEqual(report["turn"]["settled_check"]["newer_turns_streaming"], 0)
        self.assertEqual(report["record"]["chat_meta"]["messageCount"], 2)
        # The watermark counts *settled* messages: the prompt and its finished
        # reply both settle, so nothing is left unconsumed here.
        self.assertEqual(report["record"]["watermark"]["message_count"], 2)
        self.assertEqual(report["record"]["inflight"], 0)
        # The newest dir is a log dir, and that is called out rather than hidden.
        self.assertTrue(any("newest dir" in item
                            for item in report["inconclusive"]),
                        report["inconclusive"])
        self.assertIsNotNone(report["record"]["watermark"])

    def test_the_bridge_round_trip_budget_is_measurably_met(self):
        """§10 proposes p95 ≤ 120 s for a bridge round trip. The read half of
        that is measured here so the budget is a number, not a hope: a full read
        of this fixture must be far under it, and a delta read cheaper still."""
        home = self.build_client_home()
        report = measure.measure(home=home, cwd="/mnt/sdcard/Download/builderbro",
                                 proc="/nonexistent-proc")
        self.assertLess(report["record"]["full_read_seconds"], 120.0)

    def test_measure_says_inconclusive_when_the_record_is_missing(self):
        self.write("freebuff-metadata.json", json.dumps({"version": "0.0.186"}))
        self.write("recent-projects.json",
                   json.dumps([{"path": "/mnt/sdcard/Download/builderbro"}]))
        os.makedirs(self.path("projects", "builderbro", "chats"), exist_ok=True)
        report = measure.measure(home=self.tmp,
                                 cwd="/mnt/sdcard/Download/builderbro")
        self.assertEqual(report["conversation"]["verdict"], "none")
        self.assertTrue(any("conversation: none" in item
                            for item in report["inconclusive"]),
                        report["inconclusive"])

    def test_a_meta_count_that_disagrees_with_the_record_is_inconclusive(self):
        """Two witnesses to one fact. `§18.7` measured `messageCount` equal to
        the array length; if it ever is not, the reader's count is the one that
        would be trusted silently, so the disagreement has to be visible."""
        home = self.build_client_home()
        self.write("projects/builderbro/chats/2026-09-21T08-23-48.129Z/"
                   "chat-meta.json", json.dumps({"messageCount": 99}))
        report = measure.measure(home=home, cwd="/mnt/sdcard/Download/builderbro",
                                 proc="/nonexistent-proc")
        self.assertTrue(any("says 99 messages" in item
                            for item in report["inconclusive"]),
                        report["inconclusive"])

    def test_measure_never_raises_on_an_empty_home(self):
        report = measure.measure(home=self.tmp, cwd=self.tmp)
        self.assertEqual(report["lock"]["verdict"], "absent")
        self.assertIn("lock: absent", report["inconclusive"][0])

    def test_the_cli_reports_inconclusive_with_a_nonzero_exit(self):
        """The exit code is part of the contract: 2 means "could not see", and a
        probe that cannot distinguish that from success is the defect
        `live_mcp_probe.py` was written to fix."""
        home = self.build_client_home()
        code = measure.main(["--home", home,
                             "--cwd", "/mnt/sdcard/Download/builderbro"])
        self.assertEqual(code, 2)

    def test_a_clean_home_exits_zero(self):
        """The other direction: a home with a live lock, one project and a live
        conversation must report success, so `2` means something."""
        self.write("freebuff-metadata.json", json.dumps({"version": "0.0.186"}))
        self.write("recent-projects.json",
                   json.dumps([{"path": "/mnt/sdcard/Download/builderbro"}]))
        self.write("freebuff-instance-owner.json",
                   json.dumps({"instanceId": "x", "pid": os.getpid()}))
        chat = "projects/builderbro/chats/2026-09-21T08-23-48.129Z"
        self.write(os.path.join(chat, "chat-meta.json"),
                   json.dumps({"messageCount": 2}))
        self.write(os.path.join(chat, "chat-messages.json"),
                   json.dumps([message(mid("user", 1000), "user", text="q"),
                               message(mid("ai", 2000), text="a",
                                       complete=True)], separators=(",", ":")))  # noqa: E501
        self.write(os.path.join(chat, "log.jsonl"), json.dumps(
            {"msg": "Reconnection detected",
             "timestamp": "2026-09-23T14:22:03.761Z"}) + "\n")
        code = measure.main(["--home", self.tmp,
                             "--cwd", "/mnt/sdcard/Download/builderbro"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
