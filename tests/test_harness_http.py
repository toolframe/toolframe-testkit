"""Retry/deadline tests at a fake HTTP boundary; no Core authority assertions."""

import sys

import pytest

from toolframe_testkit import ApplicationHarness, HarnessTimeout

HTTP_COMMAND = r"""
import http.server, json, pathlib, socket, sys, tomllib
home = pathlib.Path(sys.argv[sys.argv.index("--home") + 1])
command = sys.argv[sys.argv.index("--json") + 1]
if command == "init":
    port = int(sys.argv[sys.argv.index("--port") + 1])
    (home / "toolframe.toml").write_text('[application]\nport=' + str(port))
    (home / ".toolframe").mkdir()
    (home / ".toolframe/management.token").write_text("owned-http-token")
    print('{}')
else:
    port = tomllib.loads((home / "toolframe.toml").read_text())["application"]["port"]
    class Handler(http.server.BaseHTTPRequestHandler):
        status_calls = 0
        operation_calls = 0
        def log_message(self, *_):
            pass
        def emit(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            if self.path.endswith("readiness"):
                self.emit(200, {"ready": True})
            elif self.path.endswith("status"):
                Handler.status_calls += 1
                if Handler.status_calls == 1:
                    self.emit(503, {"error": "temporary"})
                elif Handler.status_calls == 2:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                else:
                    self.emit(200, {"plugins": [{"id": "fixture", "healthy": True}]})
            elif "/operations/" in self.path:
                Handler.operation_calls += 1
                if Handler.operation_calls == 1:
                    self.emit(503, {"error": "temporary"})
                else:
                    self.emit(200, {"id": "test-operation", "status": "succeeded",
                                    "result": {"retained": True}})
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.emit(202, {"id": "test-operation", "status": "queued"})
    http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


@pytest.fixture
def fake_http_application(tmp_path):
    with ApplicationHarness(
        [sys.executable, "-c", HTTP_COMMAND],
        output_directory=tmp_path / "build",
        startup_timeout=2,
        poll_interval=0.01,
    ) as app:
        yield app


def test_plugin_poll_recovers_from_temporary_status_and_transport_failures(
    fake_http_application,
):
    row = fake_http_application.wait_for_plugin("fixture", timeout=2)
    assert row == {"id": "fixture", "healthy": True}


def test_operation_poll_recovers_from_temporary_status_failure(fake_http_application):
    result = fake_http_application.operation("reload", {"id": "fixture"}, timeout=2)
    assert result["result"] == {"retained": True}


def test_missing_plugin_poll_has_a_bounded_deadline(fake_http_application):
    with pytest.raises(HarnessTimeout, match="health deadline"):
        fake_http_application.wait_for_plugin("missing", timeout=0.1)
