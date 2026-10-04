"""Validate the flight-search contract without external dependencies."""

from dataclasses import dataclass
from datetime import date
from math import isfinite
import re
from urllib.parse import urlsplit


class InvalidRequest(ValueError):
    def __init__(self, details: list[dict[str, str]]):
        self.details = details
        super().__init__("The search request is invalid.")


@dataclass(frozen=True)
class SearchRequest:
    session_id: str
    origins: tuple[str, ...]
    destination: str
    departure_date: date
    return_date: date | None
    trip_type: str
    currency: str
    budget: int | float | None

    callback_url: str | None = None

    @classmethod
    def parse(cls, data: object) -> "SearchRequest":
        if not isinstance(data, dict):
            raise InvalidRequest([{"field": "body", "message": "Expected a JSON object."}])

        errors = []

        def fail(field: str, message: str) -> None:
            errors.append({"field": field, "message": message})

        # Extra fields are ignored; callers can add metadata without breaking searches.
        # Add a session length limit only if a downstream service requires one.
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            fail("session_id", "Expected a non-empty string.")

        def airport(value: object, field: str) -> str:
            # Code format only; Skyvern/provider support is checked during execution.
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value.strip()):
                fail(field, "Expected a three-letter airport code.")
                return ""
            return value.strip().upper()

        raw_origins = data.get("origins")
        origins = []
        if not isinstance(raw_origins, list) or not raw_origins:
            fail("origins", "Expected a non-empty list of airport codes.")
        else:
            origins = list(dict.fromkeys(
                airport(value, f"origins[{index}]")
                for index, value in enumerate(raw_origins)
            ))
        destination = airport(data.get("destination"), "destination")
        if destination and destination in origins:
            fail("destination", "Destination must differ from every origin.")

        def travel_date(field: str) -> date | None:
            value = data.get(field)
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                try:
                    return date.fromisoformat(value)
                except ValueError:
                    pass
            fail(field, "Expected a valid date in YYYY-MM-DD format.")
            return None

        trip_type = data.get("trip_type", "round_trip")
        if trip_type not in ("round_trip", "one_way"):
            fail("trip_type", "Expected round_trip or one_way.")
        departure = travel_date("departure_date")
        return_date = None
        if trip_type == "round_trip":
            return_date = travel_date("return_date")
            if departure and return_date and return_date < departure:
                fail("return_date", "Return date must be on or after departure date.")
        elif trip_type == "one_way" and data.get("return_date") is not None:
            fail("return_date", "Omit return_date for a one-way search.")

        currency = data.get("currency", "CAD")
        # Currency availability is deferred to the search provider.
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
            fail("currency", "Expected a three-letter currency code.")

        # A search filter needs ordinary numbers; exact money arithmetic can wait
        # until it is needed. This service does not charge or book anything.
        budget = data.get("budget")
        if budget is not None:
            if (
                isinstance(budget, bool)
                or not isinstance(budget, (int, float))
                or budget <= 0
                or (isinstance(budget, float) and not isfinite(budget))
            ):
                fail("budget", "Expected a positive number per person, or omit it.")

        callback_url = data.get("callback_url")
        if callback_url is not None:
            try:
                if not isinstance(callback_url, str):
                    raise ValueError()
                parsed = urlsplit(callback_url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                    raise ValueError()
            except ValueError:
                fail("callback_url", "Expected an HTTP or HTTPS callback URL.")

        if errors:
            raise InvalidRequest(errors)
        return cls(
            session_id=session_id.strip(),
            origins=tuple(origins),
            destination=destination,
            departure_date=departure,
            return_date=return_date,
            trip_type=trip_type,
            currency=currency.upper(),
            budget=budget,
            callback_url=callback_url,
        )
