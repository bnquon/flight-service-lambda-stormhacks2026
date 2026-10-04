"""Search Booking.com and Airbnb concurrently in recorded Skyvern browsers."""

import asyncio
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import logging
import os
from time import monotonic
from uuid import uuid4

import airbnb
import booking
from delivery import archive_recording, post_results
from live_browser import stream_browser
from request import SearchRequest
from updates import publish_update
from search_logging import log_context, log_event, log_step


def run_search(request: SearchRequest) -> dict | None:
    with log_step("search.run", session_id=request.session_id):
        api_key = os.getenv("SKYVERN_API_KEY", "").strip()
        if not api_key or api_key == "replace_me":
            log_event("search.configure", "skipped", session_id=request.session_id, reason="SKYVERN_API_KEY not configured")
            return None
        from skyvern import Skyvern
        import skyvern.library.skyvern_browser
        result = asyncio.run(search_with_clients(Skyvern, api_key, request))
        from storage import save_search
        save_search(result)
        if not post_results(result, "HOTEL_RESULTS_POST_URL", request.callback_url):
            log_event("results.persist_delivery_error", "started", session_id=request.session_id, search_id=result["search_id"])
            save_search(result)
        log_event("search.finished", "completed", session_id=request.session_id, search_id=result["search_id"],
                  status=result["status"], result_count=len(result["hotels"]),
                  recording_failed=bool(result.get("recording_error")), delivery_failed=bool(result.get("delivery_error")))
        return result


async def search_with_clients(client_type, api_key: str, request: SearchRequest) -> dict:
    # Separate clients avoid racing the SDK's lazy Playwright initialization.
    clients = [client_type(api_key=api_key, timeout=180) for _ in range(2)]

    async def close_client(client):
        try:
            await asyncio.wait_for(client.aclose(), timeout=10)
        except Exception:
            logging.exception("Skyvern client cleanup failed")

    try:
        return await search(clients[0], request, airbnb_skyvern=clients[1])
    finally:
        # Recordings have already finalized; cleanup must not discard prices.
        await asyncio.gather(*(close_client(client) for client in clients))


RESULTS_PER_SOURCE = 8


@contextmanager
def timed_phase(origin: dict, phase: str):
    started = monotonic()
    try:
        with log_step(phase):
            yield
    finally:
        origin["timings_seconds"][phase] = round(monotonic() - started, 3)


