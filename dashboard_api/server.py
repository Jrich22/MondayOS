"""
Stdlib HTTP server for the dashboard API.

A thin socket shell around `router.route`. No web framework — MondayOS keeps a
deliberately lean dependency set, and a localhost single-user bridge doesn't
warrant one. Uses `ThreadingHTTPServer` so a long-lived SSE stream doesn't block
other requests. Binds to 127.0.0.1 by default; never 0.0.0.0 unless an operator
explicitly overrides the host.
"""

from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from monday import Monday, MondayConfig
from monday.provider_env import choose, load_env_file
from monday.provider_env import provider_config as resolve_provider_config

from . import errors, security
from .router import route
from .service import DashboardService


def build_service(root: Path | None = None, provider: str | None = None) -> DashboardService:
    """
    Construct a DashboardService over a real Monday instance.

    Resolves the AI provider from the environment. Without this the workspace
    ran with `provider_config=None` — it could hold a conversation but never
    answer one, which looked like a missing feature and was a missing line of
    configuration.

    The key never passes through here: `provider_config` carries the provider
    name and model only, and each provider reads its own variable directly, so
    the secret cannot reach a repr or a log line by way of MondayOS's config.
    """
    project_root = root or Path(os.environ.get("MONDAYOS_ROOT", ".")).resolve()
    # A project-local .env, if present, filling only variables the shell has not
    # already set. An exported value is a deliberate act; a file is a default.
    load_env_file(project_root / ".env")

    prov = provider or os.environ.get("MONDAYOS_DASHBOARD_PROVIDER", "fake")
    monday = Monday(
        MondayConfig(
            project_root=project_root,
            require_human_approval=True,
            provider_config=resolve_provider_config(),
        )
    )
    return DashboardService(
        monday,
        provider=prov,
        write_log=project_root / "logs" / "dashboard_api.jsonl",
    )


def make_handler(service: DashboardService):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Quiet by default; route-level logging handles writes.
        def log_message(self, *args):  # noqa: N802
            pass

        def _send(self, status: int, headers: dict[str, str], body):
            payload = b"" if body is None else json.dumps(body).encode("utf-8")
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def _origin(self) -> str | None:
            return self.headers.get("Origin")

        def do_OPTIONS(self):  # noqa: N802
            status, headers, body = route(service, "OPTIONS", self.path, origin=self._origin())
            self._send(status, headers, body)

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.rstrip("/") == "/events":
                self._sse()
                return
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            status, headers, body = route(
                service, "GET", parsed.path, query=query, origin=self._origin()
            )
            self._send(status, headers, body)

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            origin = self._origin()
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            body: dict = {}
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                    if not isinstance(body, dict):
                        raise ValueError("body must be a JSON object")
                except (ValueError, UnicodeDecodeError):
                    headers = security.cors_headers(origin)
                    self._send(
                        400, headers, errors.error(errors.BAD_REQUEST, "Malformed JSON body.")
                    )
                    return
            # Streaming is a transport concern, so it bypasses `route`, which is
            # pure by design. The generator itself lives on the service and is
            # unit-tested without a socket.
            match = re.match(r"^/workspace/conversations/([^/]+)/stream/?$", parsed.path)
            if match:
                self._stream(match.group(1), body, origin)
                return

            status, headers, resp = route(service, "POST", parsed.path, origin=origin, body=body)
            self._send(status, headers, resp)

        def _stream(self, conversation_id: str, body: dict, origin: str | None):
            """
            Stream one turn as SSE over a POST.

            POST rather than EventSource because the request carries a message
            body, and because the client needs AbortController to stop it —
            aborting closes this connection, which closes the generator, which is
            how the workspace service learns to persist the partial answer.
            """
            if not security.is_allowed_origin(origin):
                self._send(
                    403,
                    security.cors_headers(origin),
                    errors.error(errors.FORBIDDEN_ORIGIN, "Origin not allowed."),
                )
                return

            self.send_response(200)
            for k, v in security.cors_headers(origin).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # A finite stream with no Content-Length: the connection close IS the
            # terminator. Advertising keep-alive here would leave the client
            # waiting for frames that will never come after the final event.
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            stream = service.workspace_stream(conversation_id, body)
            try:
                for event in stream:
                    payload = json.dumps(security.redact(event))
                    kind = event.get("type", "message")
                    frame = f"event: {kind}\ndata: {payload}\n\n"
                    self.wfile.write(frame.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                # The client aborted — the Stop button. Closing the generator is
                # what persists the partial answer, so it must happen even though
                # nothing can be written back.
                pass
            finally:
                stream.close()

        def _sse(self):
            """Server-Sent Events: heartbeat + revision changes. The dashboard
            refetches snapshots whenever the revision advances; polling
            /revision is the documented fallback if EventSource is unavailable."""
            origin = self._origin()
            if not security.is_allowed_origin(origin):
                self._send(
                    403,
                    security.cors_headers(origin),
                    errors.error(errors.FORBIDDEN_ORIGIN, "Origin not allowed."),
                )
                return
            self.send_response(200)
            for k, v in security.cors_headers(origin).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last = -1
            try:
                for _ in range(100_000):  # bounded loop; client close breaks it
                    if service.revision != last:
                        last = service.revision
                        self.wfile.write(f"event: revision\ndata: {last}\n\n".encode())
                        self.wfile.flush()
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


def create_server(
    service: DashboardService, host: str = security.DEFAULT_HOST, port: int = security.DEFAULT_PORT
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(service))


def main() -> None:
    host = os.environ.get("MONDAYOS_API_HOST", security.DEFAULT_HOST)
    port = int(os.environ.get("MONDAYOS_API_PORT", security.DEFAULT_PORT))
    service = build_service()
    httpd = create_server(service, host, port)
    print(f"MondayOS dashboard API on http://{host}:{port} (team provider={service.provider})")
    print(f"AI Workspace provider: {choose().describe()}")
    print(f"CORS allowlist: {sorted(security.allowed_origins())}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
