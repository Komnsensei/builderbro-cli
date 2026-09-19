#!/usr/bin/env python3
"""rag_test.py — tests for the RAG core, its grounding auditor, the
self-diagnosis layer, and the `builderbro-rag` skill interface.

No network, no model, no credentials. The grounding tests use injected
generators (`ExtractiveGenerator` / `HallucinatingGenerator`) and hand-built
hit lists, so every result is deterministic.

The most important tests here are the **paired controls**. An auditor that
passes everything and an auditor that fails everything are equally useless, so
the suite asserts both directions at once: the honest reference generator must
come out clean, and the adversarial generator must be caught with every
fabricated citation enumerated.

Run: python3 rag_test.py
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# The skill is a self-contained package under .agents/skills/, so its directory
# has to be importable too — that is the point of shipping it as a package.
SKILL_DIR = os.path.join(HERE, ".agents", "skills", "builderbro-rag")
sys.path.insert(0, SKILL_DIR)

import rag_core
import rag_diagnose
import rag_eval
import rag_skill


def make_hits(*texts):
    """A synthetic retrieval result with the same shape search() returns."""
    out = []
    for i, text in enumerate(texts, 1):
        out.append({
            "rank": i,
            "score": 1.0 / i,
            "bm25": 1.0,
            "dense": 0.0,
            "bm25_rank": i,
            "dense_rank": i,
            "lexical_only": False,
            "dense_only": False,
            "both": True,
            "chunk": {
                "id": "chunk%d" % i,
                "source": "doc.md",
                "doc_id": "doc.md",
                "ordinal": i - 1,
                "heading": "H",
                "char_start": 0,
                "char_end": len(text),
                "n_words": len(text.split()),
                "text": text,
            },
        })
    return out


class TokenizerTest(unittest.TestCase):
    def test_stem_is_consistent_across_word_forms(self):
        # The bug this guards against: 'continuous' -> 'continuou' while
        # 'continuously' stayed unchanged, so a question could not match the
        # sentence that answered it.
        for a, b in (("continuous", "continuously"), ("process", "processes"),
                     ("ground", "grounding"), ("cycle", "cycles")):
            self.assertEqual(rag_core.stem(a), rag_core.stem(b),
                             "%s and %s must share a stem" % (a, b))

    def test_compound_identifiers_expand_to_their_parts(self):
        # 'dispatch-graph' must be findable by a query saying 'dispatch graph'.
        toks = rag_core.tokenize("dispatch-graph hashing")
        self.assertIn("dispatch-graph", toks)
        self.assertIn("dispatch", toks)
        self.assertIn("graph", toks)

    def test_underscored_identifiers_expand(self):
        toks = rag_core.tokenize("drive_sync")
        self.assertIn("drive_sync", toks)
        self.assertIn("drive", toks)
        self.assertIn("sync", toks)

    def test_stopwords_and_short_tokens_dropped(self):
        self.assertEqual(rag_core.tokenize("the a of to"), [])


class ChunkingTest(unittest.TestCase):
    DOC = (
        "# Alpha\n"
        "alpha beta gamma delta\n"
        "\n"
        "## Beta\n"
        "epsilon zeta eta theta\n"
        "\n"
        "### Gamma\n"
        "iota kappa lambda mu\n"
    )

    def test_spans_point_at_real_source_text(self):
        chunks = rag_core.chunk_document("doc.md", self.DOC)
        self.assertTrue(chunks)
        for c in chunks:
            self.assertGreaterEqual(c["char_start"], 0)
            self.assertLessEqual(c["char_end"], len(self.DOC))
            self.assertLess(c["char_start"], c["char_end"])
            # The chunk's own opening text must exist at the claimed offset.
            head = c["text"].split("\n")[0][:20]
            self.assertEqual(self.DOC[c["char_start"]:c["char_start"] + len(head)], head,
                             "char span does not match the source text")

    def test_headings_are_captured_on_the_chunk(self):
        chunks = rag_core.chunk_document("doc.md", self.DOC)
        headings = " | ".join(c["heading"] for c in chunks)
        self.assertIn("Alpha", headings)

    def test_identifiers_are_stable_across_rebuilds(self):
        a = rag_core.chunk_document("doc.md", self.DOC)
        b = rag_core.chunk_document("doc.md", self.DOC)
        self.assertEqual([c["id"] for c in a], [c["id"] for c in b])

    def test_long_document_splits_and_covers_all_text(self):
        doc = "\n\n".join("word%d filler text here" % i for i in range(400))
        chunks = rag_core.chunk_document("big.md", doc, {"chunk_budget_words": 50,
                                                         "chunk_overlap_words": 10,
                                                         "min_chunk_words": 5})
        self.assertGreater(len(chunks), 5)
        # No gold text may be lost: every distinctive token must survive into
        # some chunk, otherwise no retriever can ever find it.
        ends = [len(chunks)]
        _ = ends
        for i in (0, 100, 250, 399):
            self.assertTrue(any("word%d" % i in c["text"] for c in chunks),
                            "word%d was dropped by chunking" % i)


class SupportGateTest(unittest.TestCase):
    """The retrieval-confidence gate is what prevents confident fabrication on
    questions the corpus cannot answer."""

    def test_support_high_when_context_contains_the_question(self):
        # Not 1.0: the interrogative 'what' is a query term and is not in the
        # context. What matters operationally is clearing the gate threshold.
        hits = make_hits("the phase clock law scales subjective time by frequency")
        s = rag_core.query_support("what is the phase clock law", hits)
        self.assertGreater(s, 0.7)
        self.assertGreater(s, rag_core.DEFAULT_CONFIG["min_query_support"])

    def test_support_low_when_context_is_unrelated(self):
        hits = make_hits("the drift loop writes a ledger record every cycle")
        s = rag_core.query_support("what was the weather in Lisbon", hits)
        self.assertLess(s, 0.5)

    def test_below_threshold_returns_a_refusal_without_calling_the_model(self):
        calls = []

        def generator(messages):
            calls.append(messages)
            return "The weather in Lisbon was sunny [1]."

        hits = make_hits("the drift loop writes a ledger record every cycle")
        audit = rag_core.answer("what was the weather in Lisbon", hits, generator,
                                {"min_query_support": 0.9})
        self.assertTrue(audit["refused"])
        self.assertEqual(audit["answer"], rag_core.REFUSAL_TOKEN)
        self.assertEqual(audit["verdict"], "grounding:ok")
        self.assertEqual(calls, [], "the model must not be called when the gate fires")

    def test_above_threshold_calls_the_model(self):
        calls = []

        def generator(messages):
            calls.append(messages)
            return "Subjective time scales by the frequency ratio [1]."

        hits = make_hits("the phase clock law scales subjective time by the frequency ratio")
        audit = rag_core.answer("how does subjective time scale", hits, generator,
                                {"min_query_support": 0.3})
        self.assertFalse(audit["refused"])
        self.assertEqual(len(calls), 1)


class AuditTest(unittest.TestCase):
    """The auditor must catch fabrication without slandering honest output."""

    def test_fabricated_citation_is_detected(self):
        hits = make_hits("the ledger is append only")
        audit = rag_core.audit_answer("The ledger is append only [4].", hits)
        self.assertEqual(audit["invalid_citations"], [4])
        self.assertEqual(audit["verdict"], "grounding:fail")
        self.assertTrue(any(r.startswith("fabricated_citation") for r in audit["hard_reasons"]))

    def test_uncited_claim_fails_the_hard_gate(self):
        hits = make_hits("the ledger is append only and written by the machine")
        audit = rag_core.audit_answer(
            "The ledger is append only and written by the machine.", hits)
        self.assertTrue(audit["uncited_claims"])
        self.assertIn("no_citations", audit["hard_reasons"])

    def test_quoted_marker_is_not_a_fabricated_citation(self):
        # The corpus itself contains '[3]'-style references; quoting one is not
        # inventing a citation.
        hits = make_hits("see the brief [3] for the target figure")
        audit = rag_core.audit_answer(
            "The brief's target appears in the source [3].", hits)
        self.assertEqual(audit["invalid_citations"], [])

    def test_citation_after_a_display_equation_attaches_to_the_claim(self):
        # Real model output style: prose, then an equation, then the marker on
        # its own line. The marker belongs to the prose sentence above it.
        hits = make_hits("The Phase-Clock Law scales subjective time by the frequency ratio.")
        answer = ("The Phase-Clock Law scales subjective time by the frequency ratio:\n"
                  "\\[ dtau = (O0/O(x)) dt \\]\n"
                  "[1]")
        audit = rag_core.audit_answer(answer, hits)
        self.assertEqual(audit["invalid_citations"], [])
        self.assertNotIn("no_citations", audit["hard_reasons"])
        self.assertFalse(audit["uncited_claims"],
                         "the marker after the equation must attach to the claim above it")

    def test_refusal_is_recognised_and_passes(self):
        hits = make_hits("unrelated text")
        audit = rag_core.audit_answer(rag_core.REFUSAL_TOKEN, hits)
        self.assertTrue(audit["refused"])
        self.assertEqual(audit["verdict"], "grounding:ok")
        self.assertEqual(audit["reasons"], [])

    def test_json_blob_is_not_counted_as_a_claim(self):
        # Structured data in the corpus must not be graded as a prose claim.
        hits = make_hits('schema: {"step_id": 2, "action": "build", "status": "pending"}')
        audit = rag_core.audit_answer(
            '{"step_id": 2, "action": "build", "status": "pending"}', hits)
        self.assertEqual(audit["claims"], 0)

    def test_numbers_must_agree_with_the_cited_source(self):
        # A hallucinated throughput figure with correct surrounding vocabulary
        # must not pass on vocabulary alone.
        hits = make_hits("measured throughput was 86 tokens per second on the phone")
        good = rag_core.audit_answer(
            "Measured throughput was 86 tokens per second on the phone [1].", hits)
        bad = rag_core.audit_answer(
            "Measured throughput was 412 tokens per second on the phone [1].", hits)
        self.assertEqual(good["unsupported_rate"], 0.0)
        self.assertGreater(bad["unsupported_rate"], 0.0)

    def test_two_layers_are_separate(self):
        hits = make_hits("the ledger records each write")
        # Well cited but a paraphrase that shares little vocabulary: the hard
        # gate passes, the entailment proxy complains. Both must be visible.
        audit = rag_core.audit_answer(
            "An append-only journaling mechanism persists each mutation durably [1].", hits)
        self.assertEqual(audit["verdict"], "grounding:ok")
        self.assertTrue(audit["soft_reasons"])
        self.assertEqual(audit["strict_verdict"], "grounding:fail")


class CorpusTest(unittest.TestCase):
    """Corpus-dependent behaviour. The index is built once for the class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rag-test-")
        cls.config = rag_core.active_config()
        cls.index = rag_core.build_index(config=cls.config)
        cls.extractive = rag_eval.ExtractiveGenerator()
        cls.hallucinating = rag_eval.HallucinatingGenerator()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_corpus_is_ingested(self):
        self.assertGreater(len(self.index.chunks), 20)
        self.assertGreater(len(self.index.corpus_meta["files"]), 2)

    def test_span_coverage_is_complete(self):
        # If gold text is missing from every chunk, the fault is chunking and no
        # ranker change can fix it.
        coverage = rag_eval.span_coverage(self.index.chunks)
        self.assertEqual(coverage["coverage"], 1.0,
                         "chunking lost gold text: %s" % coverage["missing_phrases"])

    def test_retrieval_meets_its_pre_registered_floors(self):
        r = rag_eval.run_retrieval_eval(self.index, config=self.config)
        self.assertGreaterEqual(r["recall_at_k"], rag_eval.FLOORS["recall_at_k"])
        self.assertGreaterEqual(r["mrr"], rag_eval.FLOORS["mrr"])

    def test_the_gate_separates_answerable_from_unanswerable(self):
        support_pos, support_neg = [], []
        for item in rag_eval.GOLDEN_ITEMS:
            hits = self.index.search(item["question"], config=self.config)
            s = rag_core.query_support(item["question"], hits, index=self.index,
                                       config=self.config)
            (support_neg if item.get("expect_no_answer") else support_pos).append(s)
        self.assertGreater(min(support_pos), max(support_neg),
                           "the confidence gate cannot separate the two sets")

    def test_honest_generator_produces_grounded_output(self):
        g = rag_eval.run_grounding_eval(self.index, self.extractive, config=self.config)
        self.assertEqual(g["citation_fidelity"], 1.0)
        self.assertEqual(g["fabricated_citation_markers"], 0)
        self.assertEqual(g["groundedness"], 1.0)

    def test_adversarial_generator_is_caught(self):
        g = rag_eval.run_grounding_eval(self.index, self.hallucinating, config=self.config)
        self.assertEqual(g["citation_fidelity"], 0.0)
        self.assertGreater(g["fabricated_citation_markers"], 0)
        self.assertEqual(g["verdict_pass_rate"], 0.0)

    def test_no_false_answers_and_no_false_refusals(self):
        g = rag_eval.run_grounding_eval(self.index, self.extractive, config=self.config)
        self.assertEqual(g["false_answer_rate"], 0.0)
        self.assertEqual(g["false_refusal_rate"], 0.0)

    def test_full_eval_passes_every_floor(self):
        report = rag_eval.evaluate(self.index, self.extractive, config=self.config)
        failed = [k for k, v in report["checks"].items() if not v["pass"]]
        self.assertEqual(failed, [], "floors missed: %s" % failed)


