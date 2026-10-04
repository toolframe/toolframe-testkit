"""Fixture lifecycle tests; they intentionally do not launch installed Core."""


def test_unconfigured_application_is_a_clear_skip(pytester):
    pytester.makeconftest('pytest_plugins = ["toolframe_testkit.pytest_plugin"]')
    pytester.makepyfile(
        "def test_application(toolframe_application):\n"
        '    raise AssertionError("must skip before starting Core")\n'
    )
    result = pytester.runpytest("-q", "-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*Supply --toolframe-core-command*"])


def test_factory_keyword_override_cleans_home_between_tests(pytester):
    pytester.makeconftest('pytest_plugins = ["toolframe_testkit.pytest_plugin"]')
    pytester.makepyfile("""
import sys
from pathlib import Path

def test_create(toolframe_harness_factory, tmp_path):
    app = toolframe_harness_factory(
        core_command=sys.executable, output_directory=tmp_path / "build")
    Path("owned-home.txt").write_text(str(app.home))
    assert app.home.is_dir()

def test_cleaned():
    assert not Path(Path("owned-home.txt").read_text()).exists()
""")
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=2)
