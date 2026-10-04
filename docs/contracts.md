# Contract checks

`toolframe_testkit.check_definition` checks a `toolframe-sdk` `PluginDefinition`
at the public SDK boundary. It accepts either a `PluginDefinition` instance or a
mapping and returns a `ContractReport` rather than raising for ordinary contract
mistakes.

```python
from toolframe_testkit import (
    ResourceExample,
    ServiceExample,
    check_definition,
)

report = check_definition(
    plugin,
    service_examples=[
        ServiceExample("notes.records", "list", {"limit": 10}),
    ],
    resource_examples=[
        ResourceExample("list_notes", {}),
    ],
)

if not report.ok:
    for issue in report.issues:
        print(issue.code, issue.path, issue.message, issue.hint)
report.assert_valid()
```

`ContractReport` contains the revalidated `definition` (or `None`) and an
immutable tuple of `ContractIssue` values. Each issue has a machine-readable
`code`, a tuple `path`, a short `message`, and an actionable `hint`. `ok` is true
when the issue tuple is empty. `assert_valid()` returns the checked
`PluginDefinition`; otherwise it raises `ContractCheckError`, whose `.report`
property preserves the structured result.

The checker uses the public SDK validators. It catches malformed definitions,
missing service methods, invalid MCP targets, missing external service
requirements, invalid concrete examples, unavailable or oversized packaged
documents, and (when requested) unavailable callable entry points. Paths point
to the definition field or example value that needs attention.

## Targets, scopes, and schemas

For an `mcp.tool` resource, the target service and method must resolve to a
declared `ServiceSpec`, or to a service supplied through the
`external_services` mapping. A target outside the plugin must also have an
explicit SDK `Requirement`; the checker does not infer dependencies from an
arbitrary service name. The external `ServiceSpec.version` must match the
requirement version. MCP targets must use a business-audience method.

Resource scopes add to the target service scopes. They are not required to be
identical to the service method's scope tuple. An MCP input or output schema may
be narrower than its service schema. The checker validates supplied values
against both schemas where both apply, but it does not attempt to prove a
general JSON Schema implication.

`ServiceExample(service, method, input, output)` checks concrete service input
and, when an output value is supplied, its output schema. `ResourceExample` does
the same for a declared MCP resource and its target service schemas; its fields
are `resource_id`, `input`, and optional `output`. The examples are compatibility
probes: they do not exercise Core admission, authorization, plugin loading, or
runtime code.

By default `check_packaged_resources=True` reads `llms.document` resources from
the current Python environment as UTF-8 and enforces the SDK worker's 256 KiB
document limit. A missing resource or a clean-install packaging mistake is
reported as an issue. Build and install a wheel to check distribution contents.

`load_entrypoints=False` is intentional. Set it to `True` only in a trusted test
environment when callable factory or dashboard entry points should be imported;
imports can execute plugin code. This option does not turn the checker into a
runtime loader.

## What this check does not establish

A passing report establishes that the definition and the supplied examples fit
the public SDK contract in the current environment. It does not establish
general schema subsumption, Core admission or authorization, dependency wheel
availability, database migrations, health behavior, or a successful dashboard or
MCP provider integration. Use [ApplicationHarness](harness.md) for those tests.