class AttributionTest(unittest.TestCase):
    def test_chunking_fault_is_attributed_to_chunking(self):
        # Gold text absent from every chunk must be reported as a chunking
        # failure, not as a retrieval failure.
        chunks = rag_core.chunk_document("doc.md", "only this text exists")
        index = rag_core.RagIndex(chunks, dict(rag_core.DEFAULT_CONFIG),
                                  {"files": [{"path": "doc.md", "bytes": 21}], "skipped": []})
        retrieval = rag_eval.run_retrieval_eval(index)
        coverage = {"coverage": 0.5, "items": [], "missing_phrases": ["never present"]}
        findings = rag_diagnose.attribute(index, retrieval, coverage)
        stages = [f["stage"] for f in findings]
        self.assertIn("chunking", stages)
        chunking = [f for f in findings if f["stage"] == "chunking"][0]
        self.assertEqual(chunking["severity"], "critical")
        self.assertIn("chunk_overlap_words", chunking["lever"])

    def test_empty_corpus_is_attributed_to_ingestion(self):
        index = rag_core.RagIndex([], dict(rag_core.DEFAULT_CONFIG),
                                  {"files": [], "skipped": []})
        retrieval = rag_eval.run_retrieval_eval(index)
        findings = rag_diagnose.attribute(index, retrieval, {"coverage": 1.0,
                                                            "items": [],
                                                            "missing_phrases": []})
        self.assertIn("ingestion", [f["stage"] for f in findings])

    def test_fabrication_is_attributed_to_generation(self):
        chunks = rag_core.chunk_document("doc.md", "the ledger is append only")
        index = rag_core.RagIndex(chunks, dict(rag_core.DEFAULT_CONFIG),
                                  {"files": [{"path": "doc.md", "bytes": 25}], "skipped": []})
        retrieval = rag_eval.run_retrieval_eval(index)
        grounding = {"fabricated_citation_markers": 3, "fabricated_citation_items": ["x"],
                     "false_answer_rate": 0.5, "refusal_accuracy": 0.5,
                     "citation_fidelity": 0.1}
        findings = rag_diagnose.attribute(index, retrieval, {"coverage": 1.0, "items": [],
                                                            "missing_phrases": []}, grounding)
        stages = [f["stage"] for f in findings]
        self.assertIn("generation", stages)

    def test_gate_excludes_a_high_objective_config_that_misses_a_floor(self):
        # Guards the pre-registered rule: a config cannot win by trading away a
        # floored metric.
        good = {"recall_at_k": 0.9, "mrr": 0.8}
        self.assertTrue(rag_diagnose._gates_passed(good, {"coverage": 1.0}))
        # Higher objective than the config it is compared against, but MRR misses
        # the floor — this is exactly the bm25@120 case from the first sweep.
        high_sum_but_floor_missed = {"recall_at_k": 0.99, "mrr": 0.69}
        borderline = {"recall_at_k": 0.86, "mrr": 0.72}
        self.assertGreater(rag_diagnose._score(high_sum_but_floor_missed),
                           rag_diagnose._score(borderline))
        self.assertFalse(rag_diagnose._gates_passed(high_sum_but_floor_missed,
                                                    {"coverage": 1.0}))
        self.assertTrue(rag_diagnose._gates_passed(borderline, {"coverage": 1.0}))

    def test_cycle_reports_truthfully_when_nothing_improves(self):
        incumbent = dict(rag_core.DEFAULT_CONFIG)
        result = rag_diagnose.run_cycle(incumbent=incumbent,
                                       generate=rag_eval.ExtractiveGenerator(),
                                       log_path=None, register=False)
        self.assertIn("incumbent", result)
        self.assertIsInstance(result["n_candidates"], int)
        # A dry run must never register, and "nothing improved" must be reported
        # as such rather than dressed up as a win.
        self.assertFalse(result["changed"])
        self.assertTrue(result["action"])
        self.assertNotIn("registered to", result["action"])


