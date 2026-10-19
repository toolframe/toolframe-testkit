# Installed application harness

`ApplicationHarness` runs an explicitly selected installed Core command against
a fresh private home. It composes the home with the public `init` and `serve`
CLI actions, waits for the public readiness endpoint, and communicates through
public status, operation, and service HTTP endpoints. It never imports Core
internals or changes an existing user application.

```python
from pathlib import Path

from toolframe_testkit import ApplicationHarness

with ApplicationHarness(
    "/path/to/toolframe-core",
    output_directory=Path("build/installed-core"),
    find_links=[Path("build/wheelhouse")],
) as app:
    app.install_plugin("build/wheelhouse/my_plugin-0.1.0-py3-none-any.whl")
    app.wait_for_plugin("my-plugin")
    response = app.service_call(
        "my-plugin", "my.records", "list", {}, entrance="public"
    )
    assert response.status_code == 200
```

The command may be a string or `Path` naming an executable, or a sequence of
argv strings. A string is an executable path; it is not shell-parsed. `port` is
optional. Without one, the harness reserves a dynamically chosen loopback port
before starting Core. That reservation has the normal small race with another
process binding the port, so a test should use a retry or an explicitly managed
port when its environment needs that guarantee.

The harness always creates a new home and removes it on `close()`. Logs and CLI
output remain in a unique directory below the caller's `output_directory`; use
an ignored `build/` path. `find_links` accepts local paths and credentialless
`https` or `file` URLs. `wheelhouse` is a convenience local directory. An
optional `uv_command` can select the package installer used by the installed
Core. Child processes receive a scrubbed environment with private HOME,
configuration, cache, runtime, and temporary directories.

## Lifecycle and CLI

`initialize()` runs `init` and writes the selected package links and
`local_control` value to the new composition. `start()` calls `initialize()`,
starts `serve`, and polls readiness with a bounded deadline. `stop()` terminates
the owned process tree and `close()` also removes the temporary home. The context
manager calls `start()` and `close()` for deterministic cleanup.

`cli(*args, timeout=None, check=True)` passes an argv list to Core. It returns a
`CLIResult` with `returncode`, sanitized `stdout`, sanitized `stderr`, and an
`output_file`; `CLIResult.json()` parses stdout. Failed commands raise
`HarnessError` when `check=True`; a deadline raises `HarnessTimeout` while
retaining the output file.

`install_plugin(source, plugin_id=None, enable=True)` uses the public plugin-add
action. Local source paths resolve relative to the caller's directory. `cli`
rejects any `--home` override. `plugin_action` supports Core's public
`validate`, `update`, `reload`,
`enable`, `disable`, `remove`, `stop`, `status`, and `logs` actions. `status()`
reads the management status endpoint. `wait_for_plugin()` polls status until the
selected plugin has the requested health state.

## HTTP and authority checks

`request(method, path, management=False, **kwargs)` accepts only a path relative
to this application's loopback base URL. It never follows redirects, so a test
can inspect an unexpected `Location` without sending a management credential to
another host. `management=True` reads the token only from this harness-owned
home and sends it as a bearer header.

`operation(action, params=None, timeout=None)` submits a public management
operation and polls until a terminal succeeded result, returning its JSON
dictionary. Health and operation GET polling retries transport errors, HTTP 5xx,
and 429 within the configured deadline. Operation submissions are not retried
when the outcome is unknown. `service_call(owner, service, method, params, entrance=...)`
supports `visitor` and `public` entrances and returns the raw `httpx.Response`;
the caller asserts the expected status and error body. This makes denied calls
part of the test contract rather than hiding them in helper behavior.

## Pytest integration

The package has no automatic `pytest11` entry point. Load the plugin explicitly
in `conftest.py` or with `pytest -p toolframe_testkit.pytest_plugin`:

```python
pytest_plugins = ["toolframe_testkit.pytest_plugin"]
```

`toolframe_definition_checker` exposes `check_definition`. The
`toolframe_harness_factory` fixture creates harnesses with keyword overrides and
closes every harness at test teardown. `toolframe_application` creates and
starts one harness, skipping when no Core command is supplied. The command-line
options are `--toolframe-core-command`, repeatable `--toolframe-find-links`, and
`--toolframe-output-directory` (default `build/toolframe-testkit`).

With the default `local_control=False`, visitor requests go through Core's real
admission checks. `management=True` supplies only the harness-owned management
identity for an authority test. The harness does not simulate principals or
bypass authorization. `local_control=True` is available when a test explicitly
needs Core's local-control mode.

Teardown tracks observed descendants by PID and process creation time, then
terminates only that owned tree. It does not kill processes by global PID/name.
The harness redacts its own management token and common bearer or `token=` query
forms from retained output. It cannot guarantee redaction of arbitrary secrets
that a plugin writes in its own logs.
