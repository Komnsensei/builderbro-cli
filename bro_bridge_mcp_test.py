#!/usr/bin/env python3
"""Tests for the Freebuff MCP adapter around ``bro_bridge.py``."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bro_bridge_mcp as mcp
import live_freebuff_probe as live

REGISTRY = os.path.join(HERE, ".agents", "mcp.json")


class FakeBridge(object):
    def __init__(self, replies=None):
        self.had_hello = False
        self.requests = []
        self.replies = list(replies or [])
        self._out = io.StringIO()
        self.released = 0

    def handle(self, request):
        self.requests.append(dict(request))
        if request.get("op") == "hello":
            self.had_hello = True
            if self.replies:
                return self.replies.pop(0)
            return {"v": 1, "id": request["id"], "type": "res", "ok": True,
                    "server": "bro_bridge/1"}
        if self.replies:
            return self.replies.pop(0)
        return {"v": 1, "id": request["id"], "type": "res", "ok": True,
                "op": request["op"]}

    def release(self):
        self.released += 1
        return [{"pid": 123, "signal": 15, "dry_run": False}]


class ProtocolTest(unittest.TestCase):
    def test_initialize_identifies_the_bridge_server(self):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05"}},
                           mcp.BridgeMCP(FakeBridge()))
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(reply["result"]["serverInfo"]["name"], "bro-bridge")
        self.assertIn("tools", reply["result"]["capabilities"])

    def test_notifications_and_ping_follow_mcp_shape(self):
        adapter = mcp.BridgeMCP(FakeBridge())
        self.assertIsNone(mcp.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, adapter))
        self.assertEqual(mcp.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"}, adapter)["result"], {})

    def test_every_advertised_tool_has_a_usable_schema(self):
        tools = mcp.tool_schemas()
        self.assertEqual([tool["name"] for tool in tools], list(mcp.TOOLS))
        for tool in tools:
            self.assertTrue(tool["description"].strip())
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIsInstance(schema.get("properties", {}), dict)
            for required in schema.get("required", []):
                self.assertIn(required, schema["properties"])

    def test_a_malformed_line_does_not_end_the_session(self):
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"pi\n'
                            '{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
        stdout = io.StringIO()
        adapter = mcp.BridgeMCP(FakeBridge())
        self.assertEqual(mcp.serve(stdin=stdin, stdout=stdout, adapter=adapter), 0)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["id"], 2)

    def test_stdout_contains_only_json_rpc(self):
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
                            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n')
        stdout = io.StringIO()
        mcp.serve(stdin=stdin, stdout=stdout,
                 adapter=mcp.BridgeMCP(FakeBridge()))
        for line in stdout.getvalue().splitlines():
            json.loads(line)


class MappingTest(unittest.TestCase):
    def setUp(self):
        self.bridge = FakeBridge()
        self.adapter = mcp.BridgeMCP(self.bridge)

    def requests(self):
        return [request for request in self.bridge.requests
                if request.get("op") != "hello"]

    def test_hello_is_negotiated_once_and_status_is_read_only(self):
        mcp.call_tool("bridge_status", {}, self.adapter)
        mcp.call_tool("bridge_status", {}, self.adapter)
        self.assertEqual([r["op"] for r in self.bridge.requests],
                         ["hello", "status", "status"])

    def test_attach_defaults_to_read_only_adoption(self):
        mcp.call_tool("bridge_attach", {}, self.adapter)
        request = self.requests()[-1]
        self.assertEqual(request["op"], "attach")
        self.assertTrue(request["adopt"])
        self.assertFalse(request["spawn"])
        self.assertEqual(request["cwd"], HERE)

    def test_spawn_must_be_explicit_and_disables_adopt_by_default(self):
        mcp.call_tool("bridge_attach", {"spawn": True}, self.adapter)
        request = self.requests()[-1]
        self.assertTrue(request["spawn"])
        self.assertFalse(request["adopt"])

    def test_attach_rejects_a_relative_cwd(self):
        payload, is_error = mcp.call_tool("bridge_attach", {"cwd": "."}, self.adapter)
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "bad_request")
        self.assertIn("absolute", payload["reason"])

    def test_observe_forces_post_off(self):
        mcp.call_tool("bridge_observe", {"turn_id": "observe-1"}, self.adapter)
        request = self.requests()[-1]
        self.assertEqual(request["op"], "observe")
        self.assertFalse(request["post"])
        self.assertNotIn("task", request)

    def test_ask_defaults_to_posting_and_preserves_the_expectation(self):
        expect = {"tool": "read_files", "arg": ".agents/mcp.json",
                  "check": "contains:bro-bridge"}
        mcp.call_tool("bridge_ask", {"turn_id": "ask-1", "task": "verify",
                                     "expect": expect}, self.adapter)
        request = self.requests()[-1]
        self.assertEqual(request["op"], "ask")
        self.assertTrue(request["post"])
        self.assertEqual(request["task"], "verify")
        self.assertEqual(request["expect"], expect)

    def test_wrong_boolean_types_are_bad_requests(self):
        payload, is_error = mcp.call_tool(
            "bridge_ask", {"turn_id": "x", "task": "y", "post": "false"},
            self.adapter)
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "bad_request")

    def test_a_foreign_lock_refusal_is_not_an_mcp_execution_error(self):
        self.bridge.had_hello = True
        self.bridge.replies = [{
            "v": 1, "id": "mcp-2", "type": "err", "ok": False,
            "code": "E_LOCK_FOREIGN", "message": "foreign",
            "retryable": True, "fallback_eligible": True,
        }]
        payload, is_error = mcp.call_tool("bridge_status", {}, self.adapter)
        self.assertFalse(is_error)
        self.assertTrue(payload["refused"])
        self.assertEqual(payload["code"], "E_LOCK_FOREIGN")

    def test_unreadable_record_remains_an_mcp_execution_error(self):
        self.bridge.had_hello = True
        self.bridge.replies = [{
            "v": 1, "id": "mcp-2", "type": "err", "ok": False,
            "code": "E_UNREADABLE", "message": "torn",
            "retryable": True, "fallback_eligible": True,
        }]
        payload, is_error = mcp.call_tool("bridge_status", {}, self.adapter)
        self.assertTrue(is_error)
        self.assertEqual(payload["code"], "E_UNREADABLE")

    def test_shutdown_releases_only_the_bridge_child(self):
        self.bridge.had_hello = True
        self.bridge.replies = [{
            "v": 1, "id": "mcp-1", "type": "res", "ok": True,
            "stopping": True, "signals": [],
        }]
        payload, is_error = mcp.call_tool("bridge_shutdown", {}, self.adapter)
        self.assertFalse(is_error)
        self.assertTrue(payload["stopping"])
        self.assertEqual(self.bridge.released, 1)
        self.assertEqual(payload["signals"][0]["pid"], 123)

    def test_unknown_tool_lists_the_surface(self):
        payload, is_error = mcp.call_tool("nope", {}, self.adapter)
        self.assertTrue(is_error)
        self.assertEqual(payload["error"], "unknown_tool")
        self.assertEqual(payload["available"], list(mcp.TOOLS))


class CompletionMarkerTest(unittest.TestCase):
    def passed_summary(self):
        return {
            "spawn": {
                "attached": {"type": "res", "state": "ATTACHED"},
                "unchanged": True,
                "mcp": {"prompt_id": "user-1", "reply_id": "ai-1", "seconds": 4.2,
                        "tools": [{"name": name} for name in live.MCP_BRIDGE_TOOLS]},
            }
        }

    def test_seven_ordered_calls_and_an_untouched_lock_pass(self):
        result = live.mcp_completion(self.passed_summary())
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "passed")
        self.assertEqual(result["observed"], list(live.MCP_BRIDGE_TOOLS))
        self.assertEqual(result["reasons"], [])

    def test_a_missing_call_fails_with_the_missing_name(self):
        summary = self.passed_summary()
        summary["spawn"]["mcp"]["tools"].pop()
        result = live.mcp_completion(summary)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertIn("bridge_shutdown", " ".join(result["reasons"]))

    def test_a_repeated_or_reordered_call_fails_the_exact_sequence(self):
        summary = self.passed_summary()
        tools = summary["spawn"]["mcp"]["tools"]
        tools[1], tools[2] = tools[2], tools[1]
        tools.append(dict(tools[0]))
        result = live.mcp_completion(summary)
        self.assertFalse(result["ok"])
        self.assertIn("bro-bridge__bridge_status", result["duplicates"])
        self.assertIn("order differs", " ".join(result["reasons"]))

    def test_status_write_is_atomic_and_replaces_the_previous_document(self):
        original = live.STATUS
        with tempfile.TemporaryDirectory() as directory:
            live.STATUS = os.path.join(directory, "status.json")
            try:
                live.write_status({"state": "running", "ok": None})
                live.write_status({"state": "passed", "ok": True})
                with open(live.STATUS, encoding="utf-8") as handle:
                    payload = json.load(handle)
                self.assertEqual(payload["state"], "passed")
                self.assertFalse(os.path.exists(live.STATUS + ".tmp"))
            finally:
                live.STATUS = original


class RegistryTest(unittest.TestCase):
    def entries(self):
        with open(REGISTRY, encoding="utf-8") as handle:
            return json.load(handle)["mcpServers"]

    def test_bridge_is_registered_with_the_loader_strict_schema(self):
        entry = self.entries()["bro-bridge"]
        self.assertEqual(set(entry), {"type", "command", "args", "env"})
        self.assertEqual(entry["type"], "stdio")
        self.assertIn(entry["command"], ("python", "python3"))
        self.assertEqual(len(entry["args"]), 1)
        self.assertTrue(os.path.isabs(entry["args"][0]))
        self.assertTrue(os.path.isfile(entry["args"][0]))

    def test_registered_server_completes_initialize_and_tools_list(self):
        entry = self.entries()["bro-bridge"]
        proc = subprocess.run(
            [entry["command"]] + entry["args"], cwd=tempfile.gettempdir(),
            input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
                  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "bro-bridge")
        self.assertEqual([tool["name"] for tool in replies[1]["result"]["tools"]],
                         list(mcp.TOOLS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
