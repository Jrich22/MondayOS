"""Tests for the dashboard API (dashboard_api/ package).

Two layers:
  * route/service tests drive `route()` directly (no sockets) against a real
    Monday over a temp root, exercising the full read/write/approval surface;
  * a small set of live-server tests (stdlib ThreadingHTTPServer + httpx) cover
    the HTTP edge: malformed JSON, CORS, localhost binding, secret redaction.

Everything uses provider="fake" so the team pipeline runs deterministically
offline — no API keys, no network.
"""
from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from monday import Monday, MondayConfig
from dashboard_api import security
from dashboard_api.router import route
from dashboard_api.server import create_server
from dashboard_api.service import DashboardService


def make_service(root: Path) -> DashboardService:
    monday = Monday(MondayConfig(project_root=root))
    return DashboardService(monday, provider="fake", write_log=root / "logs" / "dashboard_api.jsonl")


class DashboardApiBase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.service = make_service(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def GET(self, path, query=None, origin=None):
        return route(self.service, "GET", path, query=query or {}, origin=origin)

    def POST(self, path, body=None, origin=None):
        return route(self.service, "POST", path, body=body or {}, origin=origin)

    def _new_task(self):
        status, _, body = self.POST("/tasks", {"title": "Vendor Workspace", "objective": "Add a vendor workspace."})
        self.assertEqual(status, 201)
        return body["id"]

    def _run_team(self, task_id):
        status, _, body = self.POST(f"/tasks/{task_id}/team-run", {"mode": "review"})
        self.assertEqual(status, 200)
        return body


class TestReads(DashboardApiBase):
    def test_status(self):
        status, _, body = self.GET("/status")
        self.assertEqual(status, 200)
        self.assertIn("version", body)
        self.assertTrue(body["healthy"])
        self.assertEqual(body["provider"], "fake")

    def test_health_and_revision(self):
        s, _, b = self.GET("/health")
        self.assertEqual(s, 200)
        self.assertTrue(b["ok"])
        self.assertEqual(self.GET("/revision")[2]["revision"], 0)

    def test_products(self):
        status, _, body = self.GET("/products")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    def test_agents(self):
        status, _, body = self.GET("/agents")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)
        # The six default roles seed on first use.
        self.assertTrue(body)
        self.assertTrue(all("activity" in a for a in body))

    def test_tasks_list_and_get(self):
        tid = self._new_task()
        s, _, rows = self.GET("/tasks")
        self.assertTrue(any(t["id"] == tid for t in rows))
        s, _, one = self.GET(f"/tasks/{tid}")
        self.assertEqual(s, 200)
        self.assertEqual(one["id"], tid)

    def test_get_missing_task(self):
        s, _, body = self.GET("/tasks/TASK-9999")
        self.assertEqual(s, 404)
        self.assertEqual(body["error"]["code"], "not-found")

    def test_pull_requests_empty(self):
        s, _, body = self.GET("/pull-requests")
        self.assertEqual(s, 200)
        self.assertEqual(body, [])

    def test_knowledge_search(self):
        # Empty and non-empty queries both return a list (never a crash).
        for q in ("", "cue"):
            s, _, body = self.GET("/knowledge/search", query={"query": q})
            self.assertEqual(s, 200)
            self.assertIsInstance(body, list)

    def test_publish_history(self):
        s, _, body = self.GET("/publish/history")
        self.assertEqual(s, 200)
        self.assertIsInstance(body, list)

    def test_handler_exception_becomes_structured_500(self):
        # Force an internal error and confirm the edge returns a redacted 500,
        # not a bare connection reset.
        broken = make_service(self.root)
        broken._m = None  # type: ignore[assignment]
        s, headers, body = route(broken, "GET", "/status", origin="http://localhost:5273")
        self.assertEqual(s, 500)
        self.assertEqual(body["error"]["code"], "upstream-error")
        self.assertIn("Access-Control-Allow-Origin", headers)  # CORS still present


class TestWrites(DashboardApiBase):
    def test_create_task(self):
        s, _, body = self.POST("/tasks", {"title": "X", "objective": "Do X."})
        self.assertEqual(s, 201)
        self.assertTrue(body["id"])
        self.assertEqual(self.service.revision, 1)

    def test_create_task_validation(self):
        s, _, body = self.POST("/tasks", {"title": ""})
        self.assertEqual(s, 400)
        self.assertEqual(body["error"]["code"], "bad-request")

    def test_team_run_launch(self):
        tid = self._new_task()
        tr = self._run_team(tid)
        self.assertEqual(tr["taskId"], tid)
        self.assertEqual(tr["status"], "awaiting")
        self.assertEqual(len(tr["stages"]), 5)

    def test_invalid_transition(self):
        # A brand-new task is in BACKLOG; completing it directly is invalid.
        tid = self._new_task()
        s, _, body = self.POST(f"/tasks/{tid}/status", {"status": "completed"})
        self.assertEqual(s, 400)
        self.assertEqual(body["error"]["code"], "invalid-transition")

    def test_blocked_status_unsupported(self):
        tid = self._new_task()
        s, _, body = self.POST(f"/tasks/{tid}/status", {"status": "blocked"})
        self.assertEqual(s, 400)
        self.assertEqual(body["error"]["code"], "unsupported")

    def test_no_gated_endpoints(self):
        # commit/push/merge/deploy are simply not routed.
        for path in ("/commit", "/push", "/deploy", "/tasks/T/merge"):
            self.assertEqual(self.POST(path)[0], 404)


