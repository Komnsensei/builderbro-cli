#!/usr/bin/env python3
"""bro_bridge_mcp.py — MCP adapter for the live Freebuff bro bridge.

Freebuff loads stdio MCP servers, while ``bro_bridge.py`` implements the
separate ``§18`` NDJSON protocol used by the bro brain.  This module is the
narrow seam between those protocols: it owns JSON-RPC/MCP framing and tool
schemas, and delegates every lifecycle or turn decision to one in-process
``bro_bridge.Bridge``.

The adapter never writes to stdout except through its JSON-RPC writer.  Bridge
events are routed to a null stream so they cannot corrupt MCP framing.

Freebuff registers this file in ``.agents/mcp.json`` as the ``bro-bridge``
server.  Its tools are therefore exposed to a trusted session as
``bro-bridge__bridge_status``, ``bro-bridge__bridge_attach``, and so on.
"""

import argparse
import collections
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bro_bridge as bridge_mod  # noqa: E402

SERVER_NAME = "bro-bridge"
SERVER_VERSION = "1.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

# A refusal is a working decision, not a broken MCP tool.  Everything else that
# the bridge reports as `err` remains an MCP execution error so Freebuff does
# not mistake an unreadable record or a dead child for an answer.
REFUSAL_CODES = {
    "E_LOCK_FOREIGN",
    "E_LOCK_MALFORMED",
    "E_PROJECT_AMBIGUOUS",
    "E_REFUSED",
}


class _NullStream(object):
    """Swallow bridge events without allowing them onto MCP stdout."""

    def write(self, _value):
        return None

    def flush(self):
        return None


def fail(error, reason, **extra):
    payload = {"ok": False, "error": error, "reason": reason}
    payload.update(extra)
    return payload


def _bool_arg(args, name, default):
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise ValueError("`%s` must be a boolean" % name)
    return value


def _number_arg(args, name, default, minimum=0):
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("`%s` must be a number" % name)
    if value < minimum:
        raise ValueError("`%s` must be at least %s" % (name, minimum))
    return value


def _turn_fields(args, include_task):
    turn = {
        "turn_id": args.get("turn_id"),
        "expect": args.get("expect"),
        "priority": args.get("priority", "cli"),
        "timeout_s": _number_arg(args, "timeout_s", bridge_mod.TURN_TIMEOUT_S, 0),
    }
    if include_task:
        turn["task"] = args.get("task")
    return turn


class BridgeMCP(object):
    """MCP protocol state around exactly one long-lived ``Bridge`` instance."""

    def __init__(self, bridge=None, home=None, cwd=None):
        self._bridge = bridge
        self._home = home
        self._cwd = os.path.realpath(cwd or HERE)
        self._lock = threading.RLock()
        self._request_id = 0

    def _ensure_bridge(self):
        with self._lock:
            if self._bridge is None:
                self._bridge = bridge_mod.Bridge(
                    home=self._home,
                    cwd=self._cwd,
                    inline=True,
                )
                # A custom bridge still must not be allowed to corrupt stdout.
                self._bridge._out = _NullStream()
            if not self._bridge.had_hello:
                self._request_id += 1
                reply = self._bridge.handle({
                    "v": bridge_mod.VERSION,
                    "id": "hello-%d" % self._request_id,
                    "type": "req",
                    "op": "hello",
                    "client": SERVER_NAME,
                    "proto_min": bridge_mod.PROTOCOL_MIN,
                    "proto_max": bridge_mod.PROTOCOL_MAX,
                })
                if reply.get("type") == "err":
                    return reply
            return None

    def close(self):
        with self._lock:
            if self._bridge is not None:
                return self._bridge.release()
            return []

    def _call_bridge(self, op, fields):
        hello_error = self._ensure_bridge()
        if hello_error is not None:
            return hello_error
        with self._lock:
            self._request_id += 1
            request = dict(fields)
            request.update({
                "v": bridge_mod.VERSION,
                "id": "mcp-%d" % self._request_id,
                "type": "req",
                "op": op,
            })
            frame = self._bridge.handle(request)
        if frame.get("type") == "err" and frame.get("code") in REFUSAL_CODES:
            frame = dict(frame, refused=True)
        return frame

    def tool_bridge_status(self, args):
        return self._call_bridge("status", {})

    def tool_bridge_attach(self, args):
        spawn = _bool_arg(args, "spawn", False)
        adopt = _bool_arg(args, "adopt", not spawn)
        cwd = args.get("cwd") or self._cwd
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError("`cwd` must be a non-empty string")
        if not os.path.isabs(cwd):
            raise ValueError("`cwd` must be an absolute path")
        return self._call_bridge("attach", {"cwd": cwd, "adopt": adopt,
                                            "spawn": spawn})

    def tool_bridge_observe(self, args):
        turn = _turn_fields(args, include_task=False)
        turn["post"] = False
        return self._call_bridge("observe", turn)

    def tool_bridge_ask(self, args):
        turn = _turn_fields(args, include_task=True)
        turn["post"] = _bool_arg(args, "post", True)
        return self._call_bridge("ask", turn)

    def tool_bridge_capture(self, args):
        return self._call_bridge("capture", {
            "bytes": int(_number_arg(args, "bytes", bridge_mod.CAPTURE_MAX, 1)),
        })

    def tool_bridge_stop(self, args):
        return self._call_bridge("stop", {
            "graceful": _bool_arg(args, "graceful", True),
        })

    def tool_bridge_shutdown(self, args):
        frame = self._call_bridge("shutdown", {})
        if frame.get("type") == "res":
            frame = dict(frame, signals=self.close())
        return frame


