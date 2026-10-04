"""Run the testkit's installed-Core scenario as a standalone smoke check.

Example::

    python examples/installed_application.py \
      --core-command /path/to/toolframe-core \
      --wheelhouse .local/wheelhouse \
      --storage-wheel /tmp/toolframe_storage_sqlite.whl \
      --fixture-wheel /tmp/toolframe_testkit_fixture.whl \
      --output build/installed-application

The command requires wheels built by the caller.  It owns the temporary Core
home created by :class:`ApplicationHarness` and does not inspect a global
Toolframe installation or credential.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from toolframe_testkit import ApplicationHarness

_STORAGE_PLUGIN = "storage-sqlite"
_FIXTURE_PLUGIN = "fixture-records"
_SERVICE = "fixture-records.records"


def _assert_operation(result, *, plugin_id: str | None = None) -> dict:
    payload = result.json()
    assert payload.get("status") == "succeeded", payload
    if plugin_id is not None:
        assert payload.get("result", {}).get("plugin_id") == plugin_id, payload
    return payload


def _error_code(response) -> str | None:
    payload = response.json()
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    return error.get("code") if isinstance(error, dict) else None


def _list_public(application) -> dict:
    response = application.service_call(
        _FIXTURE_PLUGIN,
        _SERVICE,
        "list_records",
        {},
        entrance="public",
    )
    assert response.status_code == 200, response.text
    return response.json()


def _run(args: argparse.Namespace) -> None:
    find_links = (args.wheelhouse,) if args.wheelhouse else ()
    with ApplicationHarness(
        args.core_command,
        output_directory=args.output,
        find_links=find_links,
    ) as application:
        cli_status = application.cli("status", "--check").json()
        assert cli_status.get("core") == "ready", cli_status
        assert cli_status.get("ready") is True, cli_status

        status = application.status()
        assert status.get("core") == "ready", status
        assert status.get("ready") is True, status

        _assert_operation(
            application.install_plugin(
                args.storage_wheel,
                plugin_id=_STORAGE_PLUGIN,
                enable=True,
            ),
            plugin_id=_STORAGE_PLUGIN,
        )
        application.wait_for_plugin(_STORAGE_PLUGIN)
        _assert_operation(
            application.install_plugin(
                args.fixture_wheel,
                plugin_id=_FIXTURE_PLUGIN,
                enable=True,
            ),
            plugin_id=_FIXTURE_PLUGIN,
        )
        application.wait_for_plugin(_FIXTURE_PLUGIN)

        assert _list_public(application) == {"records": [], "count": 0}

        denied = application.service_call(
            _FIXTURE_PLUGIN,
            _SERVICE,
            "privileged",
            {},
            entrance="visitor",
        )
        # Core requires sign-in before evaluating a system scope for an
        # anonymous visitor; the application's owned management identity is
        # used only for the succeeding authority check.
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
        # Core 0.6 exposes worker-side JSON Schema rejection as handler_failed.
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
        row = next(
            item
            for item in disabled.get("plugins", [])
            if item.get("id") == _FIXTURE_PLUGIN
        )
        assert row.get("active") is None, row
        assert row.get("desired", {}).get("enabled") is False, row

        _assert_operation(application.plugin_action("enable", _FIXTURE_PLUGIN))
        application.wait_for_plugin(_FIXTURE_PLUGIN)
        assert _list_public(application) == {
            "records": [{"id": 1, "body": "persistent record"}],
            "count": 1,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-command", required=True)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--storage-wheel", type=Path, required=True)
    parser.add_argument("--fixture-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    _run(_parser().parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
