#!/usr/bin/env python3
"""builderbro_mcp_test.py — tests for the MCP surface over BuilderBro's checks.

Hermetic: no network, no model, temp dirs for every store, and the rag delegation
is tested against a stub script rather than the corpus (the rag skill has its own
55 tests; re-running the corpus here would only test it twice and slowly).

What these tests are actually for — the three ways an MCP wrapper like this one
goes wrong, none of which is "the JSON is malformed":

1. **It re-implements a rule and the copy drifts.** Every check must come from the
   module that owns it, so the tests assert *behaviour that only the real module
   produces*: `nonempty` is refused as vacuous (a hand-written wrapper would
   happily accept it), `not_failure` fires on `loop_guard.fail`'s own line, and an
   `invariant` write with no named check is refused by `memory`'s rule rather than
   this server's.

2. **It launders provenance.** The dangerous direction is the *upward* one: a
   caller trying to record a claim as a fact, or to widen the deterministic-tool
   set so its own tool can reach `invariant`. Both are asserted to fail.

3. **It reports a decision as a malfunction.** The verifier's `ok` is its own
   vocabulary (was the claim confirmed?), which is the opposite polarity from the
   transport's `isError`. A refusal that arrived as an error would invite a caller
   to retry a check that had already ruled. Asserted in both directions.

Run: python3 builderbro_mcp_test.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import builderbro_mcp as mcp
import loop_guard
import memory
import test_support

test_support.isolate_residence()


class _Temp(unittest.TestCase):
    """A throwaway directory, so no test can write into the shipped residence."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="builderbro-mcp-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def call(self, name, **args):
        payload, is_error = mcp.call_tool(name, args)
        self.assertIsInstance(payload, dict)
        return payload, is_error

    def ok(self, name, **args):
        payload, is_error = self.call(name, **args)
        self.assertFalse(is_error, "%s returned an error: %r" % (name, payload))
        self.assertTrue(payload.get("ok"), "%s was not ok: %r" % (name, payload))
        return payload


# ── 1. Protocol ───────────────────────────────────────────────────────────────

class ProtocolTest(_Temp):

    def test_initialize_echoes_the_clients_protocol_version(self):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(reply["result"]["serverInfo"]["name"], "builderbro")
        self.assertIn("tools", reply["result"]["capabilities"])

    def test_initialize_without_a_version_still_answers(self):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(reply["result"]["protocolVersion"], mcp.DEFAULT_PROTOCOL_VERSION)

    def test_notifications_get_no_reply(self):
        for method in ("notifications/initialized", "notifications/cancelled"):
            self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": method}))

    def test_ping_is_answered(self):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(reply["id"], 7)
        self.assertEqual(reply["result"], {})

    def test_unknown_method_is_method_not_found(self):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
        self.assertEqual(reply["error"]["code"], -32601)
        self.assertEqual(reply["id"], 2)

    def test_every_tool_advertises_a_usable_schema(self):
        tools = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(sorted(names), sorted(mcp.TOOLS))
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["description"].strip())
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertIsInstance(schema["properties"], dict)
                for key in schema.get("required", []):
                    self.assertIn(key, schema["properties"],
                                  "%s requires %r but does not declare it" % (tool["name"], key))

    def test_a_malformed_line_does_not_end_the_session(self):
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"pi\n'
                            '{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
        stdout = io.StringIO()
        self.assertEqual(mcp.serve(stdin=stdin, stdout=stdout), 0)
        lines = [json.loads(l) for l in stdout.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1]["id"], 2, "the session must survive a parse error")

    def test_a_broken_tool_call_does_not_end_the_session(self):
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"tools/call",'
                            '"params":{"name":"verify_claim","arguments":[]}}\n'
                            '{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
        stdout = io.StringIO()
        mcp.serve(stdin=stdin, stdout=stdout)
        lines = [json.loads(l) for l in stdout.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0]["result"]["isError"])
        self.assertEqual(lines[1]["result"], {})

    def test_nothing_but_json_rpc_reaches_stdout(self):
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
        stdout = io.StringIO()
        mcp.serve(stdin=stdin, stdout=stdout)
        for line in stdout.getvalue().splitlines():
            json.loads(line)  # raises if a stray print corrupted the stream


# ── 2. verify_claim ───────────────────────────────────────────────────────────