async def search_origin(skyvern, request: SearchRequest, search_id: str, adapter,
                        *, preview: bool = False) -> dict:
    origin = {
        "session_id": request.session_id, "search_id": search_id,
        "website": adapter.WEBSITE, "status": "searching", "hotels": [], "error": None,
        "skyvern_browser_session_id": None, "live_view_url": None,
        "recordings": [], "replay_url": None, "recording_url": None,
        "recording_error": None, "timings_seconds": {},
    }
    started = monotonic()
    browser = None
    with log_context(website=adapter.WEBSITE):
        try:
            async with asyncio.timeout(240):
                with timed_phase(origin, "browser.launch"):
                    browser = await skyvern.launch_cloud_browser(timeout=15)
                origin["skyvern_browser_session_id"] = browser.browser_session_id
                origin["live_view_url"] = browser.app_url
                if preview:
                    publish_update(request.session_id, search_id, "browser.live_view",
                                   provider="skyvern", website=adapter.WEBSITE,
                                   browser_session_id=browser.browser_session_id, url=browser.app_url)
                with timed_phase(origin, "browser.working_page"):
                    working_page = await browser.get_working_page()
                page = working_page.page
                # Only Booking emits frames: existing consumers have a single preview.
                stream = stream_browser(page, request.session_id, search_id, adapter.WEBSITE,
                                        browser.browser_session_id) if preview else nullcontext()
                async with stream:
                    with timed_phase(origin, "website.navigate"):
                        if adapter is airbnb:
                            await adapter.navigate(page, request, origin["timings_seconds"])
                        else:
                            await adapter.navigate(page, request)
                    if preview:
                        publish_update(request.session_id, search_id, "search.status", status="extracting")
                    with timed_phase(origin, "website.extract"):
                        hotels = await adapter.extract_hotels(page, request)
                        for hotel in hotels:
                            hotel["source"] = adapter.WEBSITE
                            hotel.setdefault("property_type", None)
                            hotel.setdefault("original_rating", hotel.get("rating"))
                            hotel.setdefault("original_rating_scale", 10)
                        origin["hotels"] = sorted(hotels, key=lambda hotel: hotel["total_price"])[:RESULTS_PER_SOURCE]
                origin["status"] = "complete"
        except Exception as exc:
            logging.exception("%s search failed", adapter.WEBSITE)
            origin["status"] = "failed"
            message = str(exc) if isinstance(exc, ValueError) else f"{adapter.WEBSITE} search or extraction failed; see worker logs."
            origin["error"] = {"code": "SEARCH_FAILED", "message": message}
        finally:
            origin["timings_seconds"]["results_ready"] = round(monotonic() - started, 3)
            if browser is not None:
                try:
                    with timed_phase(origin, "browser.close"):
                        await asyncio.wait_for(browser.close(), timeout=30)
                except Exception:
                    # Still attempt recording lookup if closing the browser failed.
                    logging.exception("Skyvern browser cleanup failed for %s", adapter.WEBSITE)
                try:
                    with timed_phase(origin, "recording.lookup"):
                        session = await asyncio.wait_for(skyvern.get_browser_session(browser.browser_session_id), timeout=10)
                    origin["recordings"] = [{"url": r.url, "filename": r.filename} for r in session.recordings or []]
                    if origin["recordings"]:
                        origin["replay_url"] = origin["recordings"][0]["url"]
                except Exception:
                    logging.exception("Skyvern recording lookup failed for %s", adapter.WEBSITE)
        with timed_phase(origin, "recording.archive"):
            await archive_recording(skyvern, origin, [origin], f"hotels/{adapter.WEBSITE}")
        origin["timings_seconds"]["total"] = round(monotonic() - started, 3)
        log_event("source.results", "completed", status=origin["status"], result_count=len(origin["hotels"]),
                  timings_seconds=origin["timings_seconds"])
    return origin


async def search(skyvern, request: SearchRequest, *, airbnb_skyvern=None) -> dict:
    search_id = str(uuid4())
    started = monotonic()
    with log_context(session_id=request.session_id, search_id=search_id):
        now = datetime.now(timezone.utc).isoformat()
        publish_update(request.session_id, search_id, "search.status", status="searching")
        origins = await asyncio.gather(
            search_origin(skyvern, request, search_id, booking, preview=True),
            search_origin(airbnb_skyvern if airbnb_skyvern is not None else skyvern, request, search_id, airbnb),
        )
        successful = [origin for origin in origins if origin["status"] == "complete"]
        status = "complete" if len(successful) == len(origins) else "partially_complete" if successful else "failed"
        hotels = sorted((hotel for origin in origins for hotel in origin["hotels"]),
                        key=lambda hotel: hotel["total_price"])
        # Keep the legacy single replay for callers that have not adopted origins.
        primary = next((origin for origin in origins if origin.get("recording_url")), None)
        if primary is None:
            primary = next((origin for origin in origins if origin.get("replay_url")), origins[0])
        live = next((origin for origin in origins if origin.get("skyvern_browser_session_id")), origins[0])
        record = {
            "session_id": request.session_id, "search_id": search_id, "status": status,
            "website": booking.WEBSITE, "websites": [origin["website"] for origin in origins],
            "request": {
                "destination": request.destination, "check_in": request.check_in.isoformat(),
                "check_out": request.check_out.isoformat(), "adults": request.adults,
                "rooms": request.rooms, "currency": request.currency, "budget": request.budget,
            },
            "skyvern_browser_session_id": live["skyvern_browser_session_id"],
            "live_view_url": live["live_view_url"], "origins": origins,
            "replay_url": primary["replay_url"], "recordings": primary["recordings"],
            "recording_url": primary["recording_url"], "recording_error": primary["recording_error"],
            "delivery_error": None, "hotels": hotels,
            "error": {"code": "SEARCH_FAILED", "message": "Both accommodation sources failed; see origins for details."} if not successful else None,
            "timings_seconds": {"search_and_recordings": round(monotonic() - started, 3)},
            "created_at": now, "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        log_event("search.results", "completed", status=status, result_count=len(hotels))
        publish_update(request.session_id, search_id, "search.status", status=status)
        return record
