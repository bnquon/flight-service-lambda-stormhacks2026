"""Read selected settings from the inspected controls, including complete years."""

import re
import unittest
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

from google_flights import extract_flights, read_search_settings, validate_extraction
from request import SearchRequest


def controls_page(*, trip="Round trip", dates="April 10, 2027. April 20, 2027."):
    controls = []
    for role, label, text in [
        ("combobox", "Where from? Vancouver YVR", ""),
        ("combobox", "Where to? Tokyo NRT", ""),
        ("combobox", "Change ticket type. " + trip, trip),
        ("combobox", "Change seating class. Economy", "Economy"),
        ("button", "1 passenger, change number of passengers.", "1"),
        ("button", "Currency CAD", ""),
        ("textbox", "Departure", ""),
        ("button", "Done. " + dates, ""),
    ]:
        locator = Mock()
        locator.get_attribute = AsyncMock(return_value=label)
        locator.inner_text = AsyncMock(return_value=text)
        locator.click = AsyncMock()
        controls.append((role, label, locator))

    def get_by_role(role, *, name, **kwargs):
        matches = [locator for candidate_role, label, locator in controls
                   if candidate_role == role and
                   (name.search(label) if isinstance(name, re.Pattern) else name == label)]
        if len(matches) != 1:
            raise AssertionError("Selector did not match one inspected control")
        return matches[0]

    return SimpleNamespace(get_by_role=Mock(side_effect=get_by_role))


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_controls_match_request(self):
        request = SearchRequest.parse({
            "session_id": "settings", "origins": ["YVR"], "destination": "NRT",
            "departure_date": "2027-04-10", "return_date": "2027-04-20",
        })
        settings = await read_search_settings(controls_page())
        self.assertEqual(validate_extraction(
            {"outcome": "no_results", "search": settings, "flights": []}, request, "YVR",
        ), [])
        self.assertEqual(settings["adults"], 1)
        self.assertEqual(settings["cabin"], "economy")

    async def test_one_way_reads_one_full_date(self):
        settings = await read_search_settings(controls_page(trip="One way", dates="April 10, 2027."))
        self.assertEqual(settings["trip_type"], "one_way")
        self.assertEqual(settings["departure_date"], "2027-04-10")
        self.assertIsNone(settings["return_date"])

    async def test_calendar_year_mismatch_is_not_filled_from_request(self):
        settings = await read_search_settings(controls_page(dates="April 10, 2028. April 20, 2028."))
        request = SearchRequest.parse({
            "session_id": "settings", "origins": ["YVR"], "destination": "NRT",
            "departure_date": "2027-04-10", "return_date": "2027-04-20",
        })
        with self.assertRaisesRegex(ValueError, "settings"):
            validate_extraction({"outcome": "no_results", "search": settings, "flights": []}, request, "YVR")

    async def test_incomplete_calendar_summary_rejected(self):
        with self.assertRaisesRegex(ValueError, "full search dates"):
            await read_search_settings(controls_page(dates="April 10, 2027."))

    async def test_visible_no_results_returns_empty_without_ai(self):
        request = SearchRequest.parse({
            "session_id": "settings", "origins": ["YVR"], "destination": "NRT",
            "departure_date": "2027-04-10", "return_date": "2027-04-20",
        })
        page = controls_page()
        no_results = Mock()
        no_results.first.is_visible = AsyncMock(return_value=True)
        page.get_by_text = Mock(return_value=no_results)
        wrapper = SimpleNamespace(page=page, extract=AsyncMock(side_effect=AssertionError("AI called")))
        self.assertEqual(await extract_flights(wrapper, request, "YVR"), [])
        wrapper.extract.assert_not_awaited()
