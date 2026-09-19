#!/usr/bin/env python3
"""memory_test.py — tests for episodic memory with provenance (A2).

Hermetic: scripted model, generated fixture, `emit` off, no network. What the
tests are actually for:

* **The level mapping is the boundary.** A3's `declared` (the model's own
  expectation with the verifier off) must become `volatile`, or turning the
  verifier off becomes a way to launder a claim into a remembered fact. That is
  asserted through the real loop, not only on the mapping function.
* **False recall must be a property of the code, not of a prompt.** `render` is
  built so a non-invariant cannot reach the facts section, and that is tested
  per level; the adversarial cases are the ones that matter, since a renderer
  that puts everything in the facts block would pass an honest-only test.
* **Memory informs planning and never feeds the gate.** A goal that would hold
  over a recalled fact but not over this run's evidence is refused, and says
  `recall_gap` — the diagnosis, not a completion.

Run: python3 memory_test.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_runtime
import autonomy
import loop_guard
import memory
import test_support

test_support.isolate_residence()

MARK = "alpha-token"
OTHER = "beta-token"


def _replies(replies, recorder=None):
    box = {"i": 0}

    def chat(config, messages, temperature=None, max_tokens=None, timeout_s=None,
             stop_when=None, **_kw):
        if recorder is not None:
            recorder.append([dict(m) for m in messages])
        i = box["i"]
        box["i"] += 1
        return {"content": replies[min(i, len(replies) - 1)], "elapsed": 0.01,
                "tokens": 8, "early_stop": True, "provider": "stub", "model": "stub"}

    return chat


def _plan(steps, goal):
    lines = ["PLAN:"]
    for i, (tool, arg, expect) in enumerate(steps, 1):
        lines.append("%d. tool: %s %s" % (i, tool, arg))
        lines.append("   expect: %s" % expect)
    lines.append("GOAL-CHECK: %s" % goal)
    return "\n".join(lines)


def _call(tool, arg):
    return "<<<TOOL:%s %s>>>" % (tool, arg)


def _rec(tool="list_dir", arg=".", value="README.md", level="invariant",
         verified="list_dir:expectation_held+not_failure+not_vacuous+reproduced",
         goal="find the file"):
    return memory.make_record(goal, tool, arg, value, level=level,
                              how_verified=verified)


# ── Levels ────────────────────────────────────────────────────────────────────

class LevelTest(unittest.TestCase):
    def test_the_three_integrity_levels_are_the_ones_defined(self):
        self.assertEqual(memory.LEVELS, ("invariant", "observed", "volatile"))
        self.assertEqual(memory.PROMOTABLE, ("invariant",))

    def test_verifier_levels_map_by_confirmation_strength(self):
        self.assertEqual(memory.level_for("invariant"), "invariant")
        self.assertEqual(memory.level_for("observed"), "observed")
        # The load-bearing row: no independent check means a claim, not a fact.
        self.assertEqual(memory.level_for("declared"), "volatile")

    def test_an_unknown_level_degrades_down_not_up(self):
        self.assertEqual(memory.level_for("probably-fine"), "volatile")
        self.assertEqual(memory.level_for(None), "volatile")

    def test_a_record_refuses_an_invented_level(self):
        with self.assertRaises(memory.ProvenanceError):
            _rec(level="certain")

    def test_a_long_value_is_truncated_and_says_so(self):
        short = _rec(value="x" * 10, level="observed")
        long = memory.make_record("g", "list_dir", ".", "y" * 900, level="observed",
                                  how_verified="c", value_chars=100)
        self.assertFalse(short["value_truncated"])
        self.assertEqual(len(long["value"]), 101)   # 100 chars + the ellipsis
        self.assertTrue(long["value_truncated"])
        self.assertEqual(long["chars"], 900)        # the real size is kept

    def test_a_multiline_value_becomes_one_line(self):
        rec = memory.make_record("g", "list_dir", ".", "a\n\n  b\tc \n",
                                 level="observed", how_verified="c")
        self.assertEqual(rec["value"], "a b c")


# ── The store ─────────────────────────────────────────────────────────────────

class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="memory-test-")
        self.path = os.path.join(self.dir, "memory.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_add_records_len_and_clear_round_trip(self):
        s = memory.MemoryStore(self.path)
        self.assertEqual(s.records(), [])
        self.assertEqual(len(s), 0)
        s.add(_rec())
        s.add(_rec(tool="read_file"))
        self.assertEqual(len(s), 2)
        self.assertEqual([r["tool"] for r in s.records()], ["list_dir", "read_file"])
        s.clear()
        self.assertEqual(len(s), 0)

    def test_a_missing_file_is_no_history_not_an_error(self):
        self.assertEqual(memory.MemoryStore(
            os.path.join(self.dir, "nothing-here.jsonl")).records(), [])

    def test_a_corrupt_line_is_skipped_and_the_rest_survive(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps(_rec(), sort_keys=True) + "\n")
            f.write("{not json at all\n")
            f.write("\n")
            f.write(json.dumps(_rec(tool="read_file"), sort_keys=True) + "\n")
        recs = memory.MemoryStore(self.path).records()
        self.assertEqual([r["tool"] for r in recs], ["list_dir", "read_file"])

    def test_a_write_creates_the_directory(self):
        nested = os.path.join(self.dir, "residence", "memory.jsonl")
        memory.MemoryStore(nested).add(_rec())
        self.assertTrue(os.path.isfile(nested))

    def test_open_store_distinguishes_none_from_a_named_store(self):
        self.assertIsNone(memory.open_store(None))
        s = memory.MemoryStore(self.path)
        self.assertIs(memory.open_store(s), s)
        by_path = memory.open_store(self.path)
        self.assertIsInstance(by_path, memory.MemoryStore)
        self.assertEqual(by_path.path, self.path)


# ── Recall ────────────────────────────────────────────────────────────────────

class RecallTest(unittest.TestCase):
    def test_relevance_is_zero_when_either_side_is_empty(self):
        self.assertEqual(memory.relevance(_rec(), set()), 0.0)
        self.assertEqual(memory.relevance({"goal": "", "tool": "", "arg": ""},
                                          {"marker"}), 0.0)

    def test_relevance_is_the_covered_fraction_of_the_query(self):
        rec = _rec(tool="list_dir", arg="research", goal="find marker file")
        score = memory.relevance(rec, {"marker", "file", "elsewhere"})
        self.assertAlmostEqual(score, 2 / 3.0, places=4)

    def test_scaffolding_words_do_not_make_a_record_relevant(self):
        # Every plan says "read the file"; without the stop list that alone would
        # recall every record for every goal.
        self.assertEqual(memory.content_tokens("read the file"), set())
        recs = [_rec(value="unrelated")]
        self.assertEqual(memory.recall(recs, "read the file"), [])

    def test_min_score_and_limit_are_both_honoured(self):
        # `marker` is the whole query, deliberately: with a single content word
        # the relevance of each record is known by construction rather than being
        # a property of the stop list.
        recs = [_rec(goal="marker"),
                _rec(tool="read_file", arg="notes", goal="marker"),
                _rec(goal="water the plants")]
        self.assertEqual(len(memory.recall(recs, "marker", min_score=0.6)), 2)
        self.assertEqual(len(memory.recall(recs, "marker", min_score=1.1)), 0)
        limited = memory.recall(recs, "marker", limit=1)
        self.assertEqual(len(limited), 1)
        self.assertIn("score", limited[0])

    def test_ordering_is_deterministic_across_calls(self):
        recs = [_rec(arg="a", goal="find marker"), _rec(arg="b", goal="find marker"),
                _rec(arg="c", goal="find marker")]
        one = [r["arg"] for r in memory.recall(recs, "find marker", limit=3)]
        two = [r["arg"] for r in memory.recall(list(reversed(recs)), "find marker",
                                               limit=3)]
        self.assertEqual(one, two)

    def test_equally_scored_records_break_the_tie_on_the_newest(self):
        old = memory.make_record("find marker", "list_dir", ".", "x", level="observed",
                                 how_verified="c", ts="2026-01-01T00:00:00+00:00")
        new = memory.make_record("find marker", "list_dir", ".", "x", level="observed",
                                 how_verified="c", ts="2026-09-01T00:00:00+00:00")
        got = memory.recall([old, new], "find marker", limit=2)
        self.assertEqual([r["ts"] for r in got],
                         ["2026-09-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"])


# ── Render: the false-recall boundary ────────────────────────────────────────

class RenderTest(unittest.TestCase):
    def test_nothing_recalled_renders_nothing(self):
        self.assertEqual(memory.render([]), "")

    def test_only_an_invariant_lands_above_the_unverified_header(self):
        for level in memory.LEVELS:
            block = memory.render([_rec(value=level + " value here", level=level)])
            head = block.split(memory.UNVERIFIED_HEADER, 1)[0]
            if level == "invariant":
                self.assertIn(level + " value here", head)
            else:
                self.assertNotIn(level + " value here", head)
                self.assertIn(level + " value here", block)

    def test_without_a_fact_the_facts_section_is_explicitly_empty(self):
        block = memory.render([_rec(value="some observed thing", level="observed")])
        head = block.split(memory.UNVERIFIED_HEADER, 1)[0]
        self.assertIn("(none)", head)
        self.assertNotIn("some observed thing", head)

    def test_the_unverified_header_states_the_constraint_rule(self):
        block = memory.render([_rec(level="volatile", value="a model claim")])
        self.assertIn("never use it as a constraint", memory.UNVERIFIED_HEADER)
        self.assertIn("never use it as a constraint", block)

    def test_a_fact_names_the_check_that_confirmed_it(self):
        block = memory.render([_rec(level="invariant", value="v",
                                    verified="list_dir:reproduced")])
        self.assertIn("list_dir:reproduced", block)

    def test_the_block_is_capped_and_says_it_was_cut(self):
        items = [_rec(tool="read_file", arg="f%d" % i, value="z" * 300,
                      level="observed") for i in range(10)]
        block = memory.render(items, value_chars=300, max_chars=500)
        self.assertEqual(len(block), 501)

    def test_facts_and_unverified_are_a_partition(self):
        items = [_rec(level="invariant"), _rec(level="observed"), _rec(level="volatile")]
        self.assertEqual(len(memory.facts(items)) + len(memory.unverified(items)),
                         len(items))
        self.assertEqual([r["level"] for r in memory.facts(items)], ["invariant"])


# ── The audit ─────────────────────────────────────────────────────────────────

class AuditTest(unittest.TestCase):
    def test_a_clean_block_has_no_violations(self):
        items = [_rec(level="invariant", value="a confirmed value here"),
                 _rec(level="observed", value="a merely observed value")]
        block = memory.render(items)
        self.assertEqual(memory.audit(items, block)["violations"], [])

    def test_a_leaked_unverified_value_is_detected(self):
        # A hand-built block that does exactly what render must never do.
        items = [_rec(level="volatile", value="a claim that leaked upward")]
        bad = "Known facts:\n- list_dir . -> a claim that leaked upward [volatile]"
        violations = memory.audit(items, bad)["violations"]
        self.assertEqual([v["kind"] for v in violations],
                         ["unverified_value_in_facts"])
        self.assertEqual(violations[0]["level"], "volatile")

    def test_an_invariant_without_a_named_check_is_flagged(self):
        items = [memory.make_record("g", "list_dir", ".", "value text", level="invariant",
                                    how_verified="")]
        violations = memory.audit(items, memory.render(items))["violations"]
        self.assertEqual([v["kind"] for v in violations],
                         ["fact_without_named_check"])

    def test_a_value_too_short_to_search_is_counted_as_unchecked(self):
        items = [_rec(level="volatile", value="TOK")]
        report = memory.audit(items, memory.render(items))
        self.assertEqual((report["checked"], report["unchecked"], report["violations"]),
                         (0, 1, []))
        # The same record with a searchable value is actually checked.
        items = [_rec(level="volatile", value="TOK-12345678")]
        report = memory.audit(items, memory.render(items))
        self.assertEqual((report["checked"], report["unchecked"]), (1, 0))


# ── Promotion ─────────────────────────────────────────────────────────────────

class ProvenanceTest(unittest.TestCase):
    def test_an_observed_record_may_not_be_used_as_a_fact(self):
        with self.assertRaises(memory.ProvenanceError):
            memory.as_fact(_rec(level="observed"))

    def test_a_volatile_record_may_not_be_used_as_a_fact(self):
        with self.assertRaises(memory.ProvenanceError):
            memory.as_fact(_rec(level="volatile"))

    def test_an_invariant_is_returned_unchanged(self):
        rec = _rec(level="invariant")
        self.assertIs(memory.as_fact(rec), rec)

    def test_observed_promotes_only_with_a_named_check(self):
        rec = _rec(level="observed")
        with self.assertRaises(memory.ProvenanceError):
            memory.promote(rec, how_verified="  ")
        out = memory.promote(rec, how_verified="list_dir:reproduced")
        self.assertEqual(out["level"], "invariant")
        self.assertEqual(out["promoted_from"], "observed")
        self.assertEqual(rec["level"], "observed")   # the input is not mutated

    def test_a_volatile_record_is_not_one_check_away_from_a_fact(self):
        with self.assertRaises(memory.ProvenanceError):
            memory.promote(_rec(level="volatile"), how_verified="looks fine")

    def test_promotion_to_a_non_fact_level_is_refused(self):
        with self.assertRaises(memory.ProvenanceError):
            memory.promote(_rec(level="observed"), how_verified="c",
                           to_level="volatile")


# ── Through the real loop ─────────────────────────────────────────────────────

class LoopMemoryTest(unittest.TestCase):
    """The wiring: what the loop reads, what it writes, and what it refuses."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="memory-loop-test-")
        with open(os.path.join(self.root, "notes.md"), "w", encoding="utf-8") as f:
            f.write("%s appears here\n" % MARK)
        self.dir = tempfile.mkdtemp(prefix="memory-store-test-")
        self.store_path = os.path.join(self.dir, "memory.jsonl")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_loop(self, replies, goal="test goal", **kw):
        recorder = kw.pop("recorder", None)
        guard = loop_guard.LoopGuard(kw.pop("max_steps", 6), emit=False)
        result = autonomy.run_goal_verified(None, goal, guard=guard,
                                            chat_fn=_replies(replies, recorder),
                                            emit=False, **kw)
        return result, guard

    def block_of(self, batches):
        for messages in batches:
            for m in messages:
                if m.get("role") == "system" and "Known facts" in (m.get("content") or ""):
                    return m["content"]
        return ""

    def plan_ok(self):
        return _plan([("read_file", os.path.join(self.root, "notes.md"),
                       "contains:%s" % MARK)], "contains:%s" % MARK)

    def test_no_store_means_no_memory_and_no_block(self):
        batches = []
        result, _g = self.run_loop([self.plan_ok(),
                                    _call("read_file", os.path.join(self.root, "notes.md")),
                                    "FINAL: the marker is %s" % MARK],
                                   recorder=batches)
        self.assertTrue(result["ok"], result["failure"])
        self.assertFalse(result["recall"]["enabled"])
        self.assertEqual(self.block_of(batches), "")

    def test_a_confirmed_step_is_written_back_at_its_verified_level(self):
        result, _g = self.run_loop([self.plan_ok(),
                                    _call("read_file", os.path.join(self.root, "notes.md")),
                                    "FINAL: the marker is %s" % MARK],
                                   memory_store=self.store_path)
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["recall"]["written"], result["verified_steps"])
        recs = memory.MemoryStore(self.store_path).records()
        self.assertEqual(len(recs), 1)
        # `read_file` is declared deterministic and the verifier re-ran it, so the
        # record is born invariant rather than merely observed.
        self.assertEqual(recs[0]["level"], "invariant")
        self.assertIn("reproduced", recs[0]["how_verified"])
        self.assertIn(MARK, recs[0]["value"])

    def test_with_the_verifier_off_a_written_record_is_only_volatile(self):
        # The level mapping through the real loop: `declared` evidence (no
        # independent check) must not become a remembered fact.
        t = dict(loop_guard.active_thresholds(), verify_mode="off")
        result, _g = self.run_loop([self.plan_ok(),
                                    _call("read_file", os.path.join(self.root, "notes.md")),
                                    "FINAL: the marker is %s" % MARK],
                                   memory_store=self.store_path, thresholds=t)
        self.assertEqual(result["recall"]["written"], 1)
        recs = memory.MemoryStore(self.store_path).records()
        self.assertEqual(recs[0]["level"], "volatile")
        # And a later run cannot present it as a fact.
        block = memory.render(memory.recall(recs, "find the marker"))
        self.assertNotIn(MARK, block.split(memory.UNVERIFIED_HEADER, 1)[0])

    def test_a_recalled_fact_reaches_the_system_block_with_its_level(self):
        store = memory.MemoryStore(self.store_path)
        store.add(memory.make_record(
            "report the marker", "read_file", os.path.join(self.root, "notes.md"),
            "%s appears here" % MARK, level="invariant",
            how_verified="read_file:expectation_held+reproduced"))
        batches = []
        result, _g = self.run_loop([self.plan_ok(),
                                    _call("read_file", os.path.join(self.root, "notes.md")),
                                    "FINAL: the marker is %s" % MARK],
                                   goal="report the marker", recorder=batches,
                                   memory_store=store)
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["recall"]["facts"], 1)
        block = self.block_of(batches)
        self.assertIn("read_file", block.split(memory.UNVERIFIED_HEADER, 1)[0])
        self.assertEqual(result["recall"]["violations"], [])

    def test_recall_can_be_suppressed_on_the_same_store(self):
        store = memory.MemoryStore(self.store_path)
        store.add(memory.make_record("report the marker", "read_file", "notes.md",
                                     "%s appears here" % MARK, level="invariant",
                                     how_verified="read_file:reproduced"))
        t = dict(loop_guard.active_thresholds(), recall_enabled=0)
        result, _g = self.run_loop([self.plan_ok(),
                                    _call("read_file", os.path.join(self.root, "notes.md")),
                                    "FINAL: the marker is %s" % MARK],
                                   goal="report the marker", thresholds=t,
                                   memory_store=store)
        self.assertTrue(result["recall"]["suppressed"])
        self.assertEqual(result["recall"]["available"], 1)
        self.assertEqual(result["recall"]["recalled"], 0)
        # Writing is not part of the read switch: the run still records its own
        # confirmed step, which is what makes an A/B of recall meaningful.
        self.assertEqual(result["recall"]["written"], 1)

    def test_memory_never_satisfies_the_goal_gate(self):
        # The answer is in the store and this run did not observe it. The gate must
        # refuse, and the refusal must name the gap rather than being silent.
        store = memory.MemoryStore(self.store_path)
        store.add(memory.make_record("report the marker", "read_file", "notes.md",
                                     "%s appears here" % MARK, level="invariant",
                                     how_verified="read_file:reproduced"))
        missing = os.path.join(self.root, "missing.md")
        plan = _plan([("read_file", missing, "contains:%s" % MARK)],
                     "contains:%s" % MARK)
        result, _g = self.run_loop([plan, _call("read_file", missing),
                                    "FINAL: the marker is %s" % MARK],
                                   goal="report the marker", memory_store=store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unverified_completion")
        self.assertTrue(result["diagnosis"]["detail"]["recall_gap"])
        self.assertEqual(result["evidence"], [])

    def test_the_gap_is_not_claimed_when_recall_does_not_hold_the_answer(self):
        store = memory.MemoryStore(self.store_path)
        store.add(memory.make_record("report the marker", "read_file", "notes.md",
                                     "%s appears here" % OTHER, level="invariant",
                                     how_verified="read_file:reproduced"))
        missing = os.path.join(self.root, "missing.md")
        plan = _plan([("read_file", missing, "contains:%s" % MARK)],
                     "contains:%s" % MARK)
        result, _g = self.run_loop([plan, _call("read_file", missing),
                                    "FINAL: the marker is %s" % MARK],
                                   goal="report the marker", memory_store=store)
        self.assertEqual(result["reason"], "unverified_completion")
        self.assertFalse(result["diagnosis"]["detail"]["recall_gap"])

    def test_every_recall_in_the_run_records_what_it_read(self):
        store = memory.MemoryStore(self.store_path)
        store.add(memory.make_record("report the marker", "read_file", "notes.md",
                                     "%s appears here" % MARK, level="invariant",
                                     how_verified="read_file:reproduced"))
        result, _g = self.run_loop([self.plan_ok(),
                                    _call("read_file", os.path.join(self.root, "notes.md")),
                                    "FINAL: the marker is %s" % MARK],
                                   goal="report the marker", memory_store=store)
        item = result["recall"]["items"][0]
        self.assertEqual(item["level"], "invariant")
        self.assertEqual(item["how_verified"], "read_file:reproduced")
        self.assertIsInstance(item["score"], float)
        self.assertGreaterEqual(result["recall"]["chars"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
