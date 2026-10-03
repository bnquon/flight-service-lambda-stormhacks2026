"""Small regressions for hotel validation and the inspected price layouts."""

import unittest

from booking import NO_RESULTS, parse_card
from request import InvalidRequest, SearchRequest


def hotel_request(**changes):
    values = {
        "session_id": "test", "destination": "Tokyo, Japan", "adults": 2,
        "check_in": "2027-04-10", "check_out": "2027-04-20",
    }
    return SearchRequest.parse({**values, **changes})


def hotel_card(price):
    return {
        "name": "Example Hotel", "price": price, "stay": "10 nights, 2 adults",
        "url": "https://www.booking.com/hotel/jp/example.html?checkin=2027-04-10&checkout=2027-04-20&group_adults=2&no_rooms=1",
        "review": "Scored 8.4, Very Good, 2,423 reviews, Opens Example Hotel information",
        "taxes": "Additional charges may apply", "text": "",
    }


class HotelContractTests(unittest.TestCase):
    def test_large_integer_budget_does_not_overflow_validation(self):
        self.assertEqual(hotel_request(budget=10**400).budget, 10**400)

    def test_invalid_numeric_inputs_are_rejected(self):
        for changes in ({"budget": True}, {"budget": float("inf")},
                        {"adults": True}, {"rooms": True}):
            with self.subTest(changes=changes), self.assertRaises(InvalidRequest):
                hotel_request(**changes)

    def test_regular_total_is_used_instead_of_nightly_price(self):
        card = hotel_card("Per nightCAD 177 CAD 1,765 Price CAD 1,765 10 nights, 2 adults")
        self.assertEqual(parse_card(card, hotel_request())["total_price"], 1765)

    def test_discounted_total_is_used_instead_of_original_or_nightly_price(self):
        card = hotel_card("Per nightCAD 105CAD 1,316CAD 1,053Original price CAD 1,316. Current price CAD 1,053.")
        self.assertEqual(parse_card(card, hotel_request())["total_price"], 1053)

    def test_member_prices_are_skipped(self):
        card = {**hotel_card("Price CAD 1000"), "text": "Sign in for this members-only price"}
        self.assertIsNone(parse_card(card, hotel_request()))

    def test_no_result_heading_wordings(self):
        for heading in ("Tokyo: 0 properties found", "No properties found"):
            self.assertIsNotNone(NO_RESULTS.search(heading))
        self.assertIsNone(NO_RESULTS.search("Tokyo: 10 properties found"))


if __name__ == "__main__":
    unittest.main()
