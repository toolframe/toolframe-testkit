"""Opt-in checks against a real, separately installed Toolframe Core.

The test deliberately obtains the application fixture only after checking the
wheel environment.  Running the normal test suite therefore never starts Core;
an installed run must opt in with the testkit pytest plugin's Core options and
the two explicitly built plugin wheels.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.installed_core

_STORAGE_WHEEL_ENV = "TOOLFRAME_TESTKIT_STORAGE_WHEEL"
_FIXTURE_WHEEL_ENV = "TOOLFRAME_TESTKIT_FIXTURE_WHEEL"
_STORAGE_PLUGIN = "sql-sqlite"
_FIXTURE_PLUGIN = "fixture-records"
_SERVICE = "fixture-records.records"


def _configured_wheels() -> tuple[Path, Path]:
    """Return explicitly selected wheels, or skip this opt-in test.

    Environment variables are used instead of building a wheel from a source
    checkout during the test.  That keeps the installed-Core evidence tied to
    the artifacts a caller chose and avoids accidentally importing a sibling
    development checkout.
    """

    values = {
        name: os.environ.get(name, "").strip()
        for name in (_STORAGE_WHEEL_ENV, _FIXTURE_WHEEL_ENV)
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(
            "installed Core test is opt-in; set " + ", ".join(missing) + " "
            "to explicitly built wheels"
        )

    paths = {name: Path(value).expanduser() for name, value in values.items()}
    absent = [str(path) for path in paths.values() if not path.is_file()]
    if absent:
        pytest.fail("Configured testkit wheel does not exist: " + ", ".join(absent))
    return paths[_STORAGE_WHEEL_ENV], paths[_FIXTURE_WHEEL_ENV]


def _assert_operation(result, *, plugin_id: str | None = None) -> dict:
    """Assert a CLI operation completed and return its public JSON payload."""

    payload = result.json()
    assert payload.get("status") == "succeeded", payload
    if plugin_id is not None:
        assert payload.get("result", {}).get("plugin_id") == plugin_id, payload
    return payload


def _plugin(status: dict, plugin_id: str) -> dict:
    rows = status.get("plugins", [])
    return next((row for row in rows if row.get("id") == plugin_id), {})


def _error_code(response) -> str | None:
    payload = response.json()
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    return error.get("code") if isinstance(error, dict) else None


def _list_public(application):
    response = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "list_records",
        {},
        entrance="public",
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_real_core_authorizes_services_and_retains_fixture_data(request):
    """Exercise admission, public API, schemas, lifecycle and durable storage."""

    storage_wheel, fixture_wheel = _configured_wheels()

    # Delayed fixture lookup makes missing wheel variables a cheap skip before
    # ApplicationHarness creates its private home or starts a Core process.
    application = request.getfixturevalue("toolframe_application")

    cli_status = application.cli("status", "--check").json()
    assert cli_status.get("core") == "ready", cli_status
    assert cli_status.get("ready") is True, cli_status

    initial = application.status()
    assert initial.get("core") == "ready", initial
    assert initial.get("ready") is True, initial

    _assert_operation(
        application.install_plugin(
            storage_wheel,
            plugin_id=_STORAGE_PLUGIN,
            enable=True,
        ),
        plugin_id=_STORAGE_PLUGIN,
    )
    application.wait_for_plugin(_STORAGE_PLUGIN)

    _assert_operation(
        application.install_plugin(
            fixture_wheel,
            plugin_id=_FIXTURE_PLUGIN,
            enable=True,
        ),
        plugin_id=_FIXTURE_PLUGIN,
    )
    application.wait_for_plugin(_FIXTURE_PLUGIN)

    status = application.status()
    assert status.get("core") == "ready", status
    assert status.get("ready") is True, status
    for plugin_id in (_STORAGE_PLUGIN, _FIXTURE_PLUGIN):
        row = _plugin(status, plugin_id)
        assert row.get("active"), row
        assert row.get("healthy") is True, row

    # The fixture's read-only method is explicitly public and needs no token.
    assert _list_public(application) == {"records": [], "count": 0}

    # A visitor has no system privilege.  Core requires a signed-in operator
    # before it evaluates this system scope, so the anonymous request is 401.
    # The harness supplies its owned management identity for the allowed call;
    # the test body never reads or handles that credential.
    denied = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "privileged",
        {},
        entrance="visitor",
    )
    assert denied.status_code == 401, denied.text
    assert _error_code(denied) == "authentication_required", denied.text

    allowed = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "privileged",
        {},
        entrance="visitor",
        management=True,
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json() == {"ok": True}

    # Internal calls are a plugin-to-plugin boundary and stay closed even to
    # the application's management identity.
    internal = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "internal_probe",
        {},
        entrance="visitor",
        management=True,
    )
    assert internal.status_code == 403, internal.text
    assert _error_code(internal) == "forbidden", internal.text

    public_append = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "append",
        {"body": "public writes are not enabled"},
        entrance="public",
    )
    assert public_append.status_code == 403, public_append.text
    assert _error_code(public_append) == "forbidden", public_append.text

    invalid = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "append",
        {},
        entrance="visitor",
        management=True,
    )
    assert invalid.status_code == 400, invalid.text
    # Core 0.6 reports a worker-side JSON Schema failure as handler_failed.  The
    # status and boundary assertion above remain the contract if a later Core
    # release renames this implementation detail to invalid_input.
    assert _error_code(invalid) == "handler_failed", invalid.text

    created = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "append",
        {"body": "persistent record"},
        entrance="visitor",
        management=True,
    )
    assert created.status_code == 200, created.text
    assert created.json() == {"id": 1, "body": "persistent record"}

    _assert_operation(application.plugin_action("reload", _FIXTURE_PLUGIN))
    application.wait_for_plugin(_FIXTURE_PLUGIN)
    assert _list_public(application) == {
        "records": [{"id": 1, "body": "persistent record"}],
        "count": 1,
    }

    _assert_operation(application.plugin_action("disable", _FIXTURE_PLUGIN))
    disabled = application.status()
    disabled_row = _plugin(disabled, _FIXTURE_PLUGIN)
    assert disabled_row.get("active") is None, disabled_row
    assert disabled_row.get("desired", {}).get("enabled") is False, disabled_row

    _assert_operation(application.plugin_action("enable", _FIXTURE_PLUGIN))
    application.wait_for_plugin(_FIXTURE_PLUGIN)
    assert _list_public(application) == {
        "records": [{"id": 1, "body": "persistent record"}],
        "count": 1,
    }
