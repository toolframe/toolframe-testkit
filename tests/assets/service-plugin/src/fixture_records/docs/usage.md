# Fixture records

`fixture-records.records` is a small durable business service used by
Toolframe testkit integration checks.

Call `append` with a nonblank `body` of at most 200 characters. It requires
the `fixture-records.write` scope and returns the created `{id, body}` record.

Call `list_records` with an optional `limit` from 1 to 100. It requires the
`fixture-records.read` scope and returns `{records, count}` ordered newest
first. The `list_records` MCP resource intentionally exposes a narrower input
schema, so MCP callers use the default limit and cannot provide that optional
field.

The `privileged` method requires the Core system scope
`system.plugins.manage`; `internal_probe` is available only to internal
plugin-to-plugin calls. Both return `{ "ok": true }`.

Records are stored in the plugin-owned SQL store. Disabling, reloading, or
restarting the plugin leaves the store available for later reads.
