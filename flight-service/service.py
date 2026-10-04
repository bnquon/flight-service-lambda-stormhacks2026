"""Search Google Flights, returning up to fifteen offers with recorded previews."""

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import logging
import os
from time import monotonic
from uuid import uuid4

import google_flights
from delivery import archive_recording, post_results
from live_browser import stream_browser
from request import SearchRequest
from updates import forward_callback_updates, publish_update
from search_logging import log_context, log_event, log_step

RESULTS_PER_SOURCE = google_flights.RESULT_LIMIT
ADAPTERS = (google_flights,)


def run_search(request: SearchRequest) -> dict | None:
    with log_step("search.run", session_id=request.session_id):
        api_key = os.getenv("SKYVERN_API_KEY", "").strip()
        if not api_key or api_key == "replace_me":
            log_event("search.configure", "skipped", session_id=request.session_id, reason="SKYVERN_API_KEY not configured")
            return None
        from skyvern import Skyvern
        import skyvern.library.skyvern_browser

        with forward_callback_updates(request.progress_callback_url):
            result = asyncio.run(search_with_clients(Skyvern, api_key, request))
        from storage import save_search
        save_search(result)
        if not post_results(result, "FLIGHT_RESULTS_POST_URL", request.callback_url):
            save_search(result)
        log_event("search.finished", "completed", session_id=request.session_id,
                  search_id=result["search_id"], status=result["status"], result_count=len(result["flights"]))
        return result


async def search_with_clients(client_type, api_key: str, request: SearchRequest) -> dict:
    # One client serves the sequential Google Flights origin searches.
    clients = [client_type(api_key=api_key, timeout=180) for _ in ADAPTERS]

    async def close_client(client):
        try:
            await asyncio.wait_for(client.aclose(), timeout=10)
        except Exception:
            logging.exception("Skyvern client cleanup failed")

    try:
        return await search_origins(clients[0], request)
    finally:
        await asyncio.gather(*(close_client(client) for client in clients))


def source_record(request: SearchRequest, search_id: str, origin: str, adapter) -> dict:
    return {
        "session_id": request.session_id, "search_id": search_id,
        "origin": origin, "website": adapter.WEBSITE, "status": "searching",
        "flights": [], "error": None, "results_complete": True,
        "skyvern_browser_session_id": None, "live_view_url": None,
        "replay_url": None, "recordings": [], "recording_url": None,
        "recording_error": None, "timings_seconds": {},
    }


