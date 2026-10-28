#!/usr/bin/env python3
"""Focused tests for video tools integrated into the existing bro-bridge MCP."""
import hashlib
import os
import tempfile
import unittest

import bro_bridge_mcp as mcp


class VideoToolsTest(unittest.TestCase):
    def setUp(self):
        self.env = {
            "VIDEO_MCP_API_URL": "http://127.0.0.1:8000",
            "VIDEO_MCP_API_TOKEN": "test-token",
            "VIDEO_MCP_FREE_PROVIDER_ALLOWLIST": "free-renderer",
        }
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)

    def tearDown(self):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_existing_mcp_surface_keeps_bridge_and_adds_video_tools(self):
        names = [item["name"] for item in mcp.tool_schemas()]
        self.assertTrue({"bridge_status", "bridge_attach", "bridge_ask", "bridge_shutdown"}.issubset(names))
        self.assertTrue({"video_engine_status", "video_preflight", "generate_video",
                         "video_job_status", "video_download"}.issubset(names))
        self.assertEqual(len(names), len(set(names)))

    def test_preflight_blocks_unallowlisted_target(self):
        original = mcp._video_request
        calls = []
        def fake(method, path, **kwargs):
            calls.append((method, path))
            return {"configured": True, "configured_providers": ["free-renderer", "unknown-paid"]}
        mcp._video_request = fake
        try:
            result = mcp.call_tool("video_preflight", {})
            payload = result[0]
            self.assertEqual(payload["decision"], "BLOCKED")
            self.assertIn("unknown-paid", " ".join(payload["reasons"]))
            self.assertEqual(calls, [("GET", "/v1/videos/engine")])
        finally:
            mcp._video_request = original

    def test_generate_runs_preflight_and_does_not_post_when_blocked(self):
        original = mcp._video_request
        calls = []
        def fake(method, path, **kwargs):
            calls.append((method, path))
            return {"configured": True, "configured_providers": ["unknown-paid"]}
        mcp._video_request = fake
        try:
            payload, is_error = mcp.call_tool("generate_video", {"prompt": "cinematic fox"})
            self.assertFalse(is_error)
            self.assertEqual(payload["decision"], "BLOCKED")
            self.assertFalse(payload["submitted"])
            self.assertEqual(calls, [("GET", "/v1/videos/engine")])
        finally:
            mcp._video_request = original

    def test_generate_submits_once_with_stable_idempotency_key(self):
        original = mcp._video_request
        calls = []
        def fake(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return {"configured": True, "configured_providers": ["free-renderer"]}
            return {"job_id": "task_123", "status": "running", "simulated": False}
        mcp._video_request = fake
        try:
            payload, is_error = mcp.call_tool("generate_video", {
                "prompt": "cinematic fox", "idempotency_key": "stable-key"
            })
            self.assertFalse(is_error)
            self.assertEqual(payload["job_id"], "task_123")
            self.assertEqual(payload["idempotency_key"], "stable-key")
            self.assertEqual(calls[0][0:2], ("GET", "/v1/videos/engine"))
            self.assertEqual(calls[1][0:2], ("POST", "/v1/videos/generate"))
            self.assertEqual(calls[1][2]["idempotency_key"], "stable-key")
            self.assertEqual(calls[1][2]["payload"]["provider"], "video-cascade")
        finally:
            mcp._video_request = original

    def test_download_refuses_simulated_result_before_fetching_bytes(self):
        original = mcp._video_request
        calls = []
        def fake(method, path, **kwargs):
            calls.append(path)
            return {"job_id": "task_123", "status": "succeeded", "simulated": True}
        mcp._video_request = fake
        try:
            payload, is_error = mcp.call_tool("video_download", {"job_id": "task_123"})
            self.assertTrue(is_error)
            self.assertIn("simulated", payload["reason"])
            self.assertEqual(calls, ["/v1/videos/jobs/task_123"])
        finally:
            mcp._video_request = original

    def test_download_checks_checksum_and_writes_within_output_root(self):
        original = mcp._video_request
        video = b"\\x00\\x00\\x00\\x18ftypmp42" + b"real-video-test"
        digest = hashlib.sha256(video).hexdigest()
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            os.environ["VIDEO_MCP_OUTPUT_DIR"] = directory
            def fake(method, path, **kwargs):
                calls.append((method, path))
                if path == "/v1/videos/jobs/task_456":
                    return {"job_id": "task_456", "status": "succeeded", "simulated": False}
                if path == "/v1/videos/jobs/task_456/result":
                    return {"simulated": False, "asset": {"asset_id": "asset_789",
                            "simulated": False, "checksum_sha256": digest, "provider": "free-renderer"}}
                if path == "/v1/assets/asset_789/download":
                    return "video/mp4", video
                raise AssertionError(path)
            mcp._video_request = fake
            try:
                payload, is_error = mcp.call_tool("video_download", {
                    "job_id": "task_456", "filename": "finished.mp4"
                })
                self.assertFalse(is_error)
                self.assertEqual(payload["checksum_sha256"], digest)
                with open(os.path.join(directory, "finished.mp4"), "rb") as handle:
                    self.assertEqual(handle.read(), video)
                self.assertEqual(len(calls), 3)
            finally:
                mcp._video_request = original
                os.environ.pop("VIDEO_MCP_OUTPUT_DIR", None)

    def test_bad_job_id_is_rejected_before_api_call(self):
        original = mcp._video_request
        mcp._video_request = lambda *args, **kwargs: self.fail("API must not be called")
        try:
            payload, is_error = mcp.call_tool("video_job_status", {"job_id": "../other-tenant"})
            self.assertTrue(is_error)
            self.assertEqual(payload["error"], "bad_request")
        finally:
            mcp._video_request = original


if __name__ == "__main__":
    unittest.main()
