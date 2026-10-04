"""Static and concrete-example checks using the public SDK contract validators."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from jsonschema import SchemaError
from jsonschema import ValidationError as SchemaValidationError
from pydantic import ValidationError
from toolframe_sdk import (
    SYSTEM_SCOPES,
    PluginDefinition,
    ServiceSpec,
    load_object,
    validate,
)

_ABSENT = object()


@dataclass(frozen=True)
class ContractIssue:
    code: str
    path: tuple[str | int, ...]
    message: str
    hint: str


@dataclass(frozen=True)
class ServiceExample:
    service: str
    method: str
    input: Any
    output: Any = _ABSENT


@dataclass(frozen=True)
class ResourceExample:
    resource_id: str
    input: Any
    output: Any = _ABSENT


@dataclass(frozen=True)
class ContractReport:
    definition: PluginDefinition | None
    issues: tuple[ContractIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def assert_valid(self) -> PluginDefinition:
        if not self.ok or self.definition is None:
            raise ContractCheckError(self)
        return self.definition


class ContractCheckError(ValueError):
    def __init__(self, report: ContractReport):
        self.report = report
        lines = [
            f"{item.code} at {'.'.join(map(str, item.path)) or '<definition>'}: "
            f"{item.message} {item.hint}"
            for item in report.issues
        ]
        super().__init__("\n".join(lines) or "No validated definition")


def check_definition(
    definition: PluginDefinition | Mapping[str, Any],
    *,
    check_packaged_resources: bool = True,
    load_entrypoints: bool = False,
    external_services: Mapping[str, ServiceSpec] | None = None,
    service_examples: Iterable[ServiceExample] = (),
    resource_examples: Iterable[ResourceExample] = (),
) -> ContractReport:
    """Validate a definition and report repairable issues.

    Entry-point imports are opt-in because importing plugin Python can execute
    code. Packaged document checks operate in the current Python environment;
    use a clean wheel install to verify distribution contents. MCP schemas may
    narrow service schemas. Examples test concrete compatibility, never general
    JSON Schema implication or Core admission/authorization.
    """
    issues: list[ContractIssue] = []

    def issue(code, path, message, hint):
        issues.append(ContractIssue(code, tuple(path), message, hint))

    raw = (
        definition.model_dump(mode="python")
        if isinstance(definition, PluginDefinition)
        else dict(definition)
    )
    try:
        # Revalidate model instances too: SDK model_copy/model_construct can bypass
        # validators. A JSON round trip also rejects non-JSON/NaN schema values.
        validate({}, raw)
        checked = PluginDefinition.model_validate(raw)
    except ValidationError as error:
        for detail in error.errors(include_input=False, include_url=False):
            if not detail["loc"] and "undeclared scope" in detail["msg"]:
                # The SDK has already rejected this cross-field contract. Add
                # exact source locations to its root-level diagnostic rather
                # than maintaining an alternative authorization validator.
                known = set(raw.get("authorization", {}).get("scopes", {}))
                known.update(SYSTEM_SCOPES)
                locations = []
                for service, spec in raw.get("services", {}).items():
                    for method, method_spec in spec.get("methods", {}).items():
                        locations.append(
                            (
                                (
                                    "services",
                                    service,
                                    "methods",
                                    method,
                                    "required_scopes",
                                ),
                                method_spec.get("required_scopes", ()),
                            )
                        )
                for index, resource in enumerate(raw.get("resources", ())):
                    locations.append(
                        (
                            ("resources", index, "required_scopes"),
                            resource.get("required_scopes", ()),
                        )
                    )
                for path, scopes in locations:
                    for index, scope in enumerate(scopes):
                        if scope not in known:
                            issue(
                                "scope_undeclared",
                                (*path, index),
                                f"Scope {scope} is not declared in this definition.",
                                "Declare an owned scope or use an SDK system scope.",
                            )
                if issues:
                    continue
            issue(
                "definition_invalid",
                detail["loc"],
                detail["msg"],
                "Repair this field using the public SDK PluginDefinition contract.",
            )
        return ContractReport(None, tuple(issues))
    except (SchemaError, TypeError, ValueError) as error:
        issue(
            "definition_invalid",
            (),
            f"SDK schema/JSON validation failed ({type(error).__name__}).",
            "Use valid Draft 2020-12 schemas and finite JSON values.",
        )
        return ContractReport(None, tuple(issues))

    services = {**(external_services or {}), **checked.services}

    def target(service, method, path):
        spec = services.get(service)
        result = spec.methods.get(method) if spec else None
        if result is None:
            issue(
                "resource_target_missing",
                path,
                f"No declared method {service}/{method}.",
                "Declare the target or supply its ServiceSpec in external_services.",
            )
        return result

    def example(schema, value, path):
        try:
            validate(schema, value)
        except SchemaValidationError as error:
            issue(
                "example_schema_invalid",
                (*path, *error.absolute_path),
                f"Example violates the schema's {error.validator} constraint.",
                "Correct the example or schema; instance values are omitted.",
            )
        except (SchemaError, TypeError, ValueError) as error:
            issue(
                "example_schema_invalid",
                path,
                f"Example/schema is invalid JSON ({type(error).__name__}).",
                "Use finite JSON values and valid Draft 2020-12 schemas.",
            )

    def entrypoint(reference, path):
        try:
            result = load_object(reference)
            if not callable(result):
                raise TypeError("Entry point must be callable")
        except Exception as error:
            issue(
                "entrypoint_unavailable",
                path,
                f"Could not load callable {reference} ({type(error).__name__}).",
                "Install the wheel and its dependencies; export a public callable.",
            )

    if load_entrypoints:
        entrypoint(checked.factory, ("factory",))
    mcp = {}
    for index, resource in enumerate(checked.resources):
        path = ("resources", index)
        if resource.kind == "mcp.tool":
            spec = target(
                resource.data["service"],
                resource.data["method"],
                (*path, "data", "method"),
            )
            mcp[resource.id] = (resource, spec, path)
            if resource.data["service"] not in checked.services and resource.data[
                "service"
            ] not in {requirement.service for requirement in checked.requires.values()}:
                issue(
                    "resource_dependency_undeclared",
                    (*path, "data", "service"),
                    "External resource target has no declared service requirement.",
                    "Declare a Requirement for the external target service.",
                )
            elif spec and resource.data["service"] not in checked.services:
                supplied_version = services[resource.data["service"]].version
                for alias, requirement in checked.requires.items():
                    if (
                        requirement.service == resource.data["service"]
                        and requirement.version != supplied_version
                    ):
                        issue(
                            "resource_dependency_version_mismatch",
                            ("requires", alias, "version"),
                            f"Required service v{requirement.version}, "
                            f"supplied ServiceSpec v{supplied_version}.",
                            "Supply a matching service or revise the required version.",
                        )
            if spec and spec.audience != "business":
                issue(
                    "resource_target_not_business",
                    (*path, "data", "method"),
                    "MCP tools can expose only business service methods.",
                    "Target a business method with explicit scopes.",
                )
        elif resource.kind == "dashboard.page" and load_entrypoints:
            entrypoint(resource.data["entrypoint"], (*path, "data", "entrypoint"))
        elif resource.kind == "llms.document" and check_packaged_resources:
            try:
                document = files(resource.data["package"]).joinpath(
                    resource.data["path"]
                )
                if not document.is_file():
                    raise FileNotFoundError()
                content = document.read_text(encoding="utf-8")
                if len(content.encode("utf-8")) > 256 * 1024:
                    issue(
                        "packaged_document_too_large",
                        (*path, "data", "path"),
                        "Document exceeds the SDK worker's 256 KiB limit.",
                        "Split or shorten the packaged document.",
                    )
            except Exception as error:
                issue(
                    "packaged_document_unavailable",
                    (*path, "data", "path"),
                    f"Packaged UTF-8 document is unavailable ({type(error).__name__}).",
                    "Include the document in the wheel; verify a clean installation.",
                )

    for index, item in enumerate(service_examples):
        path = ("service_examples", index)
        spec = target(item.service, item.method, (*path, "method"))
        if spec:
            example(spec.input_schema, item.input, (*path, "input"))
            if item.output is not _ABSENT:
                example(spec.output_schema, item.output, (*path, "output"))
    for index, item in enumerate(resource_examples):
        path = ("resource_examples", index)
        found = mcp.get(item.resource_id)
        if found is None:
            issue(
                "resource_example_missing",
                (*path, "resource_id"),
                f"No MCP resource {item.resource_id}.",
                "Use a declared MCP resource ID.",
            )
            continue
        resource, spec, _ = found
        example(resource.data.get("input_schema", {}), item.input, (*path, "input"))
        if spec:
            example(spec.input_schema, item.input, (*path, "service_input"))
        if item.output is not _ABSENT:
            example(
                resource.data.get("output_schema", {}), item.output, (*path, "output")
            )
            if spec:
                example(spec.output_schema, item.output, (*path, "service_output"))
    return ContractReport(checked, tuple(issues))