class SkillInterfaceTest(unittest.TestCase):
    """The skill contract: branch on `ok`, treat refusal as success."""

    def test_contract_lists_every_command(self):
        out = rag_skill.run(["contract"])
        self.assertTrue(out["ok"])
        for cmd in ("build", "search", "ask", "eval", "diagnose", "contract"):
            self.assertIn(cmd, out["commands"])

    def test_unknown_command_is_a_structured_failure(self):
        out = rag_skill.run(["frobnicate"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unknown_command")
        self.assertIn("reason", out)

    def test_missing_argument_is_a_structured_failure(self):
        out = rag_skill.run(["ask"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "missing_argument")

    def test_bad_generator_is_a_structured_failure(self):
        out = rag_skill.run(["ask", "what is the ledger", "--generator", "nope"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "bad_generator")

    def test_dead_brain_is_a_structured_failure_not_a_crash(self):
        def broken(messages):
            raise RuntimeError("cascade exhausted")

        original = rag_eval.LiveGenerator
        rag_eval.LiveGenerator = lambda: broken
        try:
            out = rag_skill.cmd_ask(_Args("what is the ledger"), os.environ)
        finally:
            rag_eval.LiveGenerator = original
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "generation_failed")
        self.assertIn("cascade exhausted", out["reason"])

    def test_refusal_is_ok_true(self):
        out = rag_skill.run(["ask", "how much does a kubernetes cluster cost per month"])
        self.assertTrue(out["ok"])
        self.assertTrue(out["refused"])

    def test_adversarial_generator_fails_through_the_skill(self):
        out = rag_skill.run(["ask", "what is the ledger",
                             "--generator", "hallucinate"])
        self.assertFalse(out["ok"])
        self.assertTrue(out["invalid_citations"])

    def test_search_hits_carry_a_usable_citation_and_span(self):
        out = rag_skill.run(["search", "phase clock law", "-k", "3"])
        self.assertTrue(out["ok"])
        self.assertTrue(out["hits"])
        for h in out["hits"]:
            self.assertEqual(h["citation"], "[%d]" % h["rank"])
            self.assertEqual(len(h["span"]), 2)
            self.assertLess(h["span"][0], h["span"][1])

    def test_skill_is_importable_from_the_skill_directory(self):
        path = os.path.join(".agents", "skills", "builderbro-rag", "rag_skill.py")
        self.assertTrue(os.path.isfile(path), "skill entry point missing")


class ScoreMemoTest(unittest.TestCase):
    """`RagIndex` memoises each scoring channel per (query, channel-relevant
    config). That is an *optimisation*, so the burden is on it to prove it changes
    nothing. Every test below pairs the memoised index against a fresh index with
    an empty cache computing the same thing from scratch, and one test forces
    eviction to show the memory bound cannot alter a result either.

    The pair of granularity tests is the important one: an over-broad key is
    correct here and wrong the moment a channel-relevant knob moves, so one test
    asserts a moved knob *misses* and another asserts an unrelated knob *hits*.
    """

    DOCS = {
        "alpha.md": (
            "# Alpha\n\nThe ledger is an append-only record of every cycle the "
            "residence completes. Each line carries a phase, an outcome and the "
            "evidence path it was derived from, so a reader can trace a claim "
            "back to the command that produced it.\n\n"
            "## Phase clock\n\n"
            "Subjective time is set by the phase clock law, which scales elapsed "
            "time by the ratio of the reference frequency to the local one. A "
            "slower local oscillator therefore advances subjective time faster "
            "than the wall clock does."
        ),
        "beta.md": (
            "# Beta\n\n"
            "Retrieval fuses a lexical channel with a dense channel. The lexical "
            "channel scores term overlap with a saturation term, and the dense "
            "channel scores hashed feature vectors by cosine similarity.\n\n"
            "## Grounding\n\n"
            "Every sentence in an answer must cite a retrieved chunk, and a "
            "citation that does not resolve to a chunk is treated as fabricated "
            "rather than as a formatting slip."
        ),
    }

    QUERIES = (
        "how does the phase clock law set subjective time",
        "what does the ledger record",
    )

    @classmethod
    def setUpClass(cls):
        cls.base = dict(rag_core.DEFAULT_CONFIG)

    def _index(self, config=None):
        chunks = []
        for name, text in sorted(self.DOCS.items()):
            chunks.extend(rag_core.chunk_document(name, text, config=config or self.base))
        return rag_core.RagIndex(chunks, dict(config or self.base),
                                 {"files": sorted(self.DOCS)})

    @staticmethod
    def _fingerprint(index, query, config):
        """Everything a caller can observe about one search: order, score and
        both channels' raw scores. Comparing only the ids would hide a channel
        returning different numbers behind the same ranking."""
        return [(h["chunk"]["id"], round(h["score"], 12), round(h["bm25"], 12),
                 round(h["dense"], 12), h["bm25_rank"], h["dense_rank"])
                for h in index.search(query, config=config)]

    def _sweep_configs(self):
        """The shape of `rag_diagnose.candidate_configs`, shrunk to two doc
        sizes: chunk geometry plus every fusion mode and dense weight."""
        configs = []
        for budget in (60, 100):
            for fusion in ("linear", "rrf", "bm25"):
                weights = (0.0,) if fusion == "bm25" else (0.0, 0.5, 1.0)
                for dw in weights:
                    cfg = dict(self.base)
                    cfg["chunk_budget_words"] = budget
                    cfg["chunk_overlap_words"] = max(8, budget // 5)
                    cfg["fusion"] = fusion
                    cfg["dense_weight"] = dw
                    cfg["top_k"] = 4
                    configs.append(cfg)
        return configs

    def test_sweep_over_one_index_matches_a_fresh_index_each_time(self):
        # The index is built once and searched with every candidate config, which
        # is exactly what the sweep does — and the case where a stale hit would
        # show up as a silently wrong score.
        memoised = self._index()
        for cfg in self._sweep_configs():
            fresh = self._index()
            for q in self.QUERIES:
                self.assertEqual(
                    self._fingerprint(memoised, q, cfg),
                    self._fingerprint(fresh, q, cfg),
                    "memoised search diverged for fusion=%s dense_weight=%s "
                    "budget=%s" % (cfg["fusion"], cfg["dense_weight"],
                                   cfg["chunk_budget_words"]))

    def test_a_repeated_search_reuses_the_channel_instead_of_recomputing(self):
        index = self._index()
        cfg = dict(self.base)
        calls = []
        original = rag_core.embed
        rag_core.embed = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        try:
            first = self._fingerprint(index, self.QUERIES[0], cfg)
            after_first = len(calls)
            second = self._fingerprint(index, self.QUERIES[0], cfg)
            after_second = len(calls)
        finally:
            rag_core.embed = original
        self.assertEqual(first, second)
        self.assertEqual(after_first, 1,
                         "the dense channel should embed the query exactly once")
        self.assertEqual(after_second, after_first,
                         "an identical search re-embedded the query")

    def test_a_change_to_one_channel_does_not_evict_the_other(self):
        # The key is per channel, not per config. If it were keyed on the whole
        # config, moving anything would throw away both channels' work — correct
        # but pointless; the other direction (a key that ignores these) is wrong.
        index = self._index()
        cfg_a = dict(self.base)
        index.search(self.QUERIES[0], config=cfg_a)
        keys_a = set(index._scores)
        cfg_b = dict(cfg_a)
        cfg_b["bm25_k1"] = cfg_a["bm25_k1"] + 0.4
        index.search(self.QUERIES[0], config=cfg_b)
        added = set(index._scores) - keys_a
        self.assertEqual(len(added), 1, "expected exactly one new key: %s" % (added,))
        self.assertTrue(all(k[0] == "bm25" for k in added),
                        "a bm25_k1 change should only invalidate bm25: %s" % (added,))

    def test_the_key_covers_exactly_the_knobs_each_channel_reads(self):
        # `bm25_*` is read by the lexical channel, `dim` / `char_ngram` /
        # `char_weight` by the dense one, and `top_k` / `fusion` / `dense_weight`
        # by neither — they shape the fusion of the two score lists. Reusing
        # across those is the whole point of the memo; reusing across the others
        # would be a wrong answer. Asserting the exact channel a new key belongs
        # to is what pins the boundary down.
        changes = {
            "bm25_k1": ("bm25", 2.0),
            "bm25_b": ("bm25", 0.2),
            "top_k": (None, 2),
            "fusion": (None, "rrf"),
            "dense_weight": (None, 0.25),
            "chunk_budget_words": (None, 60),
            "chunk_overlap_words": (None, 12),
        }
        for knob, (channel, new_value) in changes.items():
            index = self._index()
            cfg_a = dict(self.base)
            index.search(self.QUERIES[0], config=cfg_a)
            keys_a = set(index._scores)
            cfg_b = dict(cfg_a)
            cfg_b[knob] = new_value
            index.search(self.QUERIES[0], config=cfg_b)
            added = set(index._scores) - keys_a
            if channel is None:
                self.assertEqual(added, set(),
                                 "moving %s needlessly invalidated %s" % (knob, added))
            else:
                self.assertEqual(len(added), 1,
                                 "moving %s expected one new key, got %s" % (knob, added))
                self.assertEqual(next(iter(added))[0], channel,
                                 "moving %s invalidated the wrong channel" % knob)

    def test_a_foreign_vector_geometry_is_refused_not_truncated(self):
        # A smaller `dim` used to be the dangerous case: the dot product is a
        # `zip`, so it would have silently compared 256 components of vectors
        # built with 512 and returned a confident wrong ranking. All three
        # geometry knobs must fail loudly instead.
        index = self._index()
        for knob, value in (("dim", 128), ("char_ngram", 3), ("char_weight", 2.0)):
            cfg = dict(self.base)
            cfg[knob] = value
            with self.assertRaises(ValueError) as caught:
                index.search(self.QUERIES[0], config=cfg)
            message = str(caught.exception)
            self.assertIn(knob, message)
            self.assertIn("rebuild the index", message)
            self.assertEqual(index._scores, {}, "a refused search must cache nothing")

    def test_a_moved_search_time_knob_still_agrees_with_a_fresh_index(self):
        # Search-time knobs are read at search time and are consistent with any
        # built index, so a memoised result must equal a from-scratch one.
        memoised = self._index()
        for knob, value in (("bm25_k1", 2.0), ("bm25_b", 0.2), ("top_k", 2),
                            ("rrf_k", 10), ("bm25_weight", 0.3)):
            cfg = dict(self.base)
            cfg[knob] = value
            fresh = self._index()
            for q in self.QUERIES:
                self.assertEqual(
                    self._fingerprint(memoised, q, cfg),
                    self._fingerprint(fresh, q, cfg),
                    "a moved %s was served from a stale cache entry" % knob)

    def test_eviction_cannot_change_a_result(self):
        original = rag_core.SCORE_MEMO_MAX
        rag_core.SCORE_MEMO_MAX = 1  # force a clear on nearly every remember()
        self.addCleanup(setattr, rag_core, "SCORE_MEMO_MAX", original)
        memoised = self._index()
        for cfg in self._sweep_configs():
            fresh = self._index()
            for q in self.QUERIES:
                self.assertEqual(self._fingerprint(memoised, q, cfg),
                                 self._fingerprint(fresh, q, cfg))


class GatePlacementTest(unittest.TestCase):
    """Where the confidence gate sits, and why the tuning grid alone cannot place
    it.

    The failure this guards against is silent: a gate *below* the highest
    unanswerable support answers an unanswerable question, which is the one
    outcome `false_answer_rate` exists to catch. It happened — editing this
    repository's own documentation moved the measured supports, the registered
    0.55 ended up under a max-negative of 0.5504, and `rag eval` reported a false
    answer that no grid point could fix, because the gap (0.0053) was narrower
    than the grid's step (0.05).
    """

    @classmethod
    def setUpClass(cls):
        cls.config = rag_core.active_config()
        cls.index = rag_core.build_index(config=cls.config)

    def _supports(self):
        pos, neg = [], []
        for item in rag_eval.GOLDEN_ITEMS:
            hits = self.index.search(item["question"], config=self.config)
            s = rag_core.query_support(item["question"], hits, index=self.index,
                                       config=self.config)
            (neg if item.get("expect_no_answer") else pos).append(s)
        return min(pos), max(neg)

    def test_the_gate_can_work_at_the_registered_context_size(self):
        """The context stage's criterion, and its regression guard.

        With `top_k=6` and the corpus as it stands, the lowest answerable support
        sat *below* the highest unanswerable one, so no threshold could separate
        them and the gate could not be placed at all — a question about resuming
        after a crash was one chunk short of its answer in the retrieved context.
        The fix is the smallest context size that separates the sets, which is
        also the smallest cost; this asserts the registered one is that size, and
        that the size it replaced really does not separate.
        """
        rows, chosen = rag_diagnose.context_boundary(self.index, self.config)
        self.assertIsNotNone(chosen, "no context size separates the two sets — "
                                      "the fault is in retrieval or the metric")
        self.assertEqual(chosen["top_k"], self.config["top_k"],
                         "the registered context size (%s) is not the smallest that "
                         "separates the sets (%s)"
                         % (self.config["top_k"], chosen["top_k"]))
        self.assertGreater(chosen["gap"], 0)
        smaller = [r for r in rows if r["top_k"] < chosen["top_k"]]
        self.assertTrue(smaller)
        self.assertFalse(any(r["separated"] for r in smaller),
                         "%s would have been enough — the registered context is "
                         "larger than the measurement requires" % smaller)

    def test_the_registered_gate_separates_the_two_measured_sets(self):
        pos_min, neg_max = self._supports()
        gate = self.config["min_query_support"]
        self.assertGreater(gate, neg_max,
                           "the gate (%.4f) sits at or below the highest "
                           "unanswerable support (%.4f) — it will answer a question "
                           "the corpus cannot answer" % (gate, neg_max))
        self.assertLessEqual(gate, pos_min,
                             "the gate (%.4f) is above the lowest answerable "
                             "support (%.4f) — it will refuse a question the "
                             "corpus can answer" % (gate, pos_min))

    def test_the_derived_boundary_is_the_max_margin_midpoint(self):
        pos_min, neg_max = self._supports()
        self.assertAlmostEqual(rag_diagnose.support_boundary(self.index, self.config),
                               round((pos_min + neg_max) / 2.0, 4), places=6)

    def _boundary_from_supports(self, supports, expect_no_answer):
        """Run the rule against injected supports, so the *rule* is tested rather
        than the corpus. `search` returns nothing: the support is injected, which
        is the only way to test the placement in isolation from retrieval."""
        items = [{"id": "i%d" % n, "question": "q%d" % n,
                  "expect_no_answer": flag, "gold_phrases": []}
                 for n, flag in enumerate(expect_no_answer)]

        class _FakeIndex(object):
            def search(self, query, config=None):
                return []

        saved_items = rag_eval.GOLDEN_ITEMS
        saved_support = rag_core.query_support
        rag_eval.GOLDEN_ITEMS = items
        rag_core.query_support = lambda q, hits, **kw: supports[int(q[1:])]
        try:
            return rag_diagnose.support_boundary(_FakeIndex(), {})
        finally:
            rag_eval.GOLDEN_ITEMS = saved_items
            rag_core.query_support = saved_support

    def test_the_rule_places_the_boundary_between_the_sets(self):
        # answerable 0.60 / unanswerable 0.50 -> the midpoint, not either set.
        self.assertEqual(self._boundary_from_supports([0.60, 0.50], [False, True]),
                         0.55)

    def test_the_rule_declines_when_the_sets_overlap(self):
        # An answerable question scoring *below* an unanswerable one is not a
        # threshold problem — no placement fixes it, and the rule must say so
        # rather than return a number that looks like a decision.
        self.assertIsNone(self._boundary_from_supports([0.50, 0.60], [False, True]))

    def test_the_rule_declines_when_a_side_is_empty(self):
        self.assertIsNone(self._boundary_from_supports([0.50, 0.52], [True, True]))
        self.assertIsNone(self._boundary_from_supports([0.50, 0.52], [False, False]))


class _Args(object):
    """Minimal argparse-shaped stand-in for direct command function calls."""

    def __init__(self, query, generator="live", k=None):
        self.query = query
        self.generator = generator
        self.k = k


if __name__ == "__main__":
    unittest.main(verbosity=2)
