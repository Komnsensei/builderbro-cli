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
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bro_bridge as bridge_mod  # noqa: E402

MAX_VIDEO_RESPONSE_BYTES = 512 * 1024 * 1024
VIDEO_DEFAULT_API_URL = "http://127.0.0.1:8000"


class VideoToolError(RuntimeError):
    """A video API/configuration failure that must not be mistaken for a render."""


def _video_config():
    base = (os.getenv("VIDEO_MCP_API_URL") or os.getenv("AI_CREATIVE_STUDIO_URL")
            or VIDEO_DEFAULT_API_URL).strip().rstrip("/")
    token = (os.getenv("VIDEO_MCP_API_TOKEN") or os.getenv("AI_CREATIVE_STUDIO_TOKEN")
             or os.getenv("DEMO_TENANT_TOKEN") or "").strip()
    output = Path(os.getenv("VIDEO_MCP_OUTPUT_DIR")
                  or os.getenv("AI_CREATIVE_STUDIO_DOWNLOAD_DIR")
                  or str(Path.home() / "Videos" / "AI_Creative_Studio")).expanduser().resolve()
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise VideoToolError("VIDEO_MCP_API_URL must be an absolute HTTP(S) URL without embedded credentials")
    if not token:
        raise VideoToolError("Set VIDEO_MCP_API_TOKEN to a valid AI Creative Studio tenant bearer token")
    return base, token, output


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward the tenant bearer token to a different origin."""
    def __init__(self, origin):
        super().__init__()
        self.origin = origin

    @staticmethod
    def _origin(url):
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme.lower(), parsed.hostname, parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self._origin(newurl) != self.origin:
            raise urllib.error.HTTPError(req.full_url, code, "cross-origin redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _video_request(method, path, *, payload=None, idempotency_key=None, expect_json=True):
    base, token, _ = _video_config()
    if not path.startswith("/") or path.startswith("//"):
        raise VideoToolError("Internal video API path must start with one slash")
    url = base + path
    parsed = urllib.parse.urlparse(base)
    origin = (parsed.scheme.lower(), parsed.hostname,
              parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json" if expect_json else "video/mp4"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(_SameOriginRedirectHandler(origin))
    try:
        with opener.open(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_VIDEO_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise VideoToolError("Video API HTTP %s: %s" % (exc.code, detail[:1200])) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise VideoToolError("Video API request failed; if generation submission timed out, reuse the same idempotency key and check job status before retrying: %s" % exc) from exc
    if len(body) > MAX_VIDEO_RESPONSE_BYTES:
        raise VideoToolError("Video API response exceeded the 512 MiB limit")
    if not expect_json:
        return content_type, body
    if "json" not in content_type.lower():
        raise VideoToolError("Video API returned a non-JSON response where JSON was expected")
    try:
        result = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoToolError("Video API returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise VideoToolError("Video API JSON response must be an object")
    return result


def _video_engine_status():
    return _video_request("GET", "/v1/videos/engine")


def _video_preflight():
    engine = _video_engine_status()
    providers = engine.get("configured_providers") or []
    allowed = {item.strip() for item in
               (os.getenv("VIDEO_MCP_FREE_PROVIDER_ALLOWLIST")
                or os.getenv("VIDEO_FREE_PROVIDER_ALLOWLIST") or "").split(",") if item.strip()}
    reasons = []
    if engine.get("configured") is not True:
        reasons.append(engine.get("message") or "no real video renderer is configured")
    if not providers:
        reasons.append("the engine did not report any configured real renderer")
    if not allowed:
        reasons.append("the zero-cost provider allowlist is empty")
    unapproved = sorted(set(providers) - allowed)
    if unapproved:
        reasons.append("provider(s) not attested as zero-cost: " + ", ".join(unapproved))
    return {
        "decision": "PASS" if not reasons else "BLOCKED",
        "policy": "zero-cost-only",
        "configured_providers": providers,
        "allowlisted_providers": sorted(allowed),
        "reasons": reasons,
        "note": "The allowlist is an operator attestation, not independent proof of billing terms."
    }


def _safe_video_job_id(value):
    job_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", job_id):
        raise ValueError("job_id contains invalid characters")
    return job_id


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

    def tool_video_engine_status(self, args):
        return _video_engine_status()

    def tool_video_preflight(self, args):
        return _video_preflight()

    def tool_generate_video(self, args):
        preflight = _video_preflight()
        if preflight["decision"] != "PASS":
            return {"ok": False, **preflight, "submitted": False}
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("`prompt` must be a non-empty string")
        duration = args.get("duration_seconds", 5)
        if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 30:
            raise ValueError("`duration_seconds` must be an integer from 1 to 30")
        key = str(args.get("idempotency_key") or secrets.token_urlsafe(24)).strip()
        if not key or len(key) > 200:
            raise ValueError("`idempotency_key` must contain 1–200 characters")
        payload = {
            "prompt": prompt.strip(),
            "provider": "video-cascade",
            "duration_seconds": duration,
            "aspect_ratio": args.get("aspect_ratio", "16:9"),
            "resolution": args.get("resolution", "720p"),
            "fps": args.get("fps", 24),
        }
        for field in ("negative_prompt", "model", "shots"):
            if field in args:
                payload[field] = args[field]
        try:
            result = _video_request("POST", "/v1/videos/generate",
                                    payload=payload, idempotency_key=key)
        except VideoToolError as exc:
            raise VideoToolError("%s; idempotency_key=%s (reuse this exact key; do not create a new job)" % (exc, key)) from exc
        result["idempotency_key"] = key
        result["preflight"] = preflight
        result["next_action"] = "Poll video_job_status with this job_id; download only after status is succeeded and simulated is false."
        return result

    def tool_video_job_status(self, args):
        job_id = _safe_video_job_id(args.get("job_id"))
        return _video_request("GET", "/v1/videos/jobs/" + urllib.parse.quote(job_id, safe=""))

    def tool_video_download(self, args):
        job_id = _safe_video_job_id(args.get("job_id"))
        status = _video_request("GET", "/v1/videos/jobs/" + urllib.parse.quote(job_id, safe=""))
        if status.get("status") != "succeeded":
            raise VideoToolError("video is not ready; current status is %r" % status.get("status"))
        if status.get("simulated") is True:
            raise VideoToolError("refusing to download simulated output as a real video")
        result = _video_request("GET", "/v1/videos/jobs/" + urllib.parse.quote(job_id, safe="") + "/result")
        asset = result.get("asset") or {}
        if result.get("simulated") is True or asset.get("simulated") is True:
            raise VideoToolError("refusing to download simulated output as a real video")
        asset_id = str(asset.get("asset_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", asset_id):
            raise VideoToolError("video result did not contain a valid asset_id")
        content_type, data = _video_request(
            "GET", "/v1/assets/" + urllib.parse.quote(asset_id, safe="") + "/download",
            expect_json=False)
        if "video/mp4" not in content_type.lower() or len(data) < 12 or data[4:8] != b"ftyp":
            raise VideoToolError("download is not a valid MP4 response")
        digest = hashlib.sha256(data).hexdigest()
        claimed = asset.get("checksum_sha256")
        if claimed and str(claimed).lower() != digest:
            raise VideoToolError("downloaded MP4 checksum does not match the asset record")
        _, _, output_root = _video_config()
        filename = str(args.get("filename") or (job_id + ".mp4"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.mp4", filename, re.IGNORECASE):
            raise ValueError("filename must be a simple filename ending in .mp4")
        output_root.mkdir(parents=True, exist_ok=True)
        target = (output_root / filename).resolve()
        if target.parent != output_root:
            raise ValueError("filename escapes the configured output directory")
        temp = target.with_suffix(".mp4.part")
        try:
            temp.write_bytes(data)
            temp.replace(target)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise VideoToolError("could not save MP4: %s" % exc) from exc
        return {"ok": True, "job_id": job_id, "status": "succeeded",
                "simulated": False, "saved_path": str(target), "size_bytes": len(data),
                "checksum_sha256": digest, "provider": asset.get("provider")}


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
    ("video_engine_status", {
        "handler": BridgeMCP.tool_video_engine_status,
        "description": "Check the existing AI Creative Studio video API and report configured real render targets. Does not submit a job.",
        "inputSchema": {"type": "object", "properties": {}},
    }),
    ("video_preflight", {
        "handler": BridgeMCP.tool_video_preflight,
        "description": "Read-only zero-cost gate. Requires a configured real renderer and every configured target explicitly listed in VIDEO_MCP_FREE_PROVIDER_ALLOWLIST or VIDEO_FREE_PROVIDER_ALLOWLIST.",
        "inputSchema": {"type": "object", "properties": {}},
    }),
    ("generate_video", {
        "handler": BridgeMCP.tool_generate_video,
        "description": "Submit one idempotent durable video job to the existing AI Creative Studio real cascade after zero-cost preflight. A returned job ID means accepted, not rendered.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "maxLength": 2000},
                "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 30, "default": 5},
                "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1", "4:3", "3:4"], "default": "16:9"},
                "resolution": {"type": "string", "enum": ["720p", "1080p"], "default": "720p"},
                "fps": {"type": "integer", "enum": [24, 30, 60], "default": 24},
                "negative_prompt": {"type": "string", "maxLength": 1000},
                "model": {"type": "string", "maxLength": 160},
                "shots": {"type": "array", "maxItems": 8, "items": {"type": "object"}},
                "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": ["prompt"],
        },
    }),
    ("video_job_status", {
        "handler": BridgeMCP.tool_video_job_status,
        "description": "Poll the exact existing video job and its durable status. Do not resubmit after a timeout.",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string", "minLength": 1, "maxLength": 200}}, "required": ["job_id"]},
    }),
    ("video_download", {
        "handler": BridgeMCP.tool_video_download,
        "description": "Save a completed non-simulated MP4 under the configured output directory after checking media type, MP4 signature and SHA-256 checksum.",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string", "minLength": 1, "maxLength": 200}, "filename": {"type": "string", "maxLength": 124}}, "required": ["job_id"]},
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
