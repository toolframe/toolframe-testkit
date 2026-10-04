"""Process-boundary tests using a fake command; no Core authority evidence."""

import sys
from pathlib import Path

import psutil
import pytest

from toolframe_testkit import ApplicationHarness, HarnessError, HarnessTimeout

FAKE_COMMAND = r"""
import json, pathlib, subprocess, sys, time
home = pathlib.Path(sys.argv[sys.argv.index("--home") + 1])
command = sys.argv[sys.argv.index("--json") + 1]
if command == "init":
    home.mkdir(exist_ok=True)
    (home / "toolframe.toml").write_text(
        '[application]\nid="fake"\n[packages]\nfind_links=[]\n'
        '[access]\nlocal_control=true\n'
    )
    (home / ".toolframe").mkdir()
    (home / ".toolframe/management.token").write_text("private-owned-token")
    print(json.dumps({"initialized": True}))
elif command == "status":
    print(json.dumps({"token": "private-owned-token", "ready": True}))
    print("Bearer another-secret", file=sys.stderr)
elif command == "plugin":
    print(json.dumps({"source": sys.argv[sys.argv.index("add") + 1]}))
elif command == "wait":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    print("begin child=" + str(child.pid), flush=True)
    time.sleep(30)
elif command == "serve":
    if "--orphan" in sys.argv:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        print("early child=" + str(child.pid), flush=True)
        time.sleep(0.2)
    print("early failure", flush=True)
    sys.exit(7)
"""


def harness(tmp_path, **kwargs):
    return ApplicationHarness(
        [sys.executable, "-c", FAKE_COMMAND],
        output_directory=tmp_path / "build",
        **kwargs,
    )


def test_cli_output_redacts_owned_token_and_teardown_keeps_logs(tmp_path):
    app = harness(tmp_path)
    home = app.home
    app.initialize()
    result = app.cli("status")
    assert result.json() == {"token": "[REDACTED]", "ready": True}
    assert "another-secret" not in result.stderr
    logs = app.output_directory
    app.close()
    assert not home.exists()
    assert logs.is_dir()
    assert all("private-owned-token" not in p.read_text() for p in logs.glob("*.log"))


def test_cli_timeout_is_bounded_and_retains_child_output(tmp_path):
    app = harness(tmp_path)
    unrelated = psutil.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        app.initialize()
        with pytest.raises(HarnessTimeout):
            app.cli("wait", timeout=0.2)
        output = next(
            p.read_text()
            for p in app.output_directory.glob("*.log")
            if "begin child=" in p.read_text()
        )
        child_pid = int(output.split("child=")[1].split()[0])
        assert not psutil.pid_exists(child_pid)
        assert unrelated.is_running()
    finally:
        app.close()
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_failed_start_cleans_owned_home_and_reports_log_location(tmp_path):
    app = harness(tmp_path, startup_timeout=1)
    home = app.home
    with pytest.raises(HarnessError, match="output"):
        with app:
            pytest.fail("Failed command must never enter the context")
    assert not home.exists()
    assert list(app.output_directory.glob("*.log"))


def test_missing_core_command_fails_before_starting_application(tmp_path):
    with pytest.raises(ValueError, match="installed"):
        ApplicationHarness(Path("/missing/core"), output_directory=tmp_path)


def test_owned_home_cannot_be_replaced_and_requests_cannot_leave_it(tmp_path):
    with pytest.raises(ValueError, match="timeout"):
        harness(tmp_path, startup_timeout=0)
    app = harness(tmp_path)
    try:
        with pytest.raises(AttributeError):
            app.home = tmp_path
        with pytest.raises(ValueError, match="relative"):
            app.request("GET", "https://example.com")
        with pytest.raises(ValueError, match="redirect"):
            app.request("GET", "/", follow_redirects=True)
        with pytest.raises(ValueError, match="positive"):
            app.cli("status", timeout=0)
        with pytest.raises(ValueError, match="home"):
            app.cli("--home", str(tmp_path), "init")
    finally:
        app.close()


def test_abrupt_core_exit_reaps_observed_owned_descendants(tmp_path):
    app = ApplicationHarness(
        [sys.executable, "-c", FAKE_COMMAND, "--orphan"],
        output_directory=tmp_path / "build",
        startup_timeout=1,
    )
    with pytest.raises(HarnessError, match="output"):
        with app:
            pytest.fail("The fake Core must fail startup")
    output = next(
        p.read_text()
        for p in app.output_directory.glob("*.log")
        if "early child=" in p.read_text()
    )
    child_pid = int(output.split("child=")[1].split()[0])
    assert not psutil.pid_exists(child_pid)


def test_install_plugin_resolves_wheel_in_callers_directory(tmp_path, monkeypatch):
    app = harness(tmp_path)
    wheel = tmp_path / "fixture.whl"
    wheel.write_text("process-boundary fixture")
    monkeypatch.chdir(tmp_path)
    try:
        app.initialize()
        result = app.install_plugin(Path("fixture.whl"), plugin_id="fixture")
        assert result.json()["source"] == str(wheel)
    finally:
        app.close()
