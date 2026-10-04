"""Validate the single-room hotel search contract."""

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
    destination: str
    check_in: date
    check_out: date
    adults: int
    budget: int | float | None
    currency: str = "CAD"
    rooms: int = 1

    callback_url: str | None = None

    @classmethod
    def parse(cls, data: object) -> "SearchRequest":
        if not isinstance(data, dict):
            raise InvalidRequest([{"field": "body", "message": "Expected a JSON object."}])
        errors = []

        def fail(field: str, message: str) -> None:
            errors.append({"field": field, "message": message})

        for field in ("session_id", "destination"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                fail(field, "Expected a non-empty string.")
        dates = {}
        for field in ("check_in", "check_out"):
            value = data.get(field)
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                dates[field] = date.fromisoformat(value)
            except ValueError:
                fail(field, "Expected a valid date in YYYY-MM-DD format.")
        if len(dates) == 2 and dates["check_out"] <= dates["check_in"]:
            fail("check_out", "Must be after check_in.")
        adults = data.get("adults")
        if type(adults) is not int or adults < 1:
            fail("adults", "Expected a positive integer.")
        if data.get("rooms", 1) != 1 or type(data.get("rooms", 1)) is not int:
            fail("rooms", "Only one room is supported.")
        if data.get("currency", "CAD") != "CAD":
            fail("currency", "Only CAD is supported.")
        budget = data.get("budget")
        if budget is not None and (
            type(budget) not in (int, float) or budget <= 0
            or (isinstance(budget, float) and not isfinite(budget))
        ):
            fail("budget", "Expected a positive total-stay budget, or omit it.")
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
            session_id=data["session_id"].strip(), destination=data["destination"].strip(),
            check_in=dates["check_in"], check_out=dates["check_out"],
            adults=adults, budget=budget, callback_url=callback_url,
        )
