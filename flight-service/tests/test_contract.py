"""Request, extraction, and Lambda boundary tests; no cloud access required."""

import base64
import copy
import json
import unittest
from unittest.mock import patch

from google_flights import validate_extraction
from lambda_function import lambda_handler
from request import InvalidRequest, SearchRequest


def request_data(**overrides):
    return {
        "session_id": "test-session",
        "origins": ["YVR"],
        "destination": "NRT",
        "departure_date": "2027-04-10",
        "return_date": "2027-04-20",
        **overrides,
    }


def extraction_data(request):
    return {
        "outcome": "results",
        "search": {
            "origin": "YVR", "destination": request.destination,
            "departure_date": request.departure_date.isoformat(),
            "return_date": request.return_date.isoformat() if request.return_date else None,
            "trip_type": request.trip_type, "currency": request.currency,
            "adults": 1, "cabin": "economy", "price_basis": request.trip_type,
        },
        "flights": [{
            "airline": "Air Canada",
            "outbound_departure_time_text": "1:00 PM",
            "outbound_arrival_time_text": "2:55 PM+1",
            "outbound_duration_text": "9 hr 55 min",
            "outbound_stops": 0, "price": 1919, "currency": request.currency,
        }],
    }


class RequestTests(unittest.TestCase):
    def test_defaults(self):
        request = SearchRequest.parse(request_data())
        self.assertEqual(request.trip_type, "round_trip")
        self.assertEqual(request.currency, "CAD")
        self.assertIsNone(request.budget)

    def test_normalization_and_deduplication(self):
        request = SearchRequest.parse(request_data(
            session_id=" test-session ", origins=[" yvr ", "YYZ", "YVR"],
            destination="nrt", currency="usd", budget=1500.5,
        ))
        self.assertEqual(request.origins, ("YVR", "YYZ"))
        self.assertEqual(request.destination, "NRT")
        self.assertEqual(request.currency, "USD")
        self.assertEqual(request.session_id, "test-session")
        self.assertEqual(request.budget, 1500.5)

    def test_one_way_without_return(self):
        data = request_data(trip_type="one_way")
        del data["return_date"]
        self.assertIsNone(SearchRequest.parse(data).return_date)

    def test_same_day_round_trip_allowed(self):
        request = SearchRequest.parse(request_data(return_date="2027-04-10"))
        self.assertEqual(request.departure_date, request.return_date)

    def test_invalid_requests_identify_field(self):
        cases = [
            ({"session_id": " "}, "session_id"),
            ({"origins": []}, "origins"),
            ({"origins": ["Vancouver"]}, "origins[0]"),
            ({"destination": "YVR"}, "destination"),
            ({"departure_date": "2027-02-30"}, "departure_date"),
            ({"departure_date": "04/10/2027"}, "departure_date"),
            ({"return_date": None}, "return_date"),
            ({"return_date": "2027-04-09"}, "return_date"),
            ({"trip_type": "one_way"}, "return_date"),
            ({"trip_type": "multi_city"}, "trip_type"),
            ({"currency": "Canadian dollars"}, "currency"),
        ]
        cases.extend(({"budget": value}, "budget") for value in
                     [0, -1, True, "100", float("nan"), float("inf")])
        for overrides, field in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(InvalidRequest) as error:
                    SearchRequest.parse(request_data(**overrides))
                self.assertIn(field, [detail["field"] for detail in error.exception.details])

    def test_non_object_request(self):
        with self.assertRaises(InvalidRequest):
            SearchRequest.parse([])


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.request = SearchRequest.parse(request_data())
        self.data = extraction_data(self.request)

    def test_preserves_displayed_details_and_attributes_website(self):
        original = copy.deepcopy(self.data)
        flights = validate_extraction(self.data, self.request, "YVR")
        self.assertEqual(len(flights), 1)
        self.assertEqual(flights[0]["outbound_arrival_time_text"], "2:55 PM+1")
        self.assertEqual(flights[0]["website"], "google_flights")
        self.assertEqual(flights[0]["origin"], "YVR")
        self.assertEqual(flights[0]["destination"], "NRT")
        self.assertEqual(self.data, original)

    def test_one_way_and_other_currency(self):
        request = SearchRequest.parse(request_data(
            trip_type="one_way", return_date=None, currency="USD",
        ))
        flights = validate_extraction(extraction_data(request), request, "YVR")
        self.assertEqual(flights[0]["currency"], "USD")

    def test_explicit_no_results(self):
        self.data.update(outcome="no_results", flights=[])
        self.assertEqual(validate_extraction(self.data, self.request, "YVR"), [])

    def test_unreliable_or_malformed_envelope_rejected(self):
        for data in [None, [], {}, {**self.data, "outcome": "unreliable"}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_extraction(data, self.request, "YVR")

    def test_search_settings_must_match(self):
        for field, value in {
            "origin": "YYZ", "destination": "HND", "departure_date": "2027-04-11",
            "return_date": None, "trip_type": "one_way", "currency": "USD",
            "adults": 2, "cabin": "business", "price_basis": "one_way",
        }.items():
            data = copy.deepcopy(self.data)
            data["search"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_extraction(data, self.request, "YVR")

    def test_passenger_count_must_be_an_integer(self):
        for value in [True, 1.0]:
            data = copy.deepcopy(self.data)
            data["search"]["adults"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_extraction(data, self.request, "YVR")

    def test_outcome_must_match_list(self):
        for outcome, flights in [("results", []), ("no_results", self.data["flights"]),
                                 ("results", None)]:
            data = {**self.data, "outcome": outcome, "flights": flights}
            with self.subTest(outcome=outcome, flights=flights), self.assertRaises(ValueError):
                validate_extraction(data, self.request, "YVR")

    def test_invalid_flight_fields_rejected(self):
        cases = [("price", value) for value in
                 [0, -1, True, "1919", float("nan"), float("inf")]]
        cases += [("outbound_stops", value) for value in [-1, True, 1.5, "0"]]
        cases += [("currency", "USD"), ("airline", " "),
                  ("outbound_arrival_time_text", None)]
        for field, value in cases:
            data = copy.deepcopy(self.data)
            data["flights"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_extraction(data, self.request, "YVR")

    def test_missing_flight_fields_rejected(self):
        for field in self.data["flights"][0]:
            data = copy.deepcopy(self.data)
            del data["flights"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_extraction(data, self.request, "YVR")


class HandlerTests(unittest.TestCase):
    def test_direct_proxy_and_base64_requests(self):
        data = request_data()
        body = json.dumps(data)
        events = [data, {"body": body}, {
            "body": base64.b64encode(body.encode()).decode(), "isBase64Encoded": True,
        }]
        for event in events:
            with self.subTest(event=event), patch("lambda_function.run_search") as search:
                search.return_value = {"status": "complete", "flights": []}
                result = lambda_handler(event, None)
                self.assertEqual(result["statusCode"], 200)
                self.assertEqual(json.loads(result["body"]), search.return_value)
                search.assert_called_once_with(SearchRequest.parse(data))

    def test_malformed_proxy_body(self):
        events = [{"body": "{"}, {"body": None}, {"body": {}},
                  {"body": "!invalid!", "isBase64Encoded": True},
                  {"body": "/w==", "isBase64Encoded": True}]
        for event in events:
            with self.subTest(event=event), patch("lambda_function.run_search") as search:
                result = lambda_handler(event, None)
                self.assertEqual(result["statusCode"], 400)
                self.assertEqual(json.loads(result["body"])["error"]["code"], "INVALID_JSON")
                search.assert_not_called()

    def test_invalid_request_does_not_start_search(self):
        for event in [{}, [], {"body": "null"}, request_data(return_date=None)]:
            with self.subTest(event=event), patch("lambda_function.run_search") as search:
                result = lambda_handler(event, None)
                self.assertEqual(result["statusCode"], 400)
                self.assertEqual(json.loads(result["body"])["error"]["code"], "INVALID_REQUEST")
                search.assert_not_called()

    def test_not_configured(self):
        with patch("lambda_function.run_search", return_value=None):
            result = lambda_handler(request_data(), None)
        self.assertEqual(result["statusCode"], 503)
        self.assertEqual(json.loads(result["body"])["error"]["code"], "SEARCH_NOT_CONFIGURED")

    def test_execution_errors_are_not_mislabeled_as_input_errors(self):
        with patch("lambda_function.run_search", side_effect=RuntimeError("search failed")):
            with self.assertRaisesRegex(RuntimeError, "search failed"):
                lambda_handler(request_data(), None)


if __name__ == "__main__":
    unittest.main()