async def search_origins(skyvern, request: SearchRequest) -> dict:
    search_id = str(uuid4())
    started = monotonic()
    clients = (skyvern,)
    with log_context(session_id=request.session_id, search_id=search_id):
        created_at = datetime.now(timezone.utc).isoformat()
        record = {
            "session_id": request.session_id, "search_id": search_id, "status": "searching",
            "website": google_flights.WEBSITE, "websites": [a.WEBSITE for a in ADAPTERS],
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
        # Each origin has one recorded Google Flights browser.
        deadline = started + 420
        for origin in request.origins:
            sources = [source_record(request, search_id, origin, adapter) for adapter in ADAPTERS]
            record["origins"].extend(sources)

            async def run_source(client, adapter, source):
                try:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Search time budget exhausted.")
                    flights = await search_origin(client, request, search_id, source,
                                                  timeout=min(240, remaining), adapter=adapter,
                                                  preview=True)
                    eligible = [dict(flight, source=adapter.WEBSITE, website=adapter.WEBSITE)
                                for flight in flights if request.budget is None or flight["price"] <= request.budget]
                    source["flights"] = sorted(eligible, key=lambda f: f["price"])[:RESULTS_PER_SOURCE]
                    source["status"] = "complete" if source["results_complete"] else "partially_complete"
                except Exception as exc:
                    logging.exception("Flight source search failed for %s/%s", adapter.WEBSITE, origin)
                    source["status"] = "failed"
                    source["results_complete"] = False
                    source["error"] = {"code": "ORIGIN_SEARCH_FAILED", "message": str(exc) if isinstance(exc, ValueError)
                                       else "Search or extraction failed; see worker logs."}
                publish_update(request.session_id, search_id, "search.status", origin=origin,
                               website=adapter.WEBSITE, status=source["status"], error=source["error"])

            await asyncio.gather(*(run_source(client, adapter, source)
                                   for client, adapter, source in zip(clients, ADAPTERS, sources)))
        successful = [source for source in record["origins"] if source["status"] != "failed"]
        record["status"] = ("complete" if all(s["status"] == "complete" for s in record["origins"])
                            else "partially_complete" if successful else "failed")
        if not successful:
            record["error"] = {"code": "SEARCH_FAILED", "message": "All flight source searches failed."}
        # Up to fifteen cheapest eligible offers across all origins.
        for adapter in ADAPTERS:
            pool = [flight for source in record["origins"] if source["website"] == adapter.WEBSITE
                    for flight in source["flights"]]
            record["flights"].extend(sorted(pool, key=lambda f: f["price"])[:RESULTS_PER_SOURCE])
        record["flights"].sort(key=lambda f: f["price"])
        record["timings_seconds"] = {"results_ready": round(monotonic()-started, 3)}

        async def archive_source(source):
            if not source["skyvern_browser_session_id"]:
                return
            client = skyvern
            archive_started = monotonic()
            await archive_recording(client, source, [source], f"flights/{source['website']}/{source['origin']}")
            source["timings_seconds"]["recording.archive"] = round(monotonic()-archive_started, 3)

        await asyncio.gather(*(archive_source(source) for source in record["origins"]))
        primary = next((s for s in record["origins"] if s.get("recording_url")), None)
        if primary is None:
            primary = next((s for s in record["origins"] if s.get("replay_url")), record["origins"][0])
        live = next((s for s in record["origins"] if s.get("skyvern_browser_session_id")), record["origins"][0])
        record.update({key: primary.get(key) for key in ("recording_url", "recording_error", "replay_url", "recordings")})
        record.update({key: live.get(key) for key in ("skyvern_browser_session_id", "live_view_url")})
        record["delivery_error"] = None
        record["timings_seconds"]["search_and_recordings"] = round(monotonic()-started, 3)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        publish_update(request.session_id, search_id, "search.status", status=record["status"])
        return record


async def search_origin(skyvern, request: SearchRequest, search_id: str, record: dict,
                        *, timeout: float, adapter=google_flights, preview: bool = True) -> list[dict]:
    """Own source browser lifecycle; the adapter owns navigation/extraction."""
    origin = record["origin"]
    record.setdefault("timings_seconds", {})
    started = monotonic()
    browser = None
    with log_context(session_id=request.session_id, search_id=search_id, origin=origin, website=adapter.WEBSITE):
        try:
            async with asyncio.timeout(timeout):
                browser = await skyvern.launch_cloud_browser(timeout=15)
                record["timings_seconds"]["browser_ready"] = round(monotonic()-started, 3)
                record["skyvern_browser_session_id"] = browser.browser_session_id
                record["live_view_url"] = browser.app_url
                if preview:
                    publish_update(request.session_id, search_id, "browser.live_view", origin=origin,
                                   website=adapter.WEBSITE, provider="skyvern",
                                   browser_session_id=browser.browser_session_id, url=browser.app_url)
                working_page = await browser.get_working_page()
                stream = stream_browser(working_page.page, request.session_id, search_id, origin,
                                        browser.browser_session_id, website=adapter.WEBSITE) if preview else nullcontext()
                async with stream:
                    phase_started = monotonic()
                    await adapter.navigate(working_page.page, request, origin)
                    record["timings_seconds"]["website.navigate"] = round(monotonic()-phase_started, 3)
                    record["status"] = "extracting"
                    if preview:
                        publish_update(request.session_id, search_id, "search.status", origin=origin,
                                       website=adapter.WEBSITE, status="extracting")
                    phase_started = monotonic()
                    flights = await adapter.extract_flights(working_page, request, origin)
                    record["timings_seconds"]["website.extract"] = round(monotonic()-phase_started, 3)
                    return flights
        finally:
            record["timings_seconds"]["results_ready"] = round(monotonic()-started, 3)
            if browser is not None:
                try:
                    await asyncio.wait_for(browser.close(), timeout=30)
                except Exception:
                    logging.exception("Skyvern browser cleanup failed for %s/%s", adapter.WEBSITE, origin)
                try:
                    session = await asyncio.wait_for(skyvern.get_browser_session(browser.browser_session_id), timeout=10)
                    record["recordings"] = [{"url": r.url, "filename": r.filename} for r in session.recordings or []]
                    if record["recordings"]:
                        record["replay_url"] = record["recordings"][0]["url"]
                except Exception:
                    logging.exception("Skyvern recording lookup failed for %s/%s", adapter.WEBSITE, origin)
