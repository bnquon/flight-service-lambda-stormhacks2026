"""Offline search cases: real extraction validation, fake cloud/browser I/O."""

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from request import SearchRequest
import service


def search_request(**changes):
    data = {
        "session_id": "test-session", "origins": ["YVR"], "destination": "NRT",
        "departure_date": "2027-04-10", "return_date": "2027-04-20",
    }
    data.update(changes)
    return SearchRequest.parse(data)


def extracted_results(request, origin, prices):
    return {
        "outcome": "results" if prices else "no_results",
        "search": {
            "origin": origin, "destination": "NRT",
            "departure_date": "2027-04-10",
            "return_date": None if request.trip_type == "one_way" else "2027-04-20",
            "trip_type": request.trip_type, "currency": request.currency,
            "adults": 1, "cabin": "economy", "price_basis": request.trip_type,
        },
        "flights": [
            {
                "airline": "Example Air", "outbound_departure_time_text": "1:00 PM",
                "outbound_arrival_time_text": "2:55 PM+1",
                "outbound_duration_text": "9 hr 55 min", "outbound_stops": 0,
                "price": price, "currency": request.currency,
            }
            for price in prices
        ],
    }


def cloud_browser(session_id, extraction, url="https://example.test/watch"):
    page = SimpleNamespace(page=object(), extraction=extraction)
    browser = SimpleNamespace(
        browser_session_id=session_id, app_url=url,
        get_working_page=AsyncMock(return_value=page), close=AsyncMock(),
    )
    return browser