TOOLS = collections.OrderedDict([
    ("bridge_status", {
        "handler": BridgeMCP.tool_bridge_status,
        "description": (
            "Read the Freebuff singleton-lock state, current bro-bridge session, "
            "conversation watermark, active turn and queue depth. Read-only and "
            "always safe to call."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    }),
    ("bridge_attach", {
        "handler": BridgeMCP.tool_bridge_attach,
        "description": (
            "Attach the bro bridge to a Freebuff conversation. By default this is "
            "a read-only adopt of an existing live session, including the current "
            "Freebuff session. Spawning a Freebuff process requires spawn=true; "
            "a live foreign lock is never signalled or replaced."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string",
                        "description": "Absolute project directory (defaults to this repository)."},
                "adopt": {"type": "boolean",
                          "description": "Read-only attach to a live foreign session."},
                "spawn": {"type": "boolean",
                          "description": "Explicitly allow starting Freebuff when no live owner exists."},
            },
        },
    }),
    ("bridge_observe", {
        "handler": BridgeMCP.tool_bridge_observe,
        "description": (
            "Read and verify the latest settled turn in the attached conversation "
            "without writing to it. The response includes the reply and evidence. "
            "A mid-turn, torn or missing record is reported as unreadable rather "
            "than guessed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "turn_id": {"type": "string",
                            "description": "Stable idempotency key for this observation."},
                "expect": {"type": "object",
                           "description": "Optional verification claim, for example {tool,arg,check}."},
                "timeout_s": {"type": "number", "minimum": 0},
            },
            "required": ["turn_id"],
        },
    }),
    ("bridge_ask", {
        "handler": BridgeMCP.tool_bridge_ask,
        "description": (
            "Run a verified turn through an attached session. Posting defaults to "
            "true and requires a non-empty task. It is refused for a read-only or "
            "foreign session; attach with spawn=true to create a bridge-owned "
            "session. A verification refusal is a result, not a transport error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "turn_id": {"type": "string",
                            "description": "Stable idempotency key; retries do not post twice."},
                "task": {"type": "string", "minLength": 1},
                "post": {"type": "boolean", "default": True},
                "expect": {"type": "object"},
                "priority": {"type": "string", "enum": ["cli", "loop"], "default": "cli"},
                "timeout_s": {"type": "number", "minimum": 0},
            },
            "required": ["turn_id", "task"],
        },
    }),
    ("bridge_capture", {
        "handler": BridgeMCP.tool_bridge_capture,
        "description": (
            "Return bounded raw terminal-pane text from a Freebuff process started "
            "by this bridge. This is diagnostic only; replies are always assembled "
            "from the conversation record."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"bytes": {"type": "integer", "minimum": 1,
                                     "maximum": bridge_mod.CAPTURE_MAX}},
        },
    }),
    ("bridge_stop", {
        "handler": BridgeMCP.tool_bridge_stop,
        "description": (
            "Stop only the Freebuff process this bridge owns. It refuses when no "
            "bridge-owned child exists and never signals a foreign session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"graceful": {"type": "boolean", "default": True}},
        },
    }),
    ("bridge_shutdown", {
        "handler": BridgeMCP.tool_bridge_shutdown,
        "description": (
            "Stop the bridge worker and release only its owned child process. This "
            "is terminal for the current bridge session."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    }),
])


