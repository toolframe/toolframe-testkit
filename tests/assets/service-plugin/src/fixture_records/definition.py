"""Public definition for the records fixture plugin.

The definition intentionally imports only the public Toolframe SDK.  Its
resources and schemas are part of the fixture's contract and are consumed by
the installed Core during testkit integration checks.
"""

from __future__ import annotations

from importlib.resources import files

from toolframe_sdk import (
    AuthorizationSpec,
    MethodSpec,
    Migration,
    PluginDefinition,
    Requirement,
    Resource,
    ScopeSpec,
    ServiceSpec,
    StoreSchema,
)

PACKAGE = "fixture_records"
SERVICE = "fixture-records.records"

RECORD_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "body": {"type": "string", "minLength": 1, "maxLength": 200},
    },
    "required": ["id", "body"],
    "additionalProperties": False,
}
APPEND_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
        }
    },
    "required": ["body"],
    "additionalProperties": False,
}
LIST_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
        }
    },
    "additionalProperties": False,
}
LIST_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "records": {"type": "array", "items": RECORD_SCHEMA},
        "count": {"type": "integer", "minimum": 0},
    },
    "required": ["records", "count"],
    "additionalProperties": False,
}
EMPTY_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
}
OK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"const": True}},
    "required": ["ok"],
    "additionalProperties": False,
}


plugin = PluginDefinition(
    id="fixture-records",
    factory="fixture_records.service:Records",
    authorization=AuthorizationSpec(
        scopes={
            "fixture-records.read": ScopeSpec(title="Read fixture records"),
            "fixture-records.write": ScopeSpec(title="Append fixture records"),
        }
    ),
    requires={"sql": Requirement(service="toolframe.sql")},
    services={
        SERVICE: ServiceSpec(
            methods={
                "append": MethodSpec(
                    audience="business",
                    required_scopes=("fixture-records.write",),
                    description="Append one short record.",
                    input_schema=APPEND_INPUT_SCHEMA,
                    output_schema=RECORD_SCHEMA,
                ),
                "list_records": MethodSpec(
                    audience="business",
                    required_scopes=("fixture-records.read",),
                    public_api=True,
                    description="List the newest fixture records.",
                    input_schema=LIST_INPUT_SCHEMA,
                    output_schema=LIST_OUTPUT_SCHEMA,
                    read_only=True,
                ),
                "privileged": MethodSpec(
                    audience="system",
                    required_scopes=("system.plugins.manage",),
                    description="Exercise a system-authorized service call.",
                    input_schema=EMPTY_INPUT_SCHEMA,
                    output_schema=OK_OUTPUT_SCHEMA,
                ),
                "internal_probe": MethodSpec(
                    audience="internal",
                    description="Exercise an internal plugin call.",
                    input_schema=EMPTY_INPUT_SCHEMA,
                    output_schema=OK_OUTPUT_SCHEMA,
                ),
            }
        )
    },
    store=StoreSchema(
        revision="v1",
        migrations=(
            Migration(
                revision="v1",
                sql=files(PACKAGE)
                .joinpath("migrations", "0001_init.sql")
                .read_text(encoding="utf-8"),
            ),
        ),
    ),
    resources=(
        Resource(
            id="list_records",
            kind="mcp.tool",
            title="List fixture records",
            data={
                "service": SERVICE,
                "method": "list_records",
                "description": "Read the newest fixture records.",
                # Deliberately narrower than the service schema: MCP callers
                # receive the service default and cannot provide a limit.
                "input_schema": EMPTY_INPUT_SCHEMA,
                "output_schema": LIST_OUTPUT_SCHEMA,
            },
        ),
        Resource(
            id="usage",
            kind="llms.document",
            title="Fixture records usage",
            data={"package": PACKAGE, "path": "docs/usage.md"},
        ),
    ),
)
