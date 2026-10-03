"""Run one Booking.com search in a recorded Skyvern cloud browser."""

import asyncio
from datetime import datetime, timezone
import logging
import os
from uuid import uuid4

import booking
from live_browser import stream_browser
from request import SearchRequest
from updates import publish_update


def run_search(request: SearchRequest) -> dict | None:
    api_key = os.getenv("SKYVERN_API_KEY", "").strip()
    if not api_key or api_key == "replace_me":
        return None
    from skyvern import Skyvern
    import skyvern.library.skyvern_browser
    result = asyncio.run(search(Skyvern(api_key=api_key, timeout=180), request))
    from storage import save_search
    save_search(result)
    return result


async def search(skyvern, request: SearchRequest) -> dict:
    search_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "session_id": request.session_id, "search_id": search_id, "status": "searching",
        "website": booking.WEBSITE,
        "request": {
            "destination": request.destination, "check_in": request.check_in.isoformat(),
            "check_out": request.check_out.isoformat(), "adults": request.adults,
            "rooms": request.rooms, "currency": request.currency, "budget": request.budget,
        },
        "skyvern_browser_session_id": None, "live_view_url": None,
        "replay_url": None, "recordings": [], "hotels": [], "error": None,
        "created_at": now, "updated_at": now,
    }

    def publish_status(value: str) -> None:
        record["status"] = value
        publish_update(request.session_id, search_id, "search.status", status=value)

    browser = None
    publish_status("searching")
    try:
        async with asyncio.timeout(240):
            browser = await skyvern.launch_cloud_browser(timeout=15)
            record["skyvern_browser_session_id"] = browser.browser_session_id
            record["live_view_url"] = browser.app_url
            publish_update(request.session_id, search_id, "browser.live_view",
                           provider="skyvern", website=booking.WEBSITE,
                           browser_session_id=browser.browser_session_id, url=browser.app_url)
            working_page = await browser.get_working_page()
            page = working_page.page
            async with stream_browser(page, request.session_id, search_id, booking.WEBSITE,
                                      browser.browser_session_id):
                await booking.navigate(page, request)
                publish_status("extracting")
                record["hotels"] = await booking.extract_hotels(page, request)
            record["status"] = "complete"
    except Exception:
        logging.exception("Booking.com search failed")
        record["status"] = "failed"
        record["error"] = {"code": "SEARCH_FAILED", "message": "Booking.com search or extraction failed; see worker logs."}
    finally:
        if browser is not None:
            try:
                await asyncio.wait_for(browser.close(), timeout=30)
                session = await asyncio.wait_for(skyvern.get_browser_session(browser.browser_session_id), timeout=10)
                record["recordings"] = [{"url": r.url, "filename": r.filename} for r in session.recordings or []]
                if record["recordings"]:
                    record["replay_url"] = record["recordings"][0]["url"]
            except Exception:
                # Cleanup/recording metadata failures must not discard extracted prices.
                logging.exception("Skyvern cleanup or recording lookup failed")
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    publish_status(record["status"])
    return record
