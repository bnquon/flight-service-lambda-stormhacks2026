"""Structured worker logs with request context and elapsed time per step."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import time

SERVICE = "hotels"
logger = logging.getLogger(f"travel_search.{SERVICE}")
logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
# Keep Lambda's existing CloudWatch handler; provide a terminal handler locally.
logging.basicConfig(format="%(message)s")
_context = ContextVar("search_log_context", default={})


@contextmanager
def log_context(**fields):
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def log_event(step: str, phase: str, *, level=logging.INFO, **fields) -> None:
    logger.log(level, "%s", json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": SERVICE, **_context.get(), "step": step, "phase": phase, **fields,
    }, default=str))


@contextmanager
def log_step(step: str, **fields):
    with log_context(**fields):
        started = time.monotonic()
        log_event(step, "started")
        try:
            yield
        except Exception as exc:
            log_event(step, "failed", level=logging.ERROR,
                      elapsed_ms=round((time.monotonic() - started) * 1000),
                      error_type=type(exc).__name__)
            raise
        else:
            log_event(step, "completed", elapsed_ms=round((time.monotonic() - started) * 1000))
