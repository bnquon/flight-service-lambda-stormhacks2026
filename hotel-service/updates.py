"""Status messages with an optional listener for a local WebSocket transport."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json

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
