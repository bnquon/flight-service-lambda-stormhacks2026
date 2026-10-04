"""Local frontend bridge: run the Lambda handler and forward real progress."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import Event

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from lambda_function import lambda_handler
from updates import forward_updates


def load_dotenv(path):
    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def message(message_type, *, session_id=None, search_id=None, **payload):
    return {
        "version": 1, "type": message_type,
        "session_id": session_id, "search_id": search_id,
        "timestamp": datetime.now(timezone.utc).isoformat(), **payload,
    }


async def run_search(websocket, request):
    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    latest_frames = {}
    disconnected = Event()

    def deliver(event):
        if disconnected.is_set():
            return
        if event is not None and event["type"] == "browser.frame":
            key = (event["origin"], event["browser_session_id"])
            if key not in latest_frames:
                events.put_nowait(key)
            latest_frames[key] = event
        else:
            events.put_nowait(event)

    def enqueue(event):
        # The handler runs in a thread; only the event loop touches its queue.
        if not disconnected.is_set():
            loop.call_soon_threadsafe(deliver, event)

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
            if isinstance(event, tuple):
                event = latest_frames.pop(event)
            await asyncio.wait_for(websocket.send(json.dumps(event)), timeout=10)
    except (ConnectionClosed, TimeoutError):
        # Closing the page doesn't stop a thread. Let the worker close its cloud browser.
        disconnected.set()
        await websocket.close()
    finally:
        disconnected.set()
        latest_frames.clear()
        while not events.empty():
            events.get_nowait()
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
    port = int(os.environ.get("WS_PORT", "8766"))
    async with serve(handle_connection, "127.0.0.1", port, origins=[
        "http://127.0.0.1:8080", "http://localhost:8080",
        "http://127.0.0.1:3000", "http://localhost:3000",
        "http://127.0.0.1:3001", "http://localhost:3001",
        "http://127.0.0.1:8000", "http://localhost:8000",
    ]):
        print(f"WebSocket: ws://127.0.0.1:{port}", flush=True)
        print("Frontend: http://127.0.0.1:8080 (serve frontend/ separately)", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent / ".env")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
