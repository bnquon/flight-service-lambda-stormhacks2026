"""Google Flights automation using Skyvern's cloud browser."""

from contextlib import suppress
from datetime import datetime
from math import isfinite
import re

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from google_flights_navigation import navigate
from request import SearchRequest
from search_logging import log_event, log_step


WEBSITE = "google_flights"
RESULT_LIMIT = 15

TEXT_FIELDS = (
    "airline", "outbound_departure_time_text", "outbound_arrival_time_text",
    "outbound_duration_text", "currency",
)
FLIGHT_FIELDS = (*TEXT_FIELDS, "outbound_stops", "price")


def validate_extraction(data: object, request: SearchRequest, origin: str) -> list[dict]:
    # Website data is an external boundary: reject malformed fares and mismatched settings.
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
        if not isinstance(flight, dict) or not set(FLIGHT_FIELDS) <= flight.keys():
            raise ValueError("Missing flight details.")
        for field in TEXT_FIELDS:
            if (
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


def _fold_currency(value: str) -> str:
    return re.sub(r"[\s.]+", " ", value.strip().lower())


def currencies_match(displayed: str, code: str, currency_name: str) -> bool:
    """Google Flights labels CAD as 'Canadian dollars', 'CAD', or 'C$' depending on locale."""
    shown = _fold_currency(displayed)
    name = _fold_currency(currency_name)
    tokens = {
        code.strip().lower(),
        name,
        name.removesuffix("s"),
        f"{name}s" if not name.endswith("s") else name,
    }
    aliases = {
        "CAD": {"c$", "ca$", "can$", "canadian dollar", "canadian dollars", "dollar", "dollars"},
        "USD": {"$", "us$", "us dollar", "us dollars", "american dollar", "american dollars", "dollar", "dollars"},
        "EUR": {"€", "euro", "euros"},
        "GBP": {"£", "pound", "pounds", "pound sterling", "sterling"},
        "ILS": {"₪", "israeli new shekel", "israeli new shekels", "israeli shekel", "israeli shekels", "shekel", "shekels"},
    }
    tokens.update(aliases.get(code.strip().upper(), ()))
    folded = {_fold_currency(token) for token in tokens if token}
    if shown in folded:
        return True
    shown_base = shown.removesuffix("s").strip()
    return shown_base in folded or any(token.removesuffix("s") == shown_base for token in folded)


def parse_card(card: dict, currency: str, currency_name: str, trip_type: str) -> dict:
    """Parse the inspected English accessibility label and displayed card fields."""
    summary = re.fullmatch(
        r"From ([\d,]+(?:\.\d+)?) (.+?)(?: (round trip total|one way(?: total)?))?\. "
        r"(Nonstop|\d+ stops?) flight with .+?\. .*Select flight",
        card["label"],
    )
    if not summary:
        raise ValueError("Unrecognized Google Flights fare label.")
    amount, displayed_currency, basis, stops = summary.groups()
    if not currencies_match(displayed_currency, currency, currency_name):
        raise ValueError("Flight currency does not match the search.")
    # Observed one-way cards omit the fare-basis phrase; the page ticket type is verified below.
    if basis is None and trip_type == "one_way":
        basis = "one way"
    expected_basis = "round trip" if trip_type == "round_trip" else "one way"
    if basis is None or basis.removesuffix(" total") != expected_basis:
        raise ValueError("Flight price basis does not match the search.")
    airline = card["airline"].strip()
    # Preserve partner airlines and operator details, matching the previous JSON format.
    if "·Operated by " in airline:
        airline = airline.replace("·Operated by ", " (Operated by ") + ")"
    airline = re.sub(r"\s*·\s*", " · ", airline)
    price = float(amount.replace(",", ""))
    return {
        "airline": airline,
        "outbound_departure_time_text": card["departure"].strip(),
        "outbound_arrival_time_text": card["arrival"].strip(),
        "outbound_duration_text": card["duration"].strip(),
        "outbound_stops": 0 if stops == "Nonstop" else int(stops.split()[0]),
        "price": int(price) if price.is_integer() else price, "currency": currency,
    }


async def read_search_settings(page) -> dict:
    async def airport(label):
        value = await page.get_by_role("combobox", name=re.compile(rf"^{label} ")).get_attribute("aria-label")
        return value.split()[-1]

    origin = await airport("Where from\\?")
    destination = await airport("Where to\\?")
    ticket = await page.get_by_role("combobox", name=re.compile(r"^Change ticket type\.")).inner_text()
    trip_type = ticket.strip().lower().replace(" ", "_")
    cabin = await page.get_by_role("combobox", name=re.compile(r"^Change seating class\.")).inner_text()
    passengers = await page.get_by_role(
        "button", name=re.compile(r"^\d+ passenger"),
    ).get_attribute("aria-label")
    currency_label = await page.get_by_role(
        "button", name=re.compile(r"^Currency [A-Z]{3}$"),
    ).get_attribute("aria-label")
    # Reopen the calendar to read full years, rather than infer them from "Sat, Apr 10".
    await page.get_by_role("textbox", name="Departure", exact=True).click()
    done = page.get_by_role("button", name=re.compile(r"^Done\."))
    summary = await done.get_attribute("aria-label")
    dates = [datetime.strptime(value, "%B %d, %Y").date().isoformat()
             for value in re.findall(r"[A-Z][a-z]+ \d{1,2}, \d{4}", summary)]
    await done.click()
    if len(dates) != (2 if trip_type == "round_trip" else 1):
        raise ValueError("Could not confirm full search dates.")
    return {
        "origin": origin, "destination": destination,
        "departure_date": dates[0], "return_date": dates[1] if len(dates) == 2 else None,
        "trip_type": trip_type, "currency": currency_label.split()[-1],
        "adults": int(passengers.split()[0]), "cabin": cabin.strip().lower(), "price_basis": trip_type,
    }


async def extract_flights(page, request: SearchRequest, origin: str) -> list[dict]:
    page = page.page
    with log_step("extraction.read_search_settings"):
        search = await read_search_settings(page)
    no_results = page.get_by_text(re.compile(r"^(No flights|No results).*", re.I))
    if await no_results.first.is_visible():
        log_event("extraction.no_results", "completed", result_count=0)
        return validate_extraction({"outcome": "no_results", "search": search, "flights": []}, request, origin)
    # Scope to actual flight cards; ignore cheaper-date suggestions and "View more flights".
    cards = page.get_by_role("main").get_by_role("listitem").filter(
        has=page.get_by_role("link", name=re.compile(r"Select flight$")),
    )
    log_event("extraction.wait_cards", "started")
    await cards.first.wait_for()
    log_event("extraction.wait_cards", "completed")
    # Google may initially show fewer than fifteen fares behind an expander.
    initial_count = await cards.count()
    if initial_count < RESULT_LIMIT:
        more_name = re.compile(r"^(?:View|Show) more flights$", re.I)
        more = page.get_by_role("button", name=more_name).or_(page.get_by_role("link", name=more_name))
        if await more.first.is_visible():
            # Expansion is best effort: keep existing valid fares if loading stalls.
            with suppress(PlaywrightTimeoutError):
                await more.first.click(timeout=5000)
                await cards.nth(initial_count).wait_for(timeout=10000)
    body = await page.get_by_role("main").inner_text()
    if "for 1 adult" not in body:
        raise ValueError("Could not confirm adult fare basis.")
    currency_name = await page.evaluate(
        "currency => new Intl.DisplayNames(['en'], {type: 'currency'}).of(currency)", request.currency,
    )
    raw_cards = await cards.evaluate_all("""nodes => nodes.map(card => {
        const departure = card.querySelector('[role="text"][aria-label^="Departure time:"]');
        const arrival = card.querySelector('[role="text"][aria-label^="Arrival time:"]');
        const times = card.querySelector('span[aria-label^="Leaves "]');
        return {
            label: card.querySelector('[role="link"][aria-label$="Select flight"]').getAttribute('aria-label'),
            departure: departure?.innerText || '', arrival: arrival?.innerText || '',
            duration: card.querySelector('[aria-label^="Total duration "]')?.innerText || '',
            // The airline row immediately follows the inspected departure/arrival row.
            airline: Array.from(times?.parentElement.nextElementSibling?.children || [])
                .map(element => element.textContent.trim()).filter(Boolean).join('·')
        };
    })""")
    log_event("extraction.cards", "completed", card_count=len(raw_cards))
    with log_step("extraction.parse_validate"):
        flights = []
        last_error = None
        for card in raw_cards:
            try:
                flights.append(parse_card(card, request.currency, currency_name, request.trip_type))
            except ValueError as exc:
                last_error = exc
        if not flights:
            raise last_error or ValueError("No parseable Google Flights fare cards.")
        return validate_extraction({"outcome": "results", "search": search, "flights": flights}, request, origin)
