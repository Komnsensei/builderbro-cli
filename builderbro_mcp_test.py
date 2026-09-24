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

4. **It sits somewhere nothing reads.** Registering the server is not "the JSON
   is valid": the loader only opens `mcp.json` from a `.agents` directory, and a
   stdio entry is a *strict* schema, so a misplacement or one extra key disables
   the whole surface **in silence** — no error, no tools, a loop quietly less
   capable than the repository claims. `RegistryTest` asserts placement, key set,
   transport and env against the loader's own code.

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

HERE = os.path.dirname(os.path.abspath(__file__))

# Where Freebuff's loader opens this repository's registry: it walks
# `<cwd>/.agents`, `<cwd>/../.agents` and `~/.agents` and reads `mcp.json` in
# each. A copy at the repository root is never opened (see `RegistryTest`).
REGISTRY = os.path.join(HERE, ".agents", "mcp.json")


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
    def test_the_default_residence_does_not_move_with_the_client_cwd(self):
        # The client spawns this server with an inherited cwd, so a relative
        # default is a function of where the session happened to be started.
        # From a cwd that is not the repository the default must still be this
        # repository's own residence — measured before the anchor fix:
        # `{"error": "missing_log", "isError": true}` from a foreign cwd.
        saved = os.environ.pop("DRIVE_RESIDENCE", None)
        if saved:
            self.addCleanup(os.environ.__setitem__, "DRIVE_RESIDENCE", saved)
        start = os.getcwd()
        self.addCleanup(os.chdir, start)
        os.chdir(self.dir)
        payload = self.ok("evidence_audit")
        log = payload["logs"][0]
        self.assertTrue(os.path.isabs(log["path"]), log["path"])
        self.assertTrue(log["path"].startswith(HERE), log["path"])
        self.assertGreater(log["rows"], 0)


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
        # The registry names `python3 builderbro_mcp.py`. Spawning it the way the
        # loader does — inherited cwd, no `cwd` argument, newline-delimited
        # framing — is what keeps `.agents/mcp.json` from being a file that only
        # looks correct. (`RegistryTest` covers the shape it must have to be read
        # at all.)
        with open(REGISTRY, encoding="utf-8") as handle:
            entry = json.load(handle)["mcpServers"]["builderbro"]
        self.assertIn(entry["command"], ("python3", "python"))
        self.assertTrue(entry["args"])

        import subprocess
        proc = subprocess.run(
            [entry["command"]] + entry["args"], cwd=HERE,
            input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
                  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "builderbro")
        names = [tool["name"] for tool in replies[1]["result"]["tools"]]
        self.assertEqual(sorted(names), sorted(mcp.TOOLS))
        for name in names:
            self.assertNotIn("__", name, "the loader prefixes the server name with `__`")

    def test_the_tools_find_the_repository_from_a_foreign_cwd(self):
        # The whole bridge, started the way the client starts it — inherited cwd,
        # newline-delimited framing — from a directory that is not the
        # repository. The residence-relative defaults must still resolve to this
        # repository: before the anchor fix this returned `missing_log` with
        # `isError: true`, which a client reports as a tool that failed.
        import subprocess
        import tempfile

        entry = json.load(open(REGISTRY, encoding="utf-8"))["mcpServers"]["builderbro"]
        env = {k: v for k, v in os.environ.items() if k != "DRIVE_RESIDENCE"}
        with tempfile.TemporaryDirectory() as foreign:
            proc = subprocess.run(
                [entry["command"]] + entry["args"], cwd=foreign, env=env,
                input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
                      '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":'
                      '{"name":"evidence_audit","arguments":{}}}\n',
                capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        result = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()][-1]["result"]
        self.assertFalse(result["isError"], result.get("content"))
        payload = json.loads(result["content"][0]["text"])
        self.assertTrue(payload.get("ok"), payload)
        self.assertEqual(payload["logs"][0]["path"],
                         os.path.join(HERE, "freebrain-residence", "evidence",
                                      "q1-evidence.jsonl"))


# ── 9. The registry, against the loader's own contract ───────────────────────

