"""Run a search directly for this slice; job submission will be added later."""

import asyncio
from datetime import datetime, timezone
import logging
import os
import time
from uuid import uuid4

import google_flights
from request import SearchRequest
from updates import publish_update


def run_search(request: SearchRequest) -> dict | None:
    api_key = os.getenv("SKYVERN_API_KEY", "").strip()
    if not api_key or api_key == "replace_me":
        return None
    # Lazy import keeps local validation usable without installing the cloud SDK.
    from skyvern import Skyvern
    # Check browser dependencies before launch can create a billable cloud session.
    import skyvern.library.skyvern_browser

    # Bound cloud API calls; flight extraction uses the raw Playwright page.
    skyvern = Skyvern(api_key=api_key, timeout=180)
    # TODO: move execution behind an asynchronous job boundary for production.
    result = asyncio.run(search_origins(skyvern, request))
    from storage import save_search
    save_search(result)
    return result


async def search_origins(skyvern, request: SearchRequest) -> dict:
    search_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    record = {
        "session_id": request.session_id, "search_id": search_id, "status": "searching",
        "request": {
            "origins": list(request.origins), "destination": request.destination,
            "departure_date": request.departure_date.isoformat(),
            "return_date": request.return_date.isoformat() if request.return_date else None,
            "trip_type": request.trip_type, "currency": request.currency, "budget": request.budget,
        },
        "origins": [], "flights": [], "suggestion": None, "error": None,
        "created_at": created_at, "updated_at": created_at,
    }
    publish_update(request.session_id, search_id, "search.status", status="searching")
    # Share the time budget across origins; leave room for cleanup before Lambda exits.
    deadline = time.monotonic() + 600
    for origin in request.origins:
        origin_record = {
            "origin": origin, "website": google_flights.WEBSITE, "status": "searching",
            "skyvern_browser_session_id": None,
            "live_view_url": None, "replay_url": None, "recordings": [], "error": None,
        }
        record["origins"].append(origin_record)
        publish_update(request.session_id, search_id, "search.status", origin=origin, status="searching")
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Search time budget exhausted.")
            # One separate cloud browser per origin, sequentially: no shared-tab state.
            flights = await search_origin(
                skyvern, request, search_id, origin_record, timeout=min(240, remaining),
            )
            record["flights"].extend(flights)
            origin_record["status"] = "complete"
        except Exception:
            # An external search/extraction failure must not erase other origins' fares.
            logging.exception("Flight search failed for origin %s", origin)
            origin_record["status"] = "failed"
            origin_record["error"] = {
                "code": "ORIGIN_SEARCH_FAILED", "message": "Search or extraction failed; see worker logs.",
            }
        publish_update(request.session_id, search_id, "search.status", origin=origin,
                       status=origin_record["status"], error=origin_record["error"])
    successes = sum(origin["status"] == "complete" for origin in record["origins"])
    record["status"] = (
        "complete" if successes == len(request.origins) else "partially_complete" if successes else "failed"
    )
    if record["status"] == "failed":
        record["error"] = {"code": "SEARCH_FAILED", "message": "All origin searches failed."}
    if request.budget is not None:
        record["flights"] = [flight for flight in record["flights"] if flight["price"] <= request.budget]
    record["flights"].sort(key=lambda flight: flight["price"])
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    # TODO: no-result suggestion rule is still awaiting review.
    publish_update(request.session_id, search_id, "search.status", status=record["status"])
    return record


async def search_origin(
    skyvern, request: SearchRequest, search_id: str, record: dict, *, timeout: float,
) -> list[dict]:
    """Own the browser and progress; website code only navigates and extracts."""
    origin = record["origin"]
    deadline = asyncio.get_running_loop().time() + timeout
    async with asyncio.timeout_at(deadline):
        browser = await skyvern.launch_cloud_browser(timeout=15)
    try:
        async with asyncio.timeout_at(deadline):
            record["skyvern_browser_session_id"] = browser.browser_session_id
            # The SDK supplies a dashboard URL, not a verified public embed or replay URL.
            record["live_view_url"] = browser.app_url
            publish_update(request.session_id, search_id, "browser.live_view", origin=origin,
                           provider="skyvern", browser_session_id=browser.browser_session_id,
                           url=record["live_view_url"])
            page = await browser.get_working_page()
            # Other websites can expose the same navigate / extract_flights functions.
            await google_flights.navigate(page.page, request, origin)
            record["status"] = "extracting"
            publish_update(request.session_id, search_id, "search.status", origin=origin, status="extracting")
            return await google_flights.extract_flights(page, request, origin)
    finally:
        # Finalization has its own bounds so a search deadline cannot discard valid fares.
        try:
            await asyncio.wait_for(browser.close(), timeout=30)
        except Exception:
            # Keep valid fares if cleanup fails; cloud session expiry is the fallback.
            logging.exception("Skyvern browser cleanup failed for origin %s", origin)
        else:
            # One lookup only: recordings may not be ready yet, and URLs may expire.
            try:
                session = await asyncio.wait_for(
                    skyvern.get_browser_session(browser.browser_session_id), timeout=10,
                )
                record["recordings"] = [
                    {"url": recording.url, "filename": recording.filename}
                    for recording in session.recordings or []
                ]
                # Convenience link to the first segment; recordings contains every segment.
                if record["recordings"]:
                    record["replay_url"] = record["recordings"][0]["url"]
            except Exception:
                logging.exception("Skyvern recording lookup failed for origin %s", origin)
        # TODO: verify live-view embedding/auth and recording URL expiry/retention.
