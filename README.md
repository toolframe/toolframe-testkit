# Toolframe testkit

Toolframe testkit is a Python library for checking a plugin's public SDK contract
and exercising an installed Toolframe Core application through its public CLI and
HTTP APIs. It is a developer test dependency, not a Core runtime plugin. Version
0.1.1 supports Python 3.14 or newer and pins `toolframe-sdk==0.3.0`.

The package is Apache-2.0 licensed. It does not install Core, choose a global
application, or load a pytest plugin automatically.

## Quick start

Install the checker and opt-in pytest integration from PyPI:

```sh
python -m pip install 'toolframe-testkit[pytest]==0.1.1'
```

The SDK is installed as a dependency. See
[development and verification](docs/development.md) for source builds and local
wheelhouse checks.

Check a public SDK definition in a unit test:

```python
from toolframe_testkit import check_definition
from my_plugin.definition import plugin


def test_plugin_contract():
    check_definition(plugin).assert_valid()
```

For installed Core tests, explicitly load the pytest plugin in `conftest.py`:

```python
pytest_plugins = ["toolframe_testkit.pytest_plugin"]
```

Then supply an installed Core command when running the test. The plugin creates a
new private application home, starts Core on a loopback port, and removes the home
after the test. A minimal `tests/test_application.py` can check readiness without
installing a plugin:

```python
def test_application(toolframe_application):
    assert toolframe_application.status()["ready"] is True
```

Child output is retained below the selected ignored build directory:

```sh
pytest tests/test_application.py \
  --toolframe-core-command "$(command -v toolframe-core)" \
  --toolframe-output-directory build/installed-core
```

See [contract checks](docs/contracts.md) for definition and example validation,
[the application harness](docs/harness.md) for installed Core testing, and
[development and verification](docs/development.md) for wheel builds and the
real-Core fixture run. The complete standalone scenario is in
[examples/installed_application.py](examples/installed_application.py).

## Support limits

The harness is deliberately explicit: callers choose the installed Core command,
wheel links, and output directory. It uses public Core CLI and HTTP endpoints and
does not import Core internals or edit an existing user application. The default
`local_control=False` exercises Core's normal admission checks; setting it to
`True` is available only when a test specifically needs local control behavior.

The pytest integration is opt-in through `pytest_plugins` or `pytest -p`; there is
no `pytest11` auto-entry point. `check_definition` validates the SDK model, target
references, resource scopes, packaged UTF-8 documents, and supplied concrete
examples. Example checks do not prove general JSON Schema implication or Core
authorization behavior. MCP resource schemas may intentionally narrow a service
schema.

The installed-Core harness covers public readiness, status, plugin lifecycle,
service admission, authorization, schema rejection, and retained data. Browser
interaction, provider-specific protocols, and external service provisioning need
separate acceptance checks. The harness runs local processes; it does not manage
remote deployments. It redacts its owned management token and common bearer/token
query forms, but cannot guarantee redaction of arbitrary plugin output.

For release validation and Trusted Publisher setup, see [releasing](docs/releasing.md).