class SearchCases(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.navigate = AsyncMock()
        self.events = Mock()

        async def extract(page, request, origin):
            return service.google_flights.validate_extraction(page.extraction, request, origin)

        self.extract = AsyncMock(side_effect=extract)
        for target, replacement in (
            ("service.google_flights.navigate", self.navigate),
            ("service.google_flights.extract_flights", self.extract),
            ("service.publish_update", self.events),
            ("service.logging.exception", Mock()),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_search(self, request, *browsers):
        skyvern = SimpleNamespace(
            launch_cloud_browser=AsyncMock(side_effect=list(browsers)),
            get_browser_session=AsyncMock(return_value=SimpleNamespace(recordings=[])),
        )
        result = await service.search_origins(skyvern, request)
        self.assertEqual(skyvern.launch_cloud_browser.await_count, len(request.origins))
        for browser in browsers:
            if not isinstance(browser, Exception):
                browser.close.assert_awaited_once()
        # The final record remains JSON serializable, including the normalized request.
        self.assertEqual(json.loads(json.dumps(result)), result)
        return result

    async def test_multiple_origins_share_search_and_sort_combined_fares(self):
        request = search_request(origins=["yvr", "SEA", "YVR"])
        browsers = [
            cloud_browser("session-yvr", extracted_results(request, "YVR", [1500, 900])),
            cloud_browser("session-sea", extracted_results(request, "SEA", [800, 1200])),
        ]
        result = await self.run_search(request, *browsers)
        self.assertEqual(result["status"], "complete")
        self.assertEqual([flight["price"] for flight in result["flights"]], [800, 900, 1200, 1500])
        self.assertEqual([flight["origin"] for flight in result["flights"]], ["SEA", "YVR", "SEA", "YVR"])
        self.assertEqual([origin["skyvern_browser_session_id"] for origin in result["origins"]],
                         ["session-yvr", "session-sea"])
        self.assertTrue(all(flight["website"] == "google_flights" for flight in result["flights"]))
        self.assertEqual([call.args[2] for call in self.navigate.await_args_list], ["YVR", "SEA"])
        for event in self.events.call_args_list:
            self.assertEqual(event.args[:2], (request.session_id, result["search_id"]))
        statuses = [event.kwargs["status"] for event in self.events.call_args_list
                    if event.args[2] == "search.status" and event.kwargs.get("origin") == "YVR"]
        self.assertEqual(statuses, ["searching", "extracting", "complete"])

    async def test_one_way_request_reaches_extraction_without_return_date(self):
        request = search_request(trip_type="one_way", return_date=None)
        browser = cloud_browser("one-way", extracted_results(request, "YVR", [500]))
        result = await self.run_search(request, browser)
        self.assertEqual(result["status"], "complete")
        self.assertIsNone(result["request"]["return_date"])
        self.assertEqual(result["flights"][0]["price"], 500)
        self.extract.assert_awaited_once_with(browser.get_working_page.return_value, request, "YVR")
        extracted_request = self.extract.await_args.args[1]
        self.assertEqual(extracted_request.trip_type, "one_way")
        self.assertIsNone(extracted_request.return_date)

    async def test_non_cad_currency_is_preserved(self):
        request = search_request(currency="usd")
        result = await self.run_search(
            request, cloud_browser("usd", extracted_results(request, "YVR", [700])),
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["flights"][0]["currency"], "USD")

    async def test_budget_includes_exact_limit_and_filters_across_origins(self):
        request = search_request(origins=["YVR", "SEA"], budget=1000)
        result = await self.run_search(
            request,
            cloud_browser("yvr", extracted_results(request, "YVR", [1001, 1000])),
            cloud_browser("sea", extracted_results(request, "SEA", [900, 2000])),
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual([flight["price"] for flight in result["flights"]], [900, 1000])

    async def test_all_fares_over_budget_is_successful_empty_result(self):
        request = search_request(budget=100)
        result = await self.run_search(
            request, cloud_browser("expensive", extracted_results(request, "YVR", [101])),
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["flights"], [])
        self.assertIsNone(result["error"])

    async def test_confirmed_no_results_is_successful_empty_result(self):
        request = search_request()
        result = await self.run_search(
            request, cloud_browser("empty", extracted_results(request, "YVR", [])),
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["flights"], [])
        self.assertIsNone(result["suggestion"])  # Suggestions are still deferred.

    async def test_unreliable_origin_preserves_other_origins_fares(self):
        request = search_request(origins=["YVR", "SEA"])
        result = await self.run_search(
            request, cloud_browser("bad", {"outcome": "unreliable"}),
            cloud_browser("good", extracted_results(request, "SEA", [800])),
        )
        self.assertEqual(result["status"], "partially_complete")
        self.assertEqual([origin["status"] for origin in result["origins"]], ["failed", "complete"])
        self.assertEqual(result["origins"][0]["error"]["code"], "ORIGIN_SEARCH_FAILED")
        self.assertEqual([flight["origin"] for flight in result["flights"]], ["SEA"])

    async def test_all_origins_unreliable_marks_search_failed(self):
        request = search_request(origins=["YVR", "SEA"])
        result = await self.run_search(
            request, cloud_browser("bad-yvr", {}), cloud_browser("bad-sea", {}),
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "SEARCH_FAILED")
        self.assertEqual(result["flights"], [])

    async def test_launch_failure_does_not_block_next_origin(self):
        request = search_request(origins=["YVR", "SEA"])
        result = await self.run_search(
            request, RuntimeError("cloud unavailable"),
            cloud_browser("sea", extracted_results(request, "SEA", [800])),
        )
        self.assertEqual(result["status"], "partially_complete")
        self.assertIsNone(result["origins"][0]["skyvern_browser_session_id"])

    async def test_navigation_failure_closes_browser_without_extracting(self):
        request = search_request()
        browser = cloud_browser("broken-page", {})
        self.navigate.side_effect = TimeoutError("results never loaded")
        result = await self.run_search(request, browser)
        self.assertEqual(result["status"], "failed")
        self.extract.assert_not_awaited()

    async def test_cleanup_failure_preserves_valid_fares(self):
        request = search_request()
        browser = cloud_browser("close-failed", extracted_results(request, "YVR", [800]))
        browser.close.side_effect = RuntimeError("close unavailable")
        result = await self.run_search(request, browser)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["flights"]), 1)

    async def test_missing_live_url_stays_null_in_record_and_event(self):
        request = search_request()
        result = await self.run_search(
            request, cloud_browser("no-url", extracted_results(request, "YVR", [800]), url=None),
        )
        self.assertEqual(result["status"], "complete")
        self.assertIsNone(result["origins"][0]["live_view_url"])
        event = next(call for call in self.events.call_args_list if call.args[2] == "browser.live_view")
        self.assertIsNone(event.kwargs["url"])

    async def test_expired_shared_budget_does_not_launch_browsers(self):
        skyvern = SimpleNamespace(launch_cloud_browser=AsyncMock())
        # Replace this module's clock, not asyncio's global monotonic clock.
        clock = SimpleNamespace(monotonic=Mock(side_effect=[100, 701, 702]))
        with patch("service.time", clock):
            result = await service.search_origins(skyvern, search_request(origins=["YVR", "SEA"]))
        self.assertEqual(result["status"], "failed")
        skyvern.launch_cloud_browser.assert_not_awaited()

    async def test_recording_lookup_preserves_all_segments_after_close(self):
        request = search_request()
        browser = cloud_browser("recorded", extracted_results(request, "YVR", [800]))
        recordings = [
            SimpleNamespace(url="https://example.test/first.mp4", filename="first.mp4"),
            SimpleNamespace(url="https://example.test/second.mp4", filename=None),
        ]

        async def lookup(session_id):
            browser.close.assert_awaited_once()
            self.assertEqual(session_id, "recorded")
            return SimpleNamespace(recordings=recordings)

        skyvern = SimpleNamespace(
            launch_cloud_browser=AsyncMock(return_value=browser),
            get_browser_session=AsyncMock(side_effect=lookup),
        )
        result = await service.search_origins(skyvern, request)
        origin = result["origins"][0]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(origin["replay_url"], recordings[0].url)
        self.assertEqual(origin["recordings"], [
            {"url": recordings[0].url, "filename": "first.mp4"},
            {"url": recordings[1].url, "filename": None},
        ])
        skyvern.get_browser_session.assert_awaited_once_with("recorded")
        json.dumps(result)

    async def test_missing_or_failed_recording_lookup_preserves_fares(self):
        request = search_request()
        for response in [SimpleNamespace(recordings=None), SimpleNamespace(recordings=[]),
                         RuntimeError("metadata unavailable"), TimeoutError("metadata timed out")]:
            with self.subTest(response=response):
                browser = cloud_browser("no-recording", extracted_results(request, "YVR", [800]))
                lookup = AsyncMock()
                if isinstance(response, Exception):
                    lookup.side_effect = response
                else:
                    lookup.return_value = response
                skyvern = SimpleNamespace(
                    launch_cloud_browser=AsyncMock(return_value=browser), get_browser_session=lookup,
                )
                result = await service.search_origins(skyvern, request)
                origin = result["origins"][0]
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(result["flights"]), 1)
                self.assertEqual(origin["recordings"], [])
                self.assertIsNone(origin["replay_url"])
                lookup.assert_awaited_once()
                browser.close.assert_awaited_once()

    async def test_failed_extraction_can_still_return_recording(self):
        browser = cloud_browser("failed-with-video", {"outcome": "unreliable"})
        skyvern = SimpleNamespace(
            launch_cloud_browser=AsyncMock(return_value=browser),
            get_browser_session=AsyncMock(return_value=SimpleNamespace(recordings=[
                SimpleNamespace(url="https://example.test/failure.mp4", filename="failure.mp4"),
            ])),
        )
        result = await service.search_origins(skyvern, search_request())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["origins"][0]["replay_url"], "https://example.test/failure.mp4")
        browser.close.assert_awaited_once()

    async def test_failed_cleanup_skips_recording_lookup(self):
        request = search_request()
        browser = cloud_browser("still-open", extracted_results(request, "YVR", [800]))
        browser.close.side_effect = TimeoutError("close timed out")
        skyvern = SimpleNamespace(
            launch_cloud_browser=AsyncMock(return_value=browser), get_browser_session=AsyncMock(),
        )
        result = await service.search_origins(skyvern, request)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["origins"][0]["recordings"], [])
        skyvern.get_browser_session.assert_not_awaited()

    async def test_search_deadline_does_not_cancel_completed_fares_during_finalization(self):
        request = search_request()
        browser = cloud_browser("slow-close", extracted_results(request, "YVR", [800]))

        async def slow_close():
            await asyncio.sleep(0.03)

        browser.close.side_effect = slow_close
        skyvern = SimpleNamespace(
            launch_cloud_browser=AsyncMock(return_value=browser),
            get_browser_session=AsyncMock(return_value=SimpleNamespace(recordings=[])),
        )
        origin = {"origin": "YVR", "recordings": [], "replay_url": None}
        flights = await service.search_origin(skyvern, request, "test-search", origin, timeout=0.02)
        self.assertEqual(flights[0]["price"], 800)
        browser.close.assert_awaited_once()
        skyvern.get_browser_session.assert_awaited_once()

    async def test_real_search_timeout_still_closes_browser(self):
        request = search_request()
        browser = cloud_browser("timed-out", {})

        async def stalled_navigation(*args):
            await asyncio.Future()

        self.navigate.side_effect = stalled_navigation
        skyvern = SimpleNamespace(
            launch_cloud_browser=AsyncMock(return_value=browser),
            get_browser_session=AsyncMock(return_value=SimpleNamespace(recordings=[])),
        )
        origin = {"origin": "YVR", "recordings": [], "replay_url": None}
        with self.assertRaises(TimeoutError):
            await service.search_origin(skyvern, request, "test-search", origin, timeout=0.02)
        browser.close.assert_awaited_once()

    async def test_cancellation_closes_active_browser(self):
        request = search_request()
        browser = cloud_browser("cancelled", {})
        skyvern = SimpleNamespace(
            launch_cloud_browser=AsyncMock(return_value=browser),
            get_browser_session=AsyncMock(return_value=SimpleNamespace(recordings=[])),
        )
        navigating = asyncio.Event()

        async def wait_for_cancellation(*args):
            navigating.set()
            await asyncio.Future()

        self.navigate.side_effect = wait_for_cancellation
        task = asyncio.create_task(service.search_origins(skyvern, request))
        try:
            await asyncio.wait_for(navigating.wait(), timeout=1)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        browser.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