class VerifyClaimTest(_Temp):

    def test_a_claim_with_no_expectation_is_refused(self):
        payload, is_error = self.call("verify_claim", tool="read_file", output="foo")
        self.assertEqual(payload["level"], "refused")
        self.assertEqual(payload["refused_by"], "expectation_held")
        self.assertTrue(payload["refused"])
        self.assertFalse(is_error, "a refusal is a decision, not a malfunction")

    def test_a_vacuous_expectation_is_refused(self):
        # `nonempty` and `ok` accept an empty output or the failure line, so the
        # verifier refuses them. A wrapper that only matched needles would pass.
        for expect in ("ok", "nonempty"):
            with self.subTest(expect=expect):
                payload, _ = self.call("verify_claim", tool="read_file",
                                       output="foo bar", expect=expect)
                self.assertEqual(payload["refused_by"], "not_vacuous")

    def test_a_failed_call_is_never_verified(self):
        payload, _ = self.call("verify_claim", tool="read_file", output=loop_guard.fail("boom"),
                               expect="contains:boom")
        self.assertEqual(payload["refused_by"], "not_failure")

    def test_holding_without_a_re_observation_is_observed_only(self):
        payload = self.ok("verify_claim", tool="read_file", output="foo bar",
                             expect="contains:foo")
        self.assertEqual(payload["level"], "observed")
        self.assertEqual(payload["independence"], "none")

    def test_a_second_observation_earns_invariant_and_says_where_it_came_from(self):
        payload = self.ok("verify_claim", tool="read_file", output="foo bar",
                             expect="contains:foo", second_output="foo baz")
        self.assertEqual(payload["level"], "invariant")
        self.assertEqual(payload["independence"], "caller-attested")
        self.assertIn("note", payload)

    def test_a_caller_cannot_widen_the_deterministic_set(self):
        # `grep` is not in verifier.DETERMINISTIC_TOOLS, so even with a second
        # observation it is confirmed at `observed`. There is no argument that
        # changes this — that is the point of the test.
        payload = self.ok("verify_claim", tool="grep", output="foo bar",
                             expect="contains:foo", second_output="foo baz")
        self.assertEqual(payload["level"], "observed")
        self.assertNotIn("grep", payload["deterministic_tools"])

    def test_a_missing_output_is_a_bad_request(self):
        payload, is_error = self.call("verify_claim", tool="read_file", expect="contains:foo")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "bad_request")

    def test_a_missing_tool_is_a_bad_request(self):
        payload, is_error = self.call("verify_claim", output="foo", expect="contains:foo")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "bad_request")

    def test_the_spec_is_reported_so_a_weak_expectation_is_visible(self):
        payload = self.ok("verify_claim", tool="read_file", output="foo bar",
                             expect="contains:foo")
        self.assertEqual(payload["claim"]["expect"], "contains:foo")
        self.assertEqual(payload["spec_strength"], "strong")


# ── 3. memory_record: the upward direction must fail ─────────────────────────

class ProvenanceTest(_Temp):

    def test_an_invariant_write_with_no_named_check_is_refused_and_writes_nothing(self):
        store = self.path("memory.jsonl")
        payload, is_error = self.call("memory_record", store=store, goal="g", tool="read_file",
                                      arg="a", value="v", evidence_level="invariant")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "provenance")
        self.assertFalse(os.path.exists(store),
                         "a refused write must not create the store")

    def test_declared_becomes_volatile(self):
        store = self.path("memory.jsonl")
        payload = self.ok("memory_record", store=store, goal="g", tool="read_file",
                          arg="a", value="v", evidence_level="declared")
        self.assertEqual(payload["record"]["level"], "volatile")
        self.assertIn("unverified", payload["note"])

    def test_an_unknown_evidence_level_falls_down_to_volatile(self):
        # memory.level_for's rule: an unrecognised provenance is an unknown one,
        # and the safe direction is down — never `observed`.
        payload = self.ok("memory_record", store=self.path("m.jsonl"), goal="g",
                          tool="t", arg="a", value="v", evidence_level="totally-invariant")
        self.assertEqual(payload["record"]["level"], "volatile")

    def test_an_invariant_write_that_names_its_check_is_accepted(self):
        payload = self.ok("memory_record", store=self.path("m.jsonl"), goal="g",
                          tool="read_file", arg="a", value="v",
                          evidence_level="invariant",
                          how_verified="read_file:expectation_held+reproduced")
        self.assertEqual(payload["record"]["level"], "invariant")
        self.assertIsNone(payload["note"])


# ── 4. memory_recall: the split is structural ────────────────────────────────

