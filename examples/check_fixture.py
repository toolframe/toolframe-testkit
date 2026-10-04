"""Check the independently installed fixture wheel without starting Core."""

from toolframe_sdk import load_object

from toolframe_testkit import ResourceExample, ServiceExample, check_definition

report = check_definition(
    load_object("fixture_records.definition:plugin"),
    load_entrypoints=True,
    service_examples=[
        ServiceExample(
            "fixture-records.records",
            "append",
            {"body": "hello"},
            {"id": 1, "body": "hello"},
        )
    ],
    resource_examples=[
        ResourceExample("list_records", {}, {"records": [], "count": 0})
    ],
)
report.assert_valid()
print("Fixture definition, callable entry points and packaged document are valid.")
