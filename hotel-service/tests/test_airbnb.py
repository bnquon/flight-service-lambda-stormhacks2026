import unittest

from airbnb import parse_card, search_url
from request import SearchRequest


def request(**changes):
    return SearchRequest.parse({
        'session_id': 'test', 'destination': 'Tokyo, Japan', 'adults': 2,
        'check_in': '2027-04-10', 'check_out': '2027-04-20', **changes,
    })


def card(**changes):
    return {
        'name': 'Example apartment', 'property_type': 'Rental unit in Tokyo',
        'url': 'https://www.airbnb.ca/rooms/123?adults=2&check_in=2027-04-10&check_out=2027-04-20&tracking=remove',
        'price': '$1,292 CAD total$1,292 CAD total, originally $1,436 CAD',
        'text': '4.78 out of 5 average rating, 597 reviews', **changes,
    }


class AirbnbContractTests(unittest.TestCase):
    def test_total_rating_and_link_match_existing_contract(self):
        hotel = parse_card(card(), request())
        self.assertEqual(hotel['total_price'], 1292)
        self.assertEqual(hotel['rating'], 9.56)
        self.assertEqual(hotel['original_rating'], 4.78)
        self.assertEqual(hotel['review_count'], 597)
        self.assertNotIn('tracking', hotel['url'])

    def test_budget_uses_whole_stay_total(self):
        self.assertIsNone(parse_card(card(), request(budget=1200)))

    def test_unrated_listing_has_null_rating(self):
        self.assertIsNone(parse_card(card(text='New listing'), request())['rating'])

    def test_mismatched_dates_guests_or_host_are_rejected(self):
        for old, new in [('2027-04-20', '2027-04-21'), ('adults=2', 'adults=3'),
                         ('www.airbnb.ca', 'example.com')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                parse_card(card(url=card()['url'].replace(old, new)), request())

    def test_nightly_price_is_not_a_total(self):
        with self.assertRaises(ValueError):
            parse_card(card(price='$129 CAD per night'), request())

    def test_unavailable_listing_is_skipped(self):
        self.assertIsNone(parse_card(card(price='', text='Unavailable'), request()))

    def test_request_url_contains_stay_and_currency(self):
        url = search_url(request())
        self.assertIn('/s/Tokyo--Japan/homes?', url)
        self.assertIn('currency=CAD', url)
        self.assertIn('checkout=2027-04-20', url)


if __name__ == '__main__':
    unittest.main()
