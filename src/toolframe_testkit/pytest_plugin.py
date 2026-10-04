"""Opt-in pytest integration: load with pytest_plugins or pytest -p.

There is intentionally no automatic pytest11 entry point, and importing the
runtime package does not import pytest or start an application.
"""

from __future__ import annotations

import shlex
from contextlib import ExitStack
from pathlib import Path

import pytest

from .contracts import check_definition
from .harness import ApplicationHarness


def pytest_addoption(parser):
    group = parser.getgroup("toolframe")
    group.addoption(
        "--toolframe-core-command",
        help="Explicit installed Core argv (shell-style quoting; no shell executes it)",
    )
    group.addoption(
        "--toolframe-find-links",
        action="append",
        default=[],
        help="Wheel directory or https/file URL (repeatable)",
    )
    group.addoption(
        "--toolframe-output-directory",
        default="build/toolframe-testkit",
        help="Ignored directory retaining sanitized child output",
    )


@pytest.fixture
def toolframe_definition_checker():
    """The public definition checker, convenient for parametrized plugin tests."""
    return check_definition


@pytest.fixture
def toolframe_harness_factory(request):
    """Create owned harnesses and close all of them when the test ends.

    A supplied command option is required only when this factory is called.
    Explicit keyword arguments override CLI configuration.
    """
    cleanup = ExitStack()

    def factory(**kwargs):
        command = kwargs.pop("core_command", None)
        if command is None:
            configured = request.config.getoption("--toolframe-core-command")
            if not configured:
                pytest.skip("Supply --toolframe-core-command for installed Core tests")
            command = shlex.split(configured)
        kwargs.setdefault(
            "output_directory",
            Path(request.config.getoption("--toolframe-output-directory")),
        )
        kwargs.setdefault(
            "find_links", request.config.getoption("--toolframe-find-links")
        )
        app = ApplicationHarness(command, **kwargs)
        cleanup.callback(app.close)
        return app

    with cleanup:
        yield factory


@pytest.fixture
def toolframe_application(toolframe_harness_factory):
    """A started private application; skip when no installed command is supplied."""
    with toolframe_harness_factory() as app:
        yield app
