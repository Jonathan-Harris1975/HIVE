from __future__ import annotations

import pytest

from app.core.sse import heartbeat_stream


async def one_done():
    yield {"event": "token", "content": "hello"}
    yield {"event": "done", "ok": True}


async def one_error_then_done():
    yield {"event": "error", "message": "bad"}
    yield {"event": "done", "ok": False}


@pytest.mark.asyncio
async def test_heartbeat_stream_does_not_duplicate_done() -> None:
    frames = [frame async for frame in heartbeat_stream(one_done())]
    assert sum(1 for frame in frames if frame.startswith("event: done")) == 1
    assert '"ok": true' in frames[-1]


@pytest.mark.asyncio
async def test_heartbeat_stream_error_done_is_false() -> None:
    frames = [frame async for frame in heartbeat_stream(one_error_then_done())]
    assert sum(1 for frame in frames if frame.startswith("event: done")) == 1
    assert '"ok": false' in frames[-1]
