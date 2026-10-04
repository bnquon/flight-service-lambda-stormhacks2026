"""Run one Booking.com search in a recorded Skyvern cloud browser."""

import asyncio
from datetime import datetime, timezone
import logging
import os
from uuid import uuid4

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
        result = asyncio.run(search(Skyvern(api_key=api_key, timeout=180), request))
        from storage import save_search
        save_search(result)
        if not post_results(result, "HOTEL_RESULTS_POST_URL", request.callback_url):
            log_event("results.persist_delivery_error", "started", session_id=request.session_id, search_id=result["search_id"])
            save_search(result)
        log_event("search.finished", "completed", session_id=request.session_id, search_id=result["search_id"],
                  status=result["status"], result_count=len(result["hotels"]),
                  recording_failed=bool(result.get("recording_error")), delivery_failed=bool(result.get("delivery_error")))
        return result


async def search(skyvern, request: SearchRequest) -> dict:
    search_id = str(uuid4())
    with log_context(session_id=request.session_id, search_id=search_id):
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
                with log_step("browser.launch"):
                    browser = await skyvern.launch_cloud_browser(timeout=15)
                    log_event("browser.session", "ready", browser_session_id=browser.browser_session_id)
                record["skyvern_browser_session_id"] = browser.browser_session_id
                record["live_view_url"] = browser.app_url
                publish_update(request.session_id, search_id, "browser.live_view",
                               provider="skyvern", website=booking.WEBSITE,
                               browser_session_id=browser.browser_session_id, url=browser.app_url)
                with log_step("browser.working_page"):
                    working_page = await browser.get_working_page()
                page = working_page.page
                async with stream_browser(page, request.session_id, search_id, booking.WEBSITE,
                                          browser.browser_session_id):
                    with log_step("website.navigate", website=booking.WEBSITE):
                        await booking.navigate(page, request)
                    publish_status("extracting")
                    with log_step("website.extract", website=booking.WEBSITE):
                        record["hotels"] = await booking.extract_hotels(page, request)
                        log_event("website.results", "completed", result_count=len(record["hotels"]))
                record["status"] = "complete"
        except Exception as exc:
            logging.exception("Booking.com search failed")
            record["status"] = "failed"
            message = str(exc) if isinstance(exc, ValueError) else "Booking.com search or extraction failed; see worker logs."
            record["error"] = {"code": "SEARCH_FAILED", "message": message}
        finally:
            if browser is not None:
                try:
                    with log_step("browser.close", browser_session_id=browser.browser_session_id):
                        await asyncio.wait_for(browser.close(), timeout=30)
                    with log_step("recording.lookup", browser_session_id=browser.browser_session_id):
                        session = await asyncio.wait_for(skyvern.get_browser_session(browser.browser_session_id), timeout=10)
                    record["recordings"] = [{"url": r.url, "filename": r.filename} for r in session.recordings or []]
                    log_event("recording.metadata", "completed", recording_count=len(record["recordings"]))
                    if record["recordings"]:
                        record["replay_url"] = record["recordings"][0]["url"]
                except Exception:
                    # Cleanup/recording metadata failures must not discard extracted prices.
                    logging.exception("Skyvern cleanup or recording lookup failed")
        log_event("search.results", "completed", status=record["status"], result_count=len(record["hotels"]))
        await archive_recording(skyvern, record, [record], "hotels")
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        publish_status(record["status"])
        return record
