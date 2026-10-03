"""Contract parity using cards captured from a real Google Flights search."""

import json
from pathlib import Path
import unittest

from google_flights import parse_card, validate_extraction
from request import SearchRequest


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "google_flights_cards.json").read_text())


def request_and_settings(**changes):
    data = {
        "session_id": "dom-test", "origins": ["YVR"], "destination": "NRT",
        "departure_date": "2027-04-10", "return_date": "2027-04-20",
    }
    data.update(changes)
    request = SearchRequest.parse(data)
    return request, {
        "origin": "YVR", "destination": "NRT", "departure_date": "2027-04-10",
        "return_date": request.return_date.isoformat() if request.return_date else None,
        "trip_type": request.trip_type, "currency": request.currency,
        "adults": 1, "cabin": "economy", "price_basis": request.trip_type,
    }


class CardParsingTests(unittest.TestCase):
    def test_all_nine_real_cards_match_previous_json_exactly(self):
        request, settings = request_and_settings()
        flights = [parse_card(card, "CAD", "Canadian Dollar", "round_trip")
                   for card in FIXTURE["cards"]]
        result = validate_extraction(
            {"outcome": "results", "search": settings, "flights": flights}, request, "YVR",
        )
        result.sort(key=lambda flight: flight["price"])
        self.assertEqual(result, FIXTURE["expected"])
        self.assertTrue(all(type(flight["price"]) is int for flight in result))

    def test_one_way_usd_with_decimal_price(self):
        request, settings = request_and_settings(trip_type="one_way", return_date=None, currency="USD")
        card = dict(FIXTURE["cards"][0])
        card["label"] = card["label"].replace(
            "1324 Canadian dollars round trip total", "1,234.56 US dollars one way",
        )
        flight = parse_card(card, "USD", "US Dollar", "one_way")
        result = validate_extraction(
            {"outcome": "results", "search": settings, "flights": [flight]}, request, "YVR",
        )
        self.assertEqual(result[0]["price"], 1234.56)
        self.assertEqual(result[0]["currency"], "USD")
        self.assertEqual(result[0]["outbound_arrival_time_text"], "1:00 PM+2")

    def test_one_way_total_label_is_supported(self):
        card = dict(FIXTURE["cards"][0])
        card["label"] = card["label"].replace("round trip total", "one way total")
        self.assertEqual(parse_card(card, "CAD", "Canadian Dollar", "one_way")["price"], 1324)

    def test_live_one_way_label_without_fare_basis_is_supported(self):
        request, settings = request_and_settings(trip_type="one_way", return_date=None, currency="USD")
        card = dict(FIXTURE["cards"][0])
        card["label"] = card["label"].replace(
            "1324 Canadian dollars round trip total", "383 US dollars",
        )
        flight = parse_card(card, "USD", "US Dollar", "one_way")
        result = validate_extraction(
            {"outcome": "results", "search": settings, "flights": [flight]}, request, "YVR",
        )
        self.assertEqual(result[0]["price"], 383)
        self.assertEqual(result[0]["currency"], "USD")

    def test_round_trip_label_without_total_basis_is_rejected(self):
        card = dict(FIXTURE["cards"][0])
        card["label"] = card["label"].replace(" round trip total", "")
        with self.assertRaisesRegex(ValueError, "price basis"):
            parse_card(card, "CAD", "Canadian Dollar", "round_trip")

    def test_currency_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "currency"):
            parse_card(FIXTURE["cards"][0], "USD", "US Dollar", "round_trip")

    def test_price_basis_mismatch_is_rejected_in_both_directions(self):
        card = FIXTURE["cards"][0]
        with self.assertRaisesRegex(ValueError, "price basis"):
            parse_card(card, "CAD", "Canadian Dollar", "one_way")
        one_way = {**card, "label": card["label"].replace("round trip total", "one way")}
        with self.assertRaisesRegex(ValueError, "price basis"):
            parse_card(one_way, "CAD", "Canadian Dollar", "round_trip")

    def test_malformed_or_incomplete_accessibility_labels_are_rejected(self):
        original = FIXTURE["cards"][0]
        labels = ["", "From unknown Canadian dollars round trip total.",
                  original["label"].replace("1 stop", "unknown stops"),
                  original["label"].replace("Select flight", "")]
        for label in labels:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "fare label"):
                parse_card({**original, "label": label}, "CAD", "Canadian Dollar", "round_trip")

    def test_missing_displayed_details_cannot_be_successful_fares(self):
        request, settings = request_and_settings()
        for field in ["airline", "departure", "arrival", "duration"]:
            with self.subTest(field=field):
                card = {**FIXTURE["cards"][0], field: ""}
                flight = parse_card(card, "CAD", "Canadian Dollar", "round_trip")
                with self.assertRaisesRegex(ValueError, "Missing flight details"):
                    validate_extraction(
                        {"outcome": "results", "search": settings, "flights": [flight]}, request, "YVR",
                    )

    def test_missing_required_contract_field_is_rejected(self):
        request, settings = request_and_settings()
        flight = parse_card(FIXTURE["cards"][0], "CAD", "Canadian Dollar", "round_trip")
        del flight["outbound_stops"]
        with self.assertRaisesRegex(ValueError, "Missing flight details"):
            validate_extraction(
                {"outcome": "results", "search": settings, "flights": [flight]}, request, "YVR",
            )

    def test_zero_fare_is_rejected(self):
        request, settings = request_and_settings()
        card = dict(FIXTURE["cards"][0])
        card["label"] = card["label"].replace("From 1324", "From 0")
        flight = parse_card(card, "CAD", "Canadian Dollar", "round_trip")
        with self.assertRaisesRegex(ValueError, "Invalid price"):
            validate_extraction(
                {"outcome": "results", "search": settings, "flights": [flight]}, request, "YVR",
            )


if __name__ == "__main__":
    unittest.main()
