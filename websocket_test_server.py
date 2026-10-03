"""Local frontend bridge: run the Lambda handler and forward real progress."""

import asyncio
from datetime import datetime, timezone
import json
import logging

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from lambda_function import lambda_handler
from updates import forward_updates


def message(message_type, *, session_id=None, search_id=None, **payload):
    return {
        "version": 1, "type": message_type,
        "session_id": session_id, "search_id": search_id,
        "timestamp": datetime.now(timezone.utc).isoformat(), **payload,
    }


async def run_search(websocket, request):
    loop = asyncio.get_running_loop()
    events = asyncio.Queue()

    def enqueue(event):
        # The handler runs in a thread; only the event loop touches its queue.
        loop.call_soon_threadsafe(events.put_nowait, event)

    def worker():
        search_id = None

        def progress(event):
            nonlocal search_id
            search_id = event["search_id"]
            enqueue(event)

        try:
            with forward_updates(progress):
                response = lambda_handler(request, None)
            result = json.loads(response["body"])
            if response["statusCode"] == 200:
                enqueue(message("search.result", session_id=result["session_id"],
                                search_id=result["search_id"], result=result))
            else:
                enqueue(message("search.error", session_id=request.get("session_id"),
                                search_id=search_id, error=result["error"]))
        except Exception:
            logging.exception("Local WebSocket search failed")
            enqueue(message("search.error", session_id=request.get("session_id"),
                            search_id=search_id, error={
                                "code": "SEARCH_EXECUTION_FAILED",
                                "message": "Search execution or Mongo save failed; see terminal logs.",
                            }))
        finally:
            enqueue(None)

    task = asyncio.create_task(asyncio.to_thread(worker))
    try:
        while (event := await events.get()) is not None:
            await websocket.send(json.dumps(event))
    except ConnectionClosed:
        # Closing the page doesn't stop a thread. Let the worker close its cloud browser.
        pass
    finally:
        await task


async def handle_connection(websocket):
    job = None
    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
                if not isinstance(data, dict) or data.get("action") != "search" or not isinstance(data.get("request"), dict):
                    raise ValueError("Expected action=search and a request object.")
            except (ValueError, TypeError):
                await websocket.send(json.dumps(message("search.error", error={
                    "code": "INVALID_MESSAGE", "message": "Send action=search with a JSON request object.",
                })))
                continue
            if job is not None and not job.done():
                await websocket.send(json.dumps(message("search.error", error={
                    "code": "SEARCH_BUSY", "message": "A search is already running on this connection.",
                })))
                continue
            job = asyncio.create_task(run_search(websocket, data["request"]))
    except ConnectionClosed:
        pass
    finally:
        if job is not None:
            await job


async def main():
    # Loopback and known frontend origins keep this billable test bridge local.
    async with serve(handle_connection, "127.0.0.1", 8765, origins=[
        "http://127.0.0.1:8080", "http://localhost:8080",
    ]):
        print("WebSocket: ws://127.0.0.1:8765", flush=True)
        print("Frontend: http://127.0.0.1:8080 (serve frontend/ separately)", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
