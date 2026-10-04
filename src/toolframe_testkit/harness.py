"""An owned temporary application driven only by installed CLI and HTTP APIs."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
import psutil
import tomlkit


class HarnessError(RuntimeError):
    """A CLI, operation, readiness or cleanup failure; output remains on disk."""


class HarnessTimeout(HarnessError):
    """A bounded application/command deadline expired."""


@dataclass(frozen=True)
class CLIResult:
    returncode: int
    stdout: str
    stderr: str
    output_file: Path

    def json(self) -> Any:
        return json.loads(self.stdout)


def _positive(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _find_link(value: str | Path) -> str:
    value = str(value)
    parsed = urlsplit(value)
    if parsed.scheme in {"https", "file"}:
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "find_links URLs must not contain credentials/query/fragment"
            )
        return value
    if parsed.scheme:
        raise ValueError("find_links must be local paths or https/file URLs")
    return str(Path(value).expanduser().resolve())


class _ProcessWatch:
    """Retain identities of owned descendants even if their parent later exits."""

    def __init__(self, process: subprocess.Popen):
        self.process = psutil.Process(process.pid)
        self._children: dict[tuple[int, float], psutil.Process] = {}
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def snapshot(self) -> list[psutil.Process]:
        try:
            children = self.process.children(recursive=True)
            with self._lock:
                for child in children:
                    try:
                        self._children[(child.pid, child.create_time())] = child
                    except psutil.NoSuchProcess:
                        pass
        except psutil.NoSuchProcess:
            pass
        with self._lock:
            return list(self._children.values())

    def _run(self):
        while not self._done.is_set():
            self.snapshot()
            self._done.wait(0.05)

    def stop(self) -> list[psutil.Process]:
        self._done.set()
        self._thread.join(timeout=2)
        return self.snapshot()


class ApplicationHarness:
    """Start a private application with an explicitly supplied installed command.

    The application home is always newly allocated and removed on close. Logs
    survive under a unique directory in output_directory. Put that directory in
    an ignored build location. No existing application home can be supplied.
    local_control=False makes loopback visitors use Core's normal admission.
    This harness does not simulate principals or bypass admission checks.
    """

    def __init__(
        self,
        core_command: str | Path | Sequence[str],
        *,
        output_directory: Path,
        port: int | None = None,
        find_links: Sequence[str | Path] = (),
        wheelhouse: Path | None = None,
        uv_command: str | Path | None = None,
        startup_timeout: float = 60,
        command_timeout: float = 120,
        shutdown_timeout: float = 45,
        poll_interval: float = 0.1,
        local_control: bool = False,
    ):
        command = (
            [str(core_command)]
            if isinstance(core_command, (str, Path))
            else list(core_command)
        )
        executable = shutil.which(command[0]) if command else None
        if not executable:
            raise ValueError("Supply an available installed Core command or binary")
        command[0] = str(Path(executable).resolve())
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.startup_timeout = _positive(startup_timeout, "startup_timeout")
        self.command_timeout = _positive(command_timeout, "command_timeout")
        self.shutdown_timeout = _positive(shutdown_timeout, "shutdown_timeout")
        self.poll_interval = _positive(poll_interval, "poll_interval")
        self.core_command = tuple(command)
        self.find_links = tuple(_find_link(link) for link in find_links)
        self.local_control = local_control
        if wheelhouse is not None:
            wheelhouse = wheelhouse.expanduser().resolve()
            if not wheelhouse.is_dir():
                raise ValueError("wheelhouse must be an existing directory")
            self.find_links += (str(wheelhouse),)
        if uv_command is not None and not shutil.which(str(uv_command)):
            raise ValueError("uv_command must be an available executable")

        root = Path(output_directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.output_directory = Path(tempfile.mkdtemp(prefix="toolframe-", dir=root))
        self._temporary = tempfile.TemporaryDirectory(prefix="toolframe-testkit-")
        self._root = Path(self._temporary.name).resolve()
        self._home = self._root / "application"
        self.home.mkdir(mode=0o700)
        if port is None:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        # Retain only platform essentials. Global Toolframe/Python/uv/DB settings,
        # proxy credentials and user config are never inherited by the child.
        self._environment = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
            if name in os.environ
        }
        private_home = self._root / "user"
        private_home.mkdir(mode=0o700)
        temporary = self._root / "tmp"
        temporary.mkdir(mode=0o700)
        self._environment.update(
            HOME=str(private_home),
            USERPROFILE=str(private_home),
            XDG_CONFIG_HOME=str(private_home / ".config"),
            XDG_CACHE_HOME=str(private_home / ".cache"),
            TMPDIR=str(temporary),
            TMP=str(temporary),
            TEMP=str(temporary),
            UV_CACHE_DIR=str(self._root / "uv-cache"),
            UV_NO_CONFIG="1",
            TOOLFRAME_RUNTIME_ROOT=str(self._root / "launcher-runtime"),
            PYTHONUNBUFFERED="1",
        )
        if uv_command is not None:
            self._environment["TOOLFRAME_UV"] = str(Path(shutil.which(str(uv_command))))
        if wheelhouse is not None:
            self._environment["TOOLFRAME_WHEELHOUSE"] = str(wheelhouse)
        self._client = httpx.Client(base_url=self.base_url, timeout=5, trust_env=False)
        self._process: subprocess.Popen | None = None
        self._serve_watch: _ProcessWatch | None = None
        self._reader_threads: list[threading.Thread] = []
        self._initialized = False
        self._closed = False
        self._log_counter = 0

    @property
    def home(self) -> Path:
        return self._home

    def __enter__(self) -> ApplicationHarness:
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def _ensure_open(self):
        if self._closed:
            raise HarnessError("ApplicationHarness has been closed")

    def _token(self) -> str:
        path = self.home / ".toolframe" / "management.token"
        if path.is_symlink() or not path.resolve().is_relative_to(self.home):
            raise HarnessError("Management token must stay inside the owned home")
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            raise HarnessError(
                "Owned application's management token is unavailable"
            ) from None
        if not token:
            raise HarnessError("Owned application's management token is empty")
        return token

    def _redact(self, text: str) -> str:
        try:
            text = text.replace(self._token(), "[REDACTED]")
        except HarnessError:
            pass
        text = re.sub(r"(?i)(Bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)
        return re.sub(r"(?i)([?&]token=)[^&\s\"'<>]+", r"\1[REDACTED]", text)

    def _argv(self, args: Sequence[str]) -> list[str]:
        return [*self.core_command, "--home", str(self.home), "--json", *args]

    def _log(self, label: str) -> Path:
        self._log_counter += 1
        return self.output_directory / f"{self._log_counter:03d}-{label}.log"

    @staticmethod
    def _cleanup_descendants(children: list[psutil.Process]):
        # psutil Process identity includes creation time, preventing signals from
        # being sent to a reused PID. Only observed descendants of our child count.
        alive = [child for child in children if child.is_running()]
        for child in reversed(alive):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=2)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=2)
        if alive:
            raise HarnessError("Owned child processes did not terminate")

    def _terminate(self, process: subprocess.Popen, watch: _ProcessWatch):
        children = watch.snapshot()
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    # Recheck for children spawned during graceful shutdown.
                    children += watch.snapshot()
                    process.kill()
                    process.wait(timeout=5)
        finally:
            children += watch.stop()
            self._cleanup_descendants(list(set(children)))

    def cli(
        self, *args: str, timeout: float | None = None, check: bool = True
    ) -> CLIResult:
        """Run a CLI action with a deadline and sanitized retained output.

        Avoid credential-producing actions: their output is intentionally redacted.
        Args are passed as an argv list; no shell is invoked.
        """
        self._ensure_open()
        if any(arg == "--home" or arg.startswith("--home=") for arg in args):
            raise ValueError(
                "CLI home cannot be overridden outside the owned application"
            )
        timeout = _positive(
            self.command_timeout if timeout is None else timeout, "timeout"
        )
        output = self._log("cli")
        process = subprocess.Popen(
            self._argv(args),
            cwd=self.home,
            env=self._environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        watch = _ProcessWatch(process)
        expired = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            expired = True
            self._terminate(process, watch)
            stdout, stderr = process.communicate(timeout=5)
        else:
            self._terminate(process, watch)
        clean_stdout = self._redact(stdout.decode("utf-8", errors="replace"))
        clean_stderr = self._redact(stderr.decode("utf-8", errors="replace"))
        output.write_text(
            clean_stdout + "\n[stderr]\n" + clean_stderr, encoding="utf-8"
        )
        result = CLIResult(process.returncode, clean_stdout, clean_stderr, output)
        if expired:
            raise HarnessTimeout(f"CLI deadline expired; output: {output}")
        if check and result.returncode:
            raise HarnessError(f"CLI exited {result.returncode}; output: {output}")
        return result

    def initialize(self) -> ApplicationHarness:
        self._ensure_open()
        if self._initialized:
            return self
        self.cli("init", "--host", "127.0.0.1", "--port", str(self.port))
        # This is the public composition file in our newly created home. No
        # existing/user application composition is accepted by this harness.
        path = self.home / "toolframe.toml"
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
        document.setdefault("packages", {})["find_links"] = list(self.find_links)
        document.setdefault("access", {})["local_control"] = self.local_control
        path.write_text(tomlkit.dumps(document), encoding="utf-8")
        self._initialized = True
        return self

    def _capture(self, stream, output: Path):
        with output.open("w", encoding="utf-8") as handle:
            for line in iter(stream.readline, b""):
                handle.write(self._redact(line.decode("utf-8", errors="replace")))
                handle.flush()
        stream.close()

    def start(self) -> ApplicationHarness:
        self._ensure_open()
        if self._process is not None:
            raise HarnessError("Core is already started; call stop() before restarting")
        self.initialize()
        self._process = subprocess.Popen(
            self._argv(("serve",)),
            cwd=self.home,
            env=self._environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._serve_watch = _ProcessWatch(self._process)
        for label, stream in (
            ("serve-stdout", self._process.stdout),
            ("serve-stderr", self._process.stderr),
        ):
            reader = threading.Thread(
                target=self._capture, args=(stream, self._log(label)), daemon=True
            )
            reader.start()
            self._reader_threads.append(reader)
        deadline = time.monotonic() + self.startup_timeout
        try:
            while time.monotonic() < deadline:
                self._alive()
                try:
                    response = self.request(
                        "GET",
                        "/_toolframe/v1/readiness",
                        management=True,
                        timeout=min(5, max(0.01, deadline - time.monotonic())),
                    )
                    if response.status_code == 200 and response.json().get("ready"):
                        return self
                except httpx.HTTPError, HarnessError:
                    pass
                time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
            raise HarnessTimeout(
                f"Core readiness deadline expired; output: {self.output_directory}"
            )
        except BaseException:
            self.stop()
            raise

    def _alive(self):
        if self._process is None or self._process.poll() is not None:
            raise HarnessError(f"Core is not running; output: {self.output_directory}")

    def stop(self):
        """Gracefully stop this Core, then reap only its observed descendants."""
        if self._process is not None:
            try:
                self._terminate(self._process, self._serve_watch)
            finally:
                self._process = None
                self._serve_watch = None
                for reader in self._reader_threads:
                    reader.join(timeout=5)
                self._reader_threads.clear()

    def close(self):
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._client.close()
            self._temporary.cleanup()
            self._closed = True

    def request(
        self, method: str, path: str, *, management: bool = False, **kwargs
    ) -> httpx.Response:
        """Send an HTTP request to this application, optionally as its operator."""
        self._ensure_open()
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc:
            raise ValueError("Request path must be relative to this application")
        headers = httpx.Headers(kwargs.pop("headers", {}))
        if management:
            headers["authorization"] = f"Bearer {self._token()}"
        # Explicitly forbid following redirects which might transmit the token
        # to an external target; consumers may inspect Location themselves.
        if kwargs.pop("follow_redirects", False):
            raise ValueError("Harness requests must not follow redirects")
        return self._client.request(method, path, headers=headers, **kwargs)

    def status(self) -> dict:
        response = self.request("GET", "/_toolframe/v1/status", management=True)
        response.raise_for_status()
        return response.json()

    def _poll_response(self, path: str, deadline: float) -> dict | None:
        try:
            response = self.request(
                "GET",
                path,
                management=True,
                timeout=min(5, max(0.01, deadline - time.monotonic())),
            )
        except httpx.TransportError:
            return None
        if response.status_code >= 500 or response.status_code == 429:
            return None
        response.raise_for_status()
        return response.json()

    def wait_for_plugin(
        self, plugin_id: str, *, healthy: bool = True, timeout: float | None = None
    ) -> dict:
        deadline = time.monotonic() + _positive(
            self.command_timeout if timeout is None else timeout, "timeout"
        )
        while time.monotonic() < deadline:
            self._alive()
            report = self._poll_response("/_toolframe/v1/status", deadline)
            for row in (report or {}).get("plugins", []):
                if row["id"] == plugin_id and bool(row.get("healthy")) == healthy:
                    return row
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise HarnessTimeout(
            f"Plugin health deadline expired; output: {self.output_directory}"
        )

    def install_plugin(
        self, source: str | Path, *, plugin_id: str | None = None, enable: bool = True
    ) -> CLIResult:
        if isinstance(source, Path):
            normalized = str(source.expanduser().resolve())
        else:
            prefix, _, value = source.partition(":")
            if prefix in {"wheel", "path"}:
                normalized = prefix + ":" + str(Path(value).expanduser().resolve())
            elif source.startswith(("pypi:", "git+", "https://")):
                normalized = source
            else:
                normalized = str(Path(source).expanduser().resolve())
        args = ["plugin", "add", normalized]
        if plugin_id is not None:
            args += ["--id", plugin_id]
        if enable:
            args.append("--enable")
        return self.cli(*args)

    def plugin_action(self, action: str, plugin_id: str, *args: str) -> CLIResult:
        if action not in {
            "validate",
            "update",
            "reload",
            "enable",
            "disable",
            "remove",
            "stop",
            "status",
            "logs",
        }:
            raise ValueError("Unknown public plugin action")
        return self.cli("plugin", action, plugin_id, *args)

    def operation(
        self,
        action: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        """Submit a public management operation and await its terminal result."""
        deadline = time.monotonic() + _positive(
            self.command_timeout if timeout is None else timeout, "timeout"
        )
        response = self.request(
            "POST",
            "/_toolframe/v1/operations",
            management=True,
            json={"action": action, "params": dict(params or {})},
            timeout=min(5, max(0.01, deadline - time.monotonic())),
        )
        response.raise_for_status()
        identifier = response.json()["id"]
        while time.monotonic() < deadline:
            self._alive()
            result = self._poll_response(
                f"/_toolframe/v1/operations/{quote(identifier, safe='')}", deadline
            )
            if result and result["status"] not in {"queued", "running", "cancelling"}:
                if result["status"] != "succeeded":
                    raise HarnessError(
                        f"Operation {identifier} {result['status']}; "
                        f"output: {self.output_directory}"
                    )
                return result
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise HarnessTimeout(
            f"Operation deadline expired; output: {self.output_directory}"
        )

    def service_call(
        self,
        owner: str,
        service: str,
        method: str,
        params: Any,
        *,
        entrance: Literal["visitor", "public"] = "visitor",
        management: bool = False,
    ) -> httpx.Response:
        if entrance not in {"visitor", "public"}:
            raise ValueError("entrance must be visitor or public")
        prefix = "/api" if entrance == "public" else "/_toolframe/v1/services"
        path = "/".join(quote(value, safe="") for value in (owner, service, method))
        return self.request(
            "POST", f"{prefix}/{path}", management=management, json=params
        )