def tool_schemas():
    return [{"name": name, "description": spec["description"],
             "inputSchema": spec["inputSchema"]} for name, spec in TOOLS.items()]


def call_tool(name, args, adapter=None):
    adapter = adapter or BridgeMCP()
    spec = TOOLS.get(name)
    if spec is None:
        return fail("unknown_tool", "no tool named %r" % name, available=list(TOOLS)), True
    if not isinstance(args, dict):
        return fail("bad_request", "`arguments` must be an object"), True
    try:
        payload = spec["handler"](adapter, args)
    except ValueError as exc:
        return fail("bad_request", str(exc)), True
    except Exception as exc:
        return fail("tool_error", "%s: %s" % (type(exc).__name__, exc)), True
    if not isinstance(payload, dict):
        return fail("tool_error", "bridge returned a non-object result"), True
    is_error = payload.get("type") == "err" and not payload.get("refused", False)
    return payload, is_error


def _result(mid, payload):
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(message, adapter=None):
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request: expected an object")
    method = message.get("method")
    mid = message.get("id")
    notification = "id" not in message
    adapter = adapter or BridgeMCP()

    if method == "initialize":
        params = message.get("params") or {}
        version = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION,
                           "title": "Bro live Freebuff bridge"},
        }
        return None if notification else _result(mid, result)
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return None if notification else _result(mid, {})
    if method == "tools/list":
        return None if notification else _result(mid, {"tools": tool_schemas()})
    if method == "tools/call":
        params = message.get("params") or {}
        payload, is_error = call_tool(params.get("name"),
                                      params.get("arguments") or {}, adapter)
        text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        return None if notification else _result(mid, {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": is_error,
        })
    if notification:
        return None
    return _error(mid, -32601, "Method not found: %r" % (method,))


def serve(stdin=None, stdout=None, env=None, adapter=None):
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    adapter = adapter or BridgeMCP(home=(env or os.environ).get("BRO_FREEBUFF_HOME"),
                                  cwd=(env or os.environ).get("BRO_FREEBUFF_CWD") or HERE)
    try:
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError as exc:
                reply = _error(None, -32700, "Parse error: %s" % exc)
            else:
                try:
                    reply = handle(message, adapter)
                except Exception as exc:
                    mid = message.get("id") if isinstance(message, dict) else None
                    reply = _error(mid, -32603,
                                   "Internal error: %s: %s" % (type(exc).__name__, exc))
            if reply is None:
                continue
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    finally:
        adapter.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bro live Freebuff bridge over MCP")
    parser.add_argument("--tools", action="store_true", help="print advertised tool schemas")
    parser.add_argument("--call", metavar="NAME", help="invoke one bridge-backed MCP tool")
    parser.add_argument("--json", default="{}", help="arguments for --call as a JSON object")
    parser.add_argument("--human", action="store_true", help="indent --call output")
    args = parser.parse_args(argv)

    if args.tools:
        print(json.dumps({"tools": tool_schemas()}, indent=2, ensure_ascii=False))
        return 0
    if args.call:
        try:
            call_args = json.loads(args.json)
        except ValueError as exc:
            print(json.dumps(fail("bad_request", "--json is not valid JSON: %s" % exc)))
            return 2
        adapter = BridgeMCP()
        try:
            payload, is_error = call_tool(args.call, call_args, adapter)
        finally:
            adapter.close()
        print(json.dumps(payload, indent=2 if args.human else None,
                         ensure_ascii=False, sort_keys=True))
        return 1 if is_error else 0
    return serve()


if __name__ == "__main__":
    sys.exit(main())
