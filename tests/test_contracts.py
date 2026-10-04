import pytest
from toolframe_sdk import MethodSpec, PluginDefinition, ServiceSpec

from toolframe_testkit import (
    ContractCheckError,
    ResourceExample,
    ServiceExample,
    check_definition,
)


def definition():
    return {
        "id": "notes",
        "factory": "notes.runtime:Notes",
        "authorization": {"scopes": {"notes.read": {"title": "Read notes"}}},
        "services": {
            "notes.records": {
                "methods": {
                    "list": {
                        "audience": "business",
                        "required_scopes": ["notes.read"],
                        "input_schema": {
                            "type": "object",
                            "properties": {"limit": {"type": "integer", "minimum": 1}},
                            "additionalProperties": False,
                        },
                    }
                }
            }
        },
        "resources": [
            {
                "id": "list",
                "kind": "mcp.tool",
                "data": {
                    "service": "notes.records",
                    "method": "missing",
                    "input_schema": {"type": "object", "additionalProperties": False},
                },
            }
        ],
    }


def test_missing_mcp_target_has_actionable_location():
    report = check_definition(definition(), check_packaged_resources=False)
    assert not report.ok
    issue = report.issues[0]
    assert issue.code == "resource_target_missing"
    assert issue.path == ("resources", 0, "data", "method")
    assert "missing" in issue.message
    assert issue.hint


def test_narrower_mcp_schema_and_additional_resource_scope_are_valid():
    data = definition()
    data["authorization"]["scopes"]["notes.export"] = {"title": "Export notes"}
    resource = data["resources"][0]
    resource["data"]["method"] = "list"
    resource["required_scopes"] = ["notes.export"]
    report = check_definition(
        data,
        check_packaged_resources=False,
        resource_examples=[ResourceExample("list", {})],
    )
    assert report.ok
    assert report.assert_valid().id == "notes"


def test_concrete_resource_example_must_also_satisfy_service():
    data = definition()
    data["resources"][0]["data"].update(method="list", input_schema={"type": "object"})
    report = check_definition(
        data,
        check_packaged_resources=False,
        resource_examples=[ResourceExample("list", {"limit": 0})],
    )
    assert report.issues[0].path == ("resource_examples", 0, "service_input", "limit")
    with pytest.raises(ContractCheckError, match="minimum"):
        report.assert_valid()


def test_examples_do_not_disclose_instance_values():
    data = definition()
    data["resources"] = []
    report = check_definition(
        data,
        service_examples=[ServiceExample("notes.records", "list", {"limit": "secret"})],
    )
    assert report.issues[0].code == "example_schema_invalid"
    assert "secret" not in str(report.issues)


def test_definition_revalidates_bypassed_sdk_models():
    data = definition()
    data["resources"] = []
    bypassed = PluginDefinition.model_validate(data).model_copy(update={"id": "Bad"})
    report = check_definition(bypassed)
    assert report.definition is None
    assert report.issues[0].path == ("id",)


def test_missing_packaged_document_is_an_actionable_issue():
    data = definition()
    data["resources"] = [
        {
            "id": "usage",
            "kind": "llms.document",
            "data": {"package": "toolframe_testkit", "path": "missing.md"},
        }
    ]
    report = check_definition(data)
    assert report.issues[0].code == "packaged_document_unavailable"


def test_undeclared_scope_identifies_the_method_and_scope_position():
    data = definition()
    data["services"]["notes.records"]["methods"]["list"]["required_scopes"] = [
        "notes.missing"
    ]
    report = check_definition(data, check_packaged_resources=False)
    assert report.issues[0].code == "scope_undeclared"
    assert report.issues[0].path == (
        "services",
        "notes.records",
        "methods",
        "list",
        "required_scopes",
        0,
    )


def test_non_business_mcp_target_is_rejected():
    data = definition()
    method = data["services"]["notes.records"]["methods"]["list"]
    method.update(audience="internal", required_scopes=[])
    data["resources"][0]["data"]["method"] = "list"
    report = check_definition(data, check_packaged_resources=False)
    assert report.issues[0].code == "resource_target_not_business"


def test_document_worker_limit_is_checked_in_installed_import_package(
    tmp_path, monkeypatch
):
    package = tmp_path / "large_doc_fixture"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "usage.md").write_text("x" * (256 * 1024 + 1))
    monkeypatch.syspath_prepend(tmp_path)
    data = definition()
    data["resources"] = [
        {
            "id": "usage",
            "kind": "llms.document",
            "data": {"package": "large_doc_fixture", "path": "usage.md"},
        }
    ]
    assert check_definition(data).issues[0].code == "packaged_document_too_large"


def test_factory_import_check_is_explicit_and_reports_a_missing_callable():
    data = definition()
    data["resources"] = []
    data["factory"] = "toolframe_testkit:missing_factory"
    assert check_definition(data).ok
    assert check_definition(data, load_entrypoints=True).issues[0].code == (
        "entrypoint_unavailable"
    )


def test_external_resource_target_must_match_declared_requirement_version():
    data = definition()
    data["requires"] = {"remote": {"service": "shared.notes", "version": 1}}
    data["resources"][0]["data"].update(service="shared.notes", method="list")
    external = ServiceSpec(
        version=2,
        methods={
            "list": MethodSpec(audience="business", required_scopes=("shared.read",))
        },
    )
    report = check_definition(data, external_services={"shared.notes": external})
    assert report.issues[0].code == "resource_dependency_version_mismatch"
    assert report.issues[0].path == ("requires", "remote", "version")