class TestApprovals(DashboardApiBase):
    def _await_approval(self):
        tid = self._new_task()
        tr = self._run_team(tid)
        s, _, approvals = self.GET("/approvals")
        self.assertEqual(s, 200)
        opens = [a for a in approvals if a["status"] == "open"]
        self.assertTrue(opens, "expected an open approval")
        return tid, opens[0]["id"]

    def test_approve_completes_task(self):
        tid, run_id = self._await_approval()
        s, _, body = self.POST(f"/agent-runs/{run_id}/approve", {"by": "human:test"})
        self.assertEqual(s, 200)
        self.assertEqual(body["status"], "approved")
        # Task is now completed in MondayOS.
        _, _, task = self.GET(f"/tasks/{tid}")
        self.assertEqual(task["status"], "completed")

    def test_duplicate_approval_is_idempotent(self):
        _, run_id = self._await_approval()
        self.POST(f"/agent-runs/{run_id}/approve", {"by": "human:test"})
        rev_after_first = self.service.revision
        s, _, body = self.POST(f"/agent-runs/{run_id}/approve", {"by": "human:test"})
        self.assertEqual(s, 200)
        self.assertTrue(body.get("alreadyDecided"))
        # Idempotent: no second write / revision bump.
        self.assertEqual(self.service.revision, rev_after_first)

    def test_conflicting_decision_rejected(self):
        _, run_id = self._await_approval()
        self.POST(f"/agent-runs/{run_id}/approve", {})
        s, _, body = self.POST(f"/agent-runs/{run_id}/reject", {"reason": "changed mind"})
        self.assertEqual(s, 409)
        self.assertEqual(body["error"]["code"], "already-decided")

    def test_reject_leaves_task_for_rework(self):
        tid, run_id = self._await_approval()
        s, _, body = self.POST(f"/agent-runs/{run_id}/reject", {"reason": "needs work"})
        self.assertEqual(s, 200)
        self.assertEqual(body["status"], "rejected")
        _, _, task = self.GET(f"/tasks/{tid}")
        self.assertEqual(task["status"], "review")

    def test_approve_missing_run(self):
        s, _, body = self.POST("/agent-runs/run-deadbeef/approve", {})
        self.assertEqual(s, 404)


class TestCorsAndRouting(DashboardApiBase):
    def test_forbidden_origin(self):
        s, _, body = self.GET("/status", origin="http://evil.example")
        self.assertEqual(s, 403)
        self.assertEqual(body["error"]["code"], "forbidden-origin")

    def test_allowed_origin_gets_cors_headers(self):
        origin = "http://localhost:5273"
        s, headers, _ = self.GET("/status", origin=origin)
        self.assertEqual(s, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_preflight(self):
        s, headers, body = route(self.service, "OPTIONS", "/tasks", origin="http://localhost:5273")
        self.assertEqual(s, 204)
        self.assertIn("Access-Control-Allow-Methods", headers)

    def test_no_origin_allowed(self):
        # server-to-server / curl (no Origin) is permitted.
        self.assertEqual(self.GET("/status")[0], 200)


class TestSecretRedaction(unittest.TestCase):
    def test_env_secret_redacted(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-supersecretvalue1234567890"
        try:
            out = security.redact({"note": "key is sk-ant-supersecretvalue1234567890 here"})
            self.assertNotIn("supersecretvalue", out["note"])
            self.assertIn("REDACTED", out["note"])
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_token_patterns_redacted(self):
        out = security.redact({"a": "ghp_abcdefghijklmnopqrstuvwxyz012345", "b": ["sk-abcdefghijklmnop1234"]})
        self.assertIn("REDACTED", out["a"])
        self.assertIn("REDACTED", out["b"][0])


class TestLiveServer(unittest.TestCase):
    """HTTP-edge behavior that needs a real socket."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.service = make_service(self.root)
        self.httpd = create_server(self.service, host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._tmp.cleanup()

    def test_localhost_status(self):
        r = httpx.get(f"{self.base}/status", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["healthy"])

    def test_health_probe(self):
        r = httpx.get(f"{self.base}/health", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_malformed_json(self):
        r = httpx.post(f"{self.base}/tasks", content=b"{not json", headers={"Content-Type": "application/json"}, timeout=5)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "bad-request")

    def test_end_to_end_create_run_approve(self):
        # Create → team run → approve, all over HTTP.
        c = httpx.Client(base_url=self.base, timeout=10)
        tid = c.post("/tasks", json={"title": "E2E", "objective": "End to end."}).json()["id"]
        tr = c.post(f"/tasks/{tid}/team-run", json={"mode": "review"}).json()
        self.assertEqual(tr["status"], "awaiting")
        approvals = c.get("/approvals").json()
        run_id = [a for a in approvals if a["status"] == "open"][0]["id"]
        res = c.post(f"/agent-runs/{run_id}/approve", json={"by": "human:e2e"})
        self.assertEqual(res.status_code, 200)
        task = c.get(f"/tasks/{tid}").json()
        self.assertEqual(task["status"], "completed")
        c.close()


if __name__ == "__main__":
    unittest.main()
