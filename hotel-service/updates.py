"""Status messages with an optional listener for a local WebSocket transport."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from urllib.request import Request, urlopen

_listener = ContextVar("update_listener", default=None)


def has_update_listener() -> bool:
    return _listener.get() is not None


@contextmanager
def forward_updates(listener):
    """Scope delivery to one search, including its worker thread and async tasks."""
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)


def publish_update(session_id: str, search_id: str, message_type: str, **payload) -> None:
    event = {
        "version": 1,
        "type": message_type,
        "session_id": session_id,
        "search_id": search_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    # Frames belong only on the live transport, never in terminal/CloudWatch logs.
    if message_type != "browser.frame":
        print(json.dumps(event), flush=True)
    listener = _listener.get()
    if listener is not None:
        listener(event)


@contextmanager
def forward_callback_updates(url: str | None):
    """Best-effort deployed progress, without blocking browser work on HTTP."""
    # The local WebSocket bridge already supplies a listener.
    if not url or has_update_listener():
        yield
        return
    pending = Queue(maxsize=32)
    stopped = Event()
    last_frame = {}

    def enqueue(event):
        if stopped.is_set():
            return
        if event["type"] == "browser.frame":
            browser = event.get("browser_session_id")
            now = monotonic()
            if now - last_frame.get(browser, float("-inf")) < 0.5:
                return
            last_frame[browser] = now
        try:
            pending.put_nowait(event)
        except Full:
            # Discard stale queued updates rather than hold up navigation.
            try:
                pending.get_nowait()
            except Empty:
                pass
            try:
                pending.put_nowait(event)
            except Full:
                pass

    def deliver():
        while not stopped.is_set():
            try:
                event = pending.get(timeout=0.1)
            except Empty:
                continue
            try:
                payload = json.dumps(event).encode("utf-8")
                request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=3) as response:
                    # Do not read an unbounded response body on a progress path.
                    response.read(1024)
            except Exception:
                # A missing dashboard must never make the actual search fail.
                pass

    worker = Thread(target=deliver, daemon=True)
    worker.start()
    try:
        with forward_updates(enqueue):
            yield
    finally:
        stopped.set()
        worker.join(timeout=4)
