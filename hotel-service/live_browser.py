"""Best-effort Chrome screencast using the existing Playwright connection."""

import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from updates import has_update_listener, publish_update


@asynccontextmanager
async def stream_browser(page, session_id, search_id, origin, browser_session_id, *, website=None):
    # Lambda invocations without a live subscriber don't capture frames.
    if not has_update_listener():
        yield
        return

    client = None
    pump = None
    frames = asyncio.Queue(maxsize=8)
    accepting = True
    unavailable = False
    metadata = {"origin": origin, "browser_session_id": browser_session_id}
    if website:
        metadata["website"] = website

    def status(value):
        publish_update(session_id, search_id, "browser.stream", **metadata, status=value)

    def receive(frame):
        nonlocal accepting, unavailable
        if accepting:
            try:
                frames.put_nowait(frame)
            except asyncio.QueueFull:
                # Chrome normally limits unacknowledged frames; bound memory anyway.
                accepting = False
                unavailable = True
                status("unavailable")

    async def forward():
        nonlocal unavailable, accepting
        last_sent = float("-inf")
        live = False
        try:
            while True:
                frame = await frames.get()
                now = asyncio.get_running_loop().time()
                if accepting and now - last_sent >= 0.2:
                    publish_update(session_id, search_id, "browser.frame", **metadata,
                                   mime_type="image/jpeg", data=frame["data"])
                    last_sent = now
                    if not live:
                        status("live")
                        live = True
                # Acknowledge even frames skipped by the five FPS limit.
                await asyncio.wait_for(client.send("Page.screencastFrameAck", {
                    "sessionId": frame["sessionId"],
                }), timeout=3)
        except Exception:
            accepting = False
            unavailable = True
            logging.exception("Live browser frame forwarding failed for %s", origin)
            status("unavailable")

    status("starting")
    try:
        try:
            async with asyncio.timeout(5):
                client = await page.context.new_cdp_session(page)
                client.on("Page.screencastFrame", receive)
                await client.send("Page.startScreencast", {
                    "format": "jpeg", "quality": 60,
                    "maxWidth": 1280, "maxHeight": 800, "everyNthFrame": 2,
                })
            pump = asyncio.create_task(forward())
        except Exception:
            unavailable = True
            accepting = False
            logging.exception("Live browser capture unavailable for %s", origin)
            status("unavailable")
        yield
    finally:
        accepting = False
        if pump is not None:
            pump.cancel()
            with suppress(asyncio.CancelledError):
                await pump
        if client is not None:
            with suppress(Exception):
                client.remove_listener("Page.screencastFrame", receive)
            # Detach our session only; keep Skyvern's connection/recording intact.
            for cleanup in (lambda: client.send("Page.stopScreencast"), client.detach):
                try:
                    await asyncio.wait_for(cleanup(), timeout=3)
                except Exception:
                    logging.warning("Live browser cleanup failed for %s", origin, exc_info=True)
        if not unavailable:
            status("ended")
