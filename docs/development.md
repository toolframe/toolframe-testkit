# Development and verification

The testkit repository is independently buildable. Runtime dependencies are
`toolframe-sdk==0.3.0`, `httpx`, `tomlkit`, `psutil`, `pydantic`, and `jsonschema`;
pytest is available
through the `[pytest]` extra. Python 3.14 or newer is required. No release is
published yet, so consumer-style installation uses a local wheelhouse.

## Local environment

Build the SDK and sibling wheels first, then create a local environment and
install the package with the development tools. The install must use the
wheelhouse so uv does not select an unrelated source checkout:

```sh
mkdir -p .local/wheelhouse
```

When testing sibling development checkouts, build their wheels into a local,
ignored wheelhouse. These commands are for a sibling checkout layout and are
deliberately explicit about `--no-sources`:

```sh
uv build --no-sources --wheel --out-dir .local/wheelhouse \
  ../toolframe-sdk/.worktrees/plugin-services
uv build --no-sources --wheel --out-dir .local/wheelhouse \
  ../fastapi-cordis
uv build --no-sources --wheel --out-dir .local/wheelhouse \
  ../toolframe-core/.worktrees/plugin-services
uv build --no-sources --wheel --out-dir .local/wheelhouse \
  ../toolframe-storage-sqlite
uv build --no-sources --wheel --out-dir .local/wheelhouse \
  tests/assets/service-plugin
```

The sibling paths above are local development commands. They are not package
metadata and should not be copied into consumer projects. After building the
SDK wheel, install the development environment:

```sh
uv venv .venv
uv pip install --python .venv/bin/python --find-links .local/wheelhouse \
  -e '.[pytest]' pytest ruff build hatchling
```

## Checks

Run the package checks from this repository:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest -q
uv build --no-sources
```

The normal unit suite uses a fake command for process-boundary behavior and does
not start Core. The fixture plugin under
`tests/assets/service-plugin` is a separately packaged business plugin named
`toolframe-testkit-fixture` (fixture id `fixture-records`). It uses the public
SDK and SQLite storage contract and is a test asset, not part of the testkit
production wheel.

## Installed Core run

The installed integration test is opt-in and requires explicitly built storage
and fixture wheels. Set their paths and pass the installed Core command and
wheel links:

Install the matching Core command into the same environment after its Core,
Cordis, and SDK wheels are in the wheelhouse:

```sh
uv pip install --python .venv/bin/python --find-links .local/wheelhouse \
  toolframe-core==0.6.2
```

```sh
export TOOLFRAME_TESTKIT_STORAGE_WHEEL="$PWD/.local/wheelhouse/toolframe_storage_sqlite-0.3.0-py3-none-any.whl"
export TOOLFRAME_TESTKIT_FIXTURE_WHEEL="$PWD/.local/wheelhouse/toolframe_testkit_fixture-0.1.0-py3-none-any.whl"
.venv/bin/python -m pytest tests/test_installed_core.py \
  --toolframe-core-command "$PWD/.venv/bin/toolframe-core" \
  --toolframe-find-links "$PWD/.local/wheelhouse" \
  --toolframe-output-directory build/installed-core
```

The scenario checks public listing, anonymous privileged-call denial
(`401 authentication_required`), management authorization (`200`), internal-call
denial (`403 forbidden`), public write denial (`403 forbidden`), schema rejection
(`400 handler_failed` at the Core 0.6 boundary), durable append, reload, disable,
enable, and record retention. These are scenario assertions; they are not a
general support claim for every Core environment.

The same scenario can run outside pytest with
`examples/installed_application.py`, which accepts `--core-command`,
`--wheelhouse`, `--storage-wheel`, `--fixture-wheel`, and `--output`.

## Clean-install smoke

Build a wheel with `uv build --no-sources`. The repository's
`scripts/smoke_wheel.py` command accepts the built testkit wheel, fixture wheel,
find-links directory, and output directory:

```sh
.venv/bin/python scripts/smoke_wheel.py \
  --wheel dist/toolframe_testkit-0.1.2-py3-none-any.whl \
  --fixture-wheel .local/wheelhouse/toolframe_testkit_fixture-0.1.0-py3-none-any.whl \
  --find-links .local/wheelhouse \
  --output build/wheel-smoke
```

It creates a fresh virtual environment, checks metadata and imports, and runs
strict contract checks without loading pytest or Core. The installed Core run
requires the separate Core and plugin wheels described above.

## Environment boundaries

Support evidence so far is limited to a macOS ARM64 environment with an
installed Python Core command. Dashboard and MCP provider flows, federated or
password-grant authentication, binary launchers, PostgreSQL, Windows, and
Linux execution remain untested. Keep such checks as explicit
environment-specific tests rather than turning this result into a broader
support claim.
