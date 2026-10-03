"""Google Flights automation using Skyvern's cloud browser."""

from math import isfinite

from google_flights_navigation import navigate
from request import SearchRequest


WEBSITE = "google_flights"

FLIGHT_FIELDS = {
    "airline": {"type": "string", "minLength": 1},
    "outbound_departure_time_text": {"type": "string", "minLength": 1},
    "outbound_arrival_time_text": {"type": "string", "minLength": 1},
    "outbound_duration_text": {"type": "string", "minLength": 1},
    "outbound_stops": {"type": "integer", "minimum": 0},
    "price": {"type": "number", "exclusiveMinimum": 0},
    "currency": {"type": "string"},
}
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["results", "no_results", "unreliable"]},
        "search": {
            "type": "object",
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "departure_date": {"type": "string"},
                "return_date": {"type": ["string", "null"]},
                "trip_type": {"type": "string"},
                "currency": {"type": "string"},
                "adults": {"type": "integer"},
                "cabin": {"type": "string"},
                "price_basis": {"type": "string"},
            },
            "required": ["origin", "destination", "departure_date", "return_date",
                         "trip_type", "currency", "adults", "cabin", "price_basis"],
            "additionalProperties": False,
        },
        "flights": {
            "type": "array",
            "items": {
                "type": "object", "properties": FLIGHT_FIELDS,
                "required": list(FLIGHT_FIELDS), "additionalProperties": False,
            },
        },
    },
    "required": ["outcome", "search", "flights"],
    "additionalProperties": False,
}


def validate_extraction(data: object, request: SearchRequest, origin: str) -> list[dict]:
    # AI output is an external boundary: keep these checks so guessed/malformed
    # fares cannot turn into successful results, even when a schema was supplied.
    if not isinstance(data, dict) or data.get("outcome") not in ("results", "no_results"):
        raise ValueError("Extraction was unreliable.")
    expected_search = {
        "origin": origin, "destination": request.destination,
        "departure_date": request.departure_date.isoformat(),
        "return_date": request.return_date.isoformat() if request.return_date else None,
        "trip_type": request.trip_type, "currency": request.currency,
        "adults": 1, "cabin": "economy", "price_basis": request.trip_type,
    }
    search = data.get("search")
    # Dictionary equality alone accepts True and 1.0 as the integer passenger count.
    if search != expected_search or type(search["adults"]) is not int:
        raise ValueError("Extracted search settings do not match the request.")
    flights = data.get("flights")
    if not isinstance(flights, list) or (data["outcome"] == "results") != bool(flights):
        raise ValueError("Extraction outcome does not match the flight list.")
    validated = []
    for flight in flights:
        if not isinstance(flight, dict) or not FLIGHT_FIELDS.keys() <= flight.keys():
            raise ValueError("Missing flight details.")
        for field, schema in FLIGHT_FIELDS.items():
            if schema["type"] == "string" and (
                not isinstance(flight[field], str) or not flight[field].strip()
            ):
                raise ValueError("Missing flight details.")
        price, stops = flight["price"], flight["outbound_stops"]
        if (
            type(price) not in (int, float) or price <= 0
            or (isinstance(price, float) and not isfinite(price))
            or type(stops) is not int or stops < 0
            or flight["currency"] != request.currency
        ):
            raise ValueError("Invalid price, stops, or currency.")
        validated.append({
            **{field: flight[field] for field in FLIGHT_FIELDS},
            "origin": origin, "destination": request.destination, "website": WEBSITE,
        })
    return validated


async def extract_flights(page, request: SearchRequest, origin: str) -> list[dict]:
    route = (
        f"{request.trip_type} from {origin} to {request.destination}, "
        f"departing {request.departure_date.isoformat()}"
    )
    if request.return_date:
        route += f", returning {request.return_date.isoformat()}"
    route += f", one adult, economy, prices in {request.currency}"
    data = await page.extract(
        f"Extract the currently displayed outbound flight cards for {route}. "
        "Do not navigate, change dates, book, or purchase anything. "
        "Extract actual search settings from the page into search: use uppercase airport "
        "and currency codes, YYYY-MM-DD dates (return_date=null for one-way), "
        "trip_type and price_basis as round_trip or one_way, adults=1, cabin=economy. "
        "For round trips, price must be the displayed total round-trip fare per person, "
        "not an outbound-only price. For one-way trips use the one-way fare. "
        "Keep time and duration text exactly as displayed, including next-day indicators. "
        "Do not invent return-leg details, flight numbers, missing prices, or currency conversions. "
        "Use outcome=unreliable if any required detail or fare basis is uncertain. "
        "Use outcome=no_results with flights=[] only if an explicit no-flights message is visible. "
        "Otherwise use outcome=results. Extract visible cards only; do not claim exhaustive results. "
        "Do not fill search settings from the prompt if they cannot be confirmed on the page. "
        "If the route, dates, passenger count, cabin, currency, or total fare basis cannot be "
        "confirmed, return outcome=unreliable and flights=[].",
        schema=EXTRACTION_SCHEMA,
    )
    # Unlike agent.run_task, page.extract returns JSON directly, not a task run.
    return validate_extraction(data, request, origin)
