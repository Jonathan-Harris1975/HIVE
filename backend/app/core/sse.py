import json
from collections.abc import AsyncIterator
from typing import Any


def encode_sse(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def heartbeat_stream(source: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Convert structured stream events to SSE frames.

    The upstream OpenRouter stream already sends chunks, but this wrapper gives the browser
    consistent event names and a controlled error frame.
    """

    try:
        async for item in source:
            event = item.pop("event", "message")
            yield encode_sse(event, item)
    except Exception as exc:  # noqa: BLE001 - deliberate last-resort stream safety net
        yield encode_sse("error", {"message": str(exc)})
    finally:
        yield encode_sse("done", {"ok": True})