class RecallTest(_Temp):

    def _seed(self, store):
        self.ok("memory_record", store=store, goal="count the widgets", tool="read_file",
                arg="counts.txt", value="widgets: 42",
                evidence_level="invariant", how_verified="read_file:reproduced")
        self.ok("memory_record", store=store, goal="count the widgets", tool="guess",
                arg="counts.txt", value="widgets: 7", evidence_level="declared")

    def test_only_invariant_records_reach_the_facts_section(self):
        store = self.path("memory.jsonl")
        self._seed(store)
        payload = self.ok("memory_recall", store=store, goal="count the widgets")
        self.assertEqual(payload["facts"], ["read_file"])
        self.assertEqual(payload["unverified"], ["guess"])
        self.assertEqual(payload["audit"]["violations"], [])

    def test_a_volatile_record_renders_below_the_unverified_header(self):
        store = self.path("memory.jsonl")
        self._seed(store)
        block = self.ok("memory_recall", store=store, goal="count the widgets")["block"]
        self.assertLess(block.index(memory.FACT_HEADER), block.index(memory.UNVERIFIED_HEADER))
        self.assertIn("widgets: 42", block.split(memory.UNVERIFIED_HEADER)[0])
        self.assertIn("widgets: 7", block.split(memory.UNVERIFIED_HEADER)[1])

    def test_recall_against_a_missing_store_is_empty_not_an_error(self):
        payload = self.ok("memory_recall", store=self.path("nope.jsonl"), goal="anything")
        self.assertEqual(payload["available"], 0)
        self.assertEqual(payload["recalled"], 0)
        self.assertEqual(payload["block"], "")

    def test_a_missing_goal_is_a_bad_request(self):
        payload, is_error = self.call("memory_recall", store=self.path("m.jsonl"))
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "bad_request")


# ── 5. evidence_audit ────────────────────────────────────────────────────────

class EvidenceAuditTest(_Temp):

    def _log(self, name, rows):
        path = self.path(name)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return path

    def test_measurements_are_split_from_stub_artifacts(self):
        path = self._log("q1.jsonl", [
            {"provider": "groq", "model": "llama", "provider_url": "https://api.groq.com/v1"},
            {"provider": "stub", "model": "m"},
            {"provider": "x", "model": "stub-model"},
        ])
        payload = self.ok("evidence_audit", paths=[path])
        log = payload["logs"][0]
        self.assertEqual(log["rows"], 3)
        self.assertEqual(log["measurements"], 1)
        self.assertEqual(log["artifacts"], 2)
        self.assertEqual(log["artifact_markers"]["provider=stub"], 1)

    def test_a_missing_log_fails_loudly_rather_than_reporting_zero(self):
        payload, is_error = self.call("evidence_audit", paths=[self.path("gone.jsonl")])
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "missing_log")
        self.assertEqual(payload["logs"][0]["error"], "missing")

    def test_the_default_path_is_the_isolated_residence_not_the_shipped_log(self):
        # The suite points DRIVE_RESIDENCE at a temp dir, so the default must
        # resolve there. If it ever resolved to the repository's real log, this
        # suite would be reading (and a future one could be writing) the
        # published record — the exact failure `test_support` exists to prevent.
        residence = os.environ["DRIVE_RESIDENCE"]
        payload, is_error = self.call("evidence_audit")
        self.assertTrue(is_error)
        reported = payload["logs"][0]["path"]
        self.assertTrue(reported.startswith(residence), reported)
        self.assertTrue(reported.endswith(os.path.join("evidence", "q1-evidence.jsonl")), reported)


# ── 6. rag_ask: delegation, not reimplementation ─────────────────────────────

