"""Runtime implementation for the records fixture plugin."""

from __future__ import annotations

from toolframe_sdk import Plugin, PluginError

from .definition import SERVICE


def _validate_body(body: object) -> str:
    if not isinstance(body, str) or not body.strip() or len(body) > 200:
        raise PluginError(
            "record_invalid",
            "body must be a nonblank string of at most 200 characters.",
        )
    return body


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise PluginError("record_invalid", "limit must be an integer from 1 to 100.")
    return limit


def _ensure_empty(params: dict) -> None:
    if params:
        raise PluginError("invalid_input", "This method does not accept parameters.")


class Records(Plugin):
    """A tiny durable business service used by testkit integration tests."""

    async def append(self, body: object) -> dict:
        body = _validate_body(body)
        storage = self.context.storage()
        async with storage.transaction() as transaction:
            result = await transaction.execute_returning(
                "INSERT INTO records(body) VALUES (?) RETURNING id",
                (body,),
            )
            rows = result.get("rows", [])
            record_id = rows[0].get("id") if len(rows) == 1 else None
            if (
                isinstance(record_id, bool)
                or not isinstance(record_id, int)
                or record_id < 1
            ):
                raise PluginError(
                    "record_storage",
                    "Storage did not return the created record identity.",
                )
            records = await transaction.query(
                "SELECT id, body FROM records WHERE id = ?",
                (record_id,),
            )
            if len(records) != 1:
                raise PluginError(
                    "record_storage",
                    "Created record could not be read back from storage.",
                )
            return records[0]

    async def list_records(self, limit: object = 20) -> dict:
        limit = _validate_limit(limit)
        records = await self.context.storage().query(
            "SELECT id, body FROM records ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return {"records": records, "count": len(records)}

    async def privileged(self, **params: object) -> dict:
        _ensure_empty(params)
        return {"ok": True}

    async def internal_probe(self, **params: object) -> dict:
        _ensure_empty(params)
        return {"ok": True}

    async def invoke(self, service: str, method: str, params: dict) -> dict:
        if service != SERVICE:
            raise PluginError("method_not_found", f"Unknown method {service}/{method}")
        if method == "append":
            return await self.append(**params)
        if method == "list_records":
            return await self.list_records(**params)
        if method == "privileged":
            return await self.privileged(**params)
        if method == "internal_probe":
            return await self.internal_probe(**params)
        raise PluginError("method_not_found", f"Unknown method {service}/{method}")

    async def health(self) -> dict:
        await self.context.storage().query("SELECT id FROM records LIMIT 1")
        return {"runtime": "ready", "business": "ready"}
