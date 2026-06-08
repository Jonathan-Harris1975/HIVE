import json
from collections.abc import AsyncIterator
from typing import Any


def encode_sse(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def heartbeat_stream(source: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Convert structured stream events to SSE frames.

    The upstream OpenRouter stream can emit comments, JSON chunks, errors and a final
    done marker. This wrapper emits exactly what the source says; it does not add a
    fake success event after an error.
    """

    emitted_done = False
    try:
        async for item in source:
            event = item.get("event", "message")
            data = {key: value for key, value in item.items() if key != "event"}
            if event == "done":
                emitted_done = True
            yield encode_sse(event, data)
    except Exception as exc:  # noqa: BLE001 - deliberate last-resort stream safety net
        yield encode_sse("error", {"message": str(exc)})
        emitted_done = True
        yield encode_sse("done", {"ok": False})

    if not emitted_done:
        yield encode_sse("done", {"ok": False, "message": "Stream ended without a done event"})