class RagAskTest(_Temp):

    def _stub_skill(self, body):
        path = self.path("stub_skill.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("import sys\nprint(%r)\n" % body)
        original = mcp.RAG_SKILL
        mcp.RAG_SKILL = path
        self.addCleanup(setattr, mcp, "RAG_SKILL", original)
        return path

    def test_a_missing_question_is_a_bad_request(self):
        payload, is_error = self.call("rag_ask")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "bad_request")

    def test_it_delegates_to_the_skill_and_returns_its_json(self):
        self._stub_skill(json.dumps({"ok": True, "refused": True, "answer": None,
                                     "reason": "the corpus does not answer this"}))
        payload = self.ok("rag_ask", question="what is the phase clock law")
        self.assertTrue(payload["refused"])
        self.assertEqual(payload["skill"], "builderbro-rag")
        self.assertEqual(payload["exit_code"], 0)

    def test_a_skill_that_returns_non_json_is_reported(self):
        self._stub_skill("not json at all")
        payload, is_error = self.call("rag_ask", question="anything")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "skill_output")

    def test_a_missing_skill_is_reported(self):
        original = mcp.RAG_SKILL
        mcp.RAG_SKILL = self.path("no-such-skill.py")
        self.addCleanup(setattr, mcp, "RAG_SKILL", original)
        payload, is_error = self.call("rag_ask", question="anything")
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "skill_missing")

    def test_the_default_generator_is_offline(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return type("P", (), {"returncode": 0, "stdout": '{"ok":true}', "stderr": ""})()

        original = mcp.subprocess.run
        mcp.subprocess.run = fake_run
        self.addCleanup(setattr, mcp.subprocess, "run", original)

        self.ok("rag_ask", question="q")
        self.assertIn("--generator", seen["argv"])
        self.assertEqual(seen["argv"][seen["argv"].index("--generator") + 1], "extractive")


# ── 7. Transport error mapping ───────────────────────────────────────────────

class ErrorMappingTest(_Temp):

    def _call_via_transport(self, name, arguments):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": name, "arguments": arguments}})
        return reply["result"]

    def test_a_refusal_is_not_an_error(self):
        result = self._call_via_transport("verify_claim",
                                          {"tool": "read_file", "output": "foo"})
        self.assertFalse(result["isError"], "a verdict is not a malfunction")
        self.assertTrue(result["structuredContent"]["refused"])

    def test_a_failure_is_an_error(self):
        result = self._call_via_transport("memory_record",
                                          {"store": self.path("m.jsonl"), "goal": "g",
                                           "tool": "t", "arg": "a", "value": "v",
                                           "evidence_level": "invariant"})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"], "provenance")

    def test_an_unknown_tool_lists_what_exists(self):
        result = self._call_via_transport("nope", {})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"], "unknown_tool")
        self.assertEqual(sorted(result["structuredContent"]["available"]), sorted(mcp.TOOLS))

    def test_non_object_arguments_are_rejected(self):
        payload, is_error = mcp.call_tool("verify_claim", ["not", "an", "object"])
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "bad_request")

    def test_a_raising_handler_is_reported_not_propagated(self):
        def boom(args, env):
            raise RuntimeError("handler exploded")

        original = mcp.TOOLS["rag_ask"]
        mcp.TOOLS["rag_ask"] = dict(original, handler=boom)
        self.addCleanup(lambda: mcp.TOOLS.__setitem__("rag_ask", original))

        payload, is_error = mcp.call_tool("rag_ask", {"question": "q"})
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "tool_error")
        self.assertIn("handler exploded", payload["reason"])

    def test_arguments_are_serialised_into_the_content_block(self):
        result = self._call_via_transport("verify_claim", {"tool": "read_file", "output": "x"})
        text = result["content"][0]["text"]
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(text)["level"], "refused")


# ── 8. The shell entry point ─────────────────────────────────────────────────

class CliTest(_Temp):

    def _run(self, argv):
        out = io.StringIO()
        stdout = sys.stdout
        sys.stdout = out
        try:
            code = mcp.main(argv)
        finally:
            sys.stdout = stdout
        return code, out.getvalue()

    def test_tools_flag_prints_the_schemas(self):
        code, text = self._run(["--tools"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(t["name"] for t in json.loads(text)["tools"]),
                         sorted(mcp.TOOLS))

    def test_call_exits_zero_for_a_verdict(self):
        code, text = self._run(["--call", "verify_claim",
                                "--json", '{"tool":"read_file","output":"foo"}'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["level"], "refused")

    def test_call_exits_one_for_a_failure(self):
        code, _text = self._run(["--call", "memory_record", "--json",
                                 '{"store":"%s","goal":"g","tool":"t","arg":"a","value":"v",'
                                 '"evidence_level":"invariant"}' % self.path("m.jsonl")])
        self.assertEqual(code, 1)

    def test_invalid_json_is_a_clean_error(self):
        code, text = self._run(["--call", "verify_claim", "--json", "{oops"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(text)["error"], "bad_request")

    def test_the_registered_command_line_can_actually_start_the_server(self):
        # The registry names `python3 builderbro_mcp.py`; if that ever stops being
        # a working server, mcp.json is a file that only looks correct.
        spec = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "mcp.json"), encoding="utf-8"))
        entry = spec["mcpServers"]["builderbro"]
        self.assertIn(entry["command"], ("python3", "python"))
        self.assertTrue(entry["args"])
        self.assertIsInstance(entry.get("env", {}), dict)

        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        proc = subprocess.run([entry["command"]] + entry["args"],
                              cwd=here, input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        reply = json.loads(proc.stdout.splitlines()[0])
        self.assertEqual(reply["result"]["serverInfo"]["name"], "builderbro")


if __name__ == "__main__":
    unittest.main(verbosity=2)
