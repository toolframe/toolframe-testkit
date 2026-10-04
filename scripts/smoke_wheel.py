"""Verify distribution metadata and a fresh install outside the source checkout."""

from __future__ import annotations

import argparse
import email
import json
import os
import shutil
import subprocess
import venv
import zipfile
from pathlib import Path

CHECK = r"""
import importlib.metadata as metadata
import importlib.util
import json
import sys
from toolframe_sdk import load_object
from toolframe_testkit import ResourceExample, ServiceExample, check_definition

distribution = metadata.distribution("toolframe-testkit")
assert distribution.version == "0.1.0"
assert importlib.util.find_spec("toolframe_core") is None
assert importlib.util.find_spec("pytest") is None
assert "pytest" not in sys.modules
assert not any(entry.group in {"toolframe.plugins", "pytest11"}
               for entry in distribution.entry_points)
fixture = metadata.distribution("toolframe-testkit-fixture")
entry, = [entry for entry in fixture.entry_points if entry.group == "toolframe.plugins"]
assert entry.name == "fixture-records"
definition = load_object(entry.value)
report = check_definition(
    definition,
    load_entrypoints=True,
    service_examples=[ServiceExample("fixture-records.records", "append",
                                     {"body": "hello"}, {"id": 1, "body": "hello"})],
    resource_examples=[ResourceExample(
        "list_records", {}, {"records": [], "count": 0})],
)
report.assert_valid()
print(json.dumps({"version": distribution.version, "contract": "valid",
                  "core_installed": False, "pytest_installed": False,
                  "fixture_resource": "packaged UTF-8 document present"}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--fixture-wheel", type=Path, required=True)
    parser.add_argument("--find-links", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    wheel = args.wheel.resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        (metadata_path,) = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        document = email.message_from_bytes(archive.read(metadata_path))
        assert document["Name"] == "toolframe-testkit"
        assert document["Requires-Python"] == ">=3.14"
        assert document["License-Expression"] == "Apache-2.0"
        requirements = document.get_all("Requires-Dist") or []
        assert any(item.startswith("toolframe-sdk==0.3.0") for item in requirements)
        assert not any(" @ " in item or "file:" in item for item in requirements)
        assert not any(
            "fixture_records/" in name or name.startswith("tests/") for name in names
        )
        assert "toolframe_testkit/py.typed" in names

    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}
    }
    environment["UV_NO_CONFIG"] = "1"
    environment["UV_CACHE_DIR"] = str(output / "uv-cache")
    private_home = output / "user"
    private_home.mkdir(exist_ok=True)
    environment["HOME"] = str(private_home)
    environment["USERPROFILE"] = str(private_home)
    uv = shutil.which("uv")
    if not uv:
        parser.error("Install uv to run the clean-install smoke")
    runtime = output / "venv"
    if runtime.exists():
        parser.error(
            "Choose a fresh output directory; refusing to reuse an existing venv"
        )
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    command = [uv, "pip", "install", "--python", str(python)]
    for path in args.find_links:
        command += ["--find-links", str(path.resolve())]
    command += [str(wheel), str(args.fixture_wheel.resolve())]
    with (output / "install.log").open("w") as log:
        subprocess.run(
            command,
            cwd=output,
            env=environment,
            stdout=log,
            stderr=log,
            check=True,
            timeout=180,
        )
    result = subprocess.run(
        [str(python), "-I", "-c", CHECK],
        cwd=output,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    report = json.loads(result.stdout)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