class RegistryTest(_Temp):
    """The four ways this file can be ignored, all of them silent.

    Read out of the installed Freebuff binary (`~/.config/manicode/freebuff`),
    not out of documentation. Its loader walks `[<cwd>/.agents, <cwd>/../.agents,
    ~/.agents]`, opens `mcp.json` in each, and `continue`s past every path that
    is missing, unparseable, or fails the schema — so each failure below costs
    the loop its tools and reports nothing. This repository had the first one:
    the registry sat at the repository root, where the loader never looks.
    """

    # The loader's `KJT`: `z.strictObject` with exactly these four keys (`type`
    # defaults to "stdio", `args` to `[]`, `env` to `{}`). `cwd` is *not* among
    # them even though the spawn would honour it, so a relative command resolves
    # against the client's process cwd and cannot be pinned here.
    STDIO_KEYS = {"type", "command", "args", "env"}

    def entries(self):
        with open(REGISTRY, encoding="utf-8") as handle:
            return json.load(handle)["mcpServers"]

    def test_the_registry_sits_where_the_loader_actually_looks(self):
        self.assertTrue(os.path.isfile(REGISTRY),
                        "the loader only opens mcp.json inside a `.agents` directory")
        self.assertFalse(os.path.exists(os.path.join(HERE, "mcp.json")),
                         "a repository-root mcp.json is never read, and a decoy "
                         "here is worse than nothing: it looks like the registry")

    def test_the_registry_declares_at_least_one_server(self):
        self.assertTrue(self.entries(), "an empty mcpServers loads nothing")
        self.assertIn("builderbro", self.entries())

    def test_every_entry_uses_only_keys_the_loader_schema_allows(self):
        # strictObject means one unknown key fails safeParse for the *whole
        # file*: adding `cwd` here would unregister every server at once.
        for name, entry in self.entries().items():
            with self.subTest(server=name):
                self.assertEqual(set(entry) - self.STDIO_KEYS, set(),
                                 "the loader rejects unknown keys, and a rejected "
                                 "entry takes the whole file with it")
                self.assertIsInstance(entry["command"], str)
                self.assertTrue(entry["command"].strip())
                self.assertIsInstance(entry.get("args", []), list)
                for arg in entry.get("args", []):
                    self.assertIsInstance(arg, str)
                self.assertIsInstance(entry.get("env", {}), dict)

    def test_the_transport_is_the_one_the_loader_defaults_to(self):
        for name, entry in self.entries().items():
            with self.subTest(server=name):
                self.assertEqual(entry.get("type", "stdio"), "stdio")

    def test_no_env_value_defers_to_an_undefined_variable(self):
        # The loader resolves a `"$NAME"` env value from its own process and
        # throws `Missing environment variable '<NAME>' required by MCP server
        # '<name>' in mcp.json` — which skips the file, like every other fault.
        for name, entry in self.entries().items():
            for key, value in (entry.get("env") or {}).items():
                with self.subTest(server=name, key=key):
                    self.assertIsInstance(value, str)
                    if value.startswith("$"):
                        self.assertIn(value[1:], os.environ)

    def test_every_server_name_namespaces_unambiguously(self):
        # Tools register as `<server>__<tool>`, split on a literal "__".
        for name in self.entries():
            with self.subTest(server=name):
                self.assertNotIn("__", name)
                self.assertEqual(name, name.strip())

    def test_the_spawn_is_pinned_to_an_absolute_path(self):
        # Discovery walks *up* from the cwd (`<cwd>/.agents`, `<cwd>/../.agents`,
        # `~/.agents`), but the spawn the loader performs inherits that same cwd
        # and `cwd` is not a key its schema allows — so a relative path is the
        # one part of this contract the registry cannot express. Starting a
        # session one directory down still finds this file and then fails to
        # start the server. Pin the path.
        for name, entry in self.entries().items():
            with self.subTest(server=name):
                for arg in entry.get("args", []):
                    if os.sep in arg or arg.endswith(".py"):
                        self.assertTrue(
                            os.path.isabs(arg),
                            "%r resolves against the client's process cwd" % arg)
                        self.assertTrue(os.path.isfile(arg), arg)

    def test_a_subdirectory_launch_is_what_pins_the_path(self):
        # Negative control, executed rather than asserted: from a directory one
        # level below the repository the loader still finds `.agents/mcp.json`
        # because it walks up, so the argument's resolution is the only thing
        # left that can break the bridge — and the relative form does, with the
        # `FileNotFoundError` the client reports as a server that failed to
        # start.
        import subprocess

        entry = self.entries()["builderbro"]
        subdir = os.path.join(HERE, ".agents")

        def spawn(args):
            return subprocess.run(
                [entry["command"]] + args, cwd=subdir,
                input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
                capture_output=True, text=True, timeout=120)

        relative = spawn([os.path.basename(entry["args"][0])])
        self.assertNotEqual(relative.returncode, 0, relative.stdout)
        self.assertIn("No such file or directory", relative.stderr)

        pinned = spawn(entry["args"])
        self.assertEqual(pinned.returncode, 0, pinned.stderr[-400:])
        reply = json.loads(pinned.stdout.splitlines()[0])
        self.assertEqual(reply["result"]["serverInfo"]["name"], "builderbro")


if __name__ == "__main__":
    unittest.main(verbosity=2)
