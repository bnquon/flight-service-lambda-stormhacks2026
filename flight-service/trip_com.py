"""Read Trip.com fares and pair both legs in a Skyvern browser; never book."""

import asyncio
from datetime import datetime
import json
from math import isfinite
import re
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit

from request import SearchRequest

WEBSITE = "trip_com"
RESULT_LIMIT = 8
MAX_WAIT_SECONDS = 120
SETTLE_SECONDS = 5
# Trip.com uses a city code plus explicit airport filters. NRT/TYO is the
# inspected route; other airport codes are accepted only if the UI confirms them.
CITY_CODES = {"NRT": "TYO"}

READ_DOM = r"""() => {
  const visible = e => e.getBoundingClientRect().width && e.getBoundingClientRect().height;
  const text = document.body.innerText;
  const panel = document.querySelector('[data-search-pac-panel="true"]');
  const value = selector => document.querySelector(selector)?.innerText.trim();
  return {
    url:location.href,
    blocked:/access denied|verify you are human|security verification|verification required|unusual traffic|are you a robot|captcha|trip.com.verification/i.test(text + ' ' + document.title),
    noResults:/no flights found|no results found|no flights available/i.test(text),
    phase:/2\.\s*Returning/i.test(text) ? 'return' : /1\.\s*Departures/i.test(text) ? 'outbound' : null,
    loading:Array.from(document.querySelectorAll('[data-testid="loading-bar"]')).some(visible),
    selected:{date:value('.result-selected-date'),departure:value('.c-result-period__depart'),
      arrival:value('.c-result-period__arrive'),duration:value('.result-duration')},
    settings:{
      roundTrip:document.querySelector('[data-testid="flightType_RT"]')?.getAttribute('aria-checked'),
      oneWay:document.querySelector('[data-testid="flightType_OW"]')?.getAttribute('aria-checked'),
      departure:document.querySelector('[data-testid="search_date_depart0"]')?.getAttribute('data-date'),
      return:document.querySelector('[data-testid="search_date_return0"]')?.getAttribute('data-date'),
      origin:value('[data-testid="search_city_from0_wrapper"]'),
      destination:value('[data-testid="search_city_to0_wrapper"]'),
      currency:value('[aria-label="Language/Currency"]'),
      adults:panel?.getAttribute('data-search-panel-adult'),
      children:panel?.getAttribute('data-search-panel-child'),
      infants:panel?.getAttribute('data-search-panel-infant'),
      cabin:panel?.getAttribute('data-search-panel-cabin')
    },
    cards:Array.from(document.querySelectorAll('[data-testid^="u-flight-card-"]'))
      .filter(e => visible(e) && e.querySelector('[data-testid="u_select_btn"]'))
      .slice(0,8).map(e => {
        const times = Array.from(e.querySelectorAll('[data-testid^="flight-time-"]'));
        return {
          testid:e.getAttribute('data-testid'),
          airline:Array.from(new Set(Array.from(e.querySelectorAll('[data-testid="flights-name"], .airline-info .flight-name'))
            .map(n => n.innerText.trim()).filter(Boolean))).join(' · '),
          price:e.querySelector('[data-testid^="flight_price_"]')?.innerText.trim(),
          basis:e.querySelector('[data-testid="flight-price-tag"]')?.innerText.trim(),
          duration:e.querySelector('[data-testid="flightInfoDuration"], .travel-duration [role="group"]')?.innerText.trim(),
          times:times.map(t => ({id:t.getAttribute('data-testid'),text:t.innerText.trim(),
            airport:t.closest('[role="textbox"]')?.innerText.match(/\b[A-Z]{3}\b/)?.[0]})),
          stops:e.querySelector('[data-testid="stopInfoText"], .stop-city-text')?.innerText.trim(),
          stopCountText:e.querySelector('.stop-num')?.innerText.trim(),
          stopDots:e.querySelectorAll('[data-testid="stopDot"]').length
        };
      })
  };
}"""


class AccessDenied(ValueError):
    pass


def search_url(request: SearchRequest, origin: str) -> str:
    query = {
        "dcity": CITY_CODES.get(origin, origin).lower(),
        "acity": CITY_CODES.get(request.destination, request.destination).lower(),
        "dairport": origin.lower(), "aairport": request.destination.lower(),
        "ddate": request.departure_date.isoformat(),
        "triptype": "rt" if request.return_date else "ow", "class": "y",
        "quantity": "1", "childqty": "0", "babyqty": "0",
        "curr": request.currency, "locale": "en-CA", "sort": "price",
    }
    if request.return_date:
        query["rdate"] = request.return_date.isoformat()
    return "https://ca.trip.com/flights/showfarefirst?" + urlencode(query)


async def navigate(page, request: SearchRequest, origin: str) -> None:
    response = await page.goto(search_url(request, origin), wait_until="domcontentloaded", timeout=60000)
    if response is not None and response.status in (403, 429):
        raise AccessDenied("Trip.com denied browser access.")


def validate_settings(snapshot: dict, request: SearchRequest, origin: str) -> None:
    url = urlsplit(snapshot["url"])
    if url.scheme != "https" or url.hostname != "ca.trip.com" or url.path.lower().rstrip("/") not in (
        "/flights/showfarefirst", "/flights/showfarenext",
    ):
        raise ValueError("Trip.com results URL does not match the inspected search flow.")
    query = parse_qs(url.query)
    expected = {"dairport": origin, "aairport": request.destination,
                "ddate": request.departure_date.isoformat(), "curr": request.currency,
                "triptype": "RT" if request.return_date else "OW", "class": "Y",
                "quantity": "1", "childqty": "0", "babyqty": "0"}
    if request.return_date:
        expected["rdate"] = request.return_date.isoformat()
    if any([v.upper() for v in query.get(k, [])] != [v.upper()] for k, v in expected.items()):
        raise ValueError("Trip.com URL airports, dates, currency, or passengers do not match.")
    settings = snapshot["settings"]
    ticket = "roundTrip" if request.return_date else "oneWay"
    if (settings.get(ticket) != "true" or settings.get("departure") != request.departure_date.isoformat()
            or (request.return_date and settings.get("return") != request.return_date.isoformat())):
        raise ValueError("Trip.com displayed ticket type or full dates do not match.")
    if not all(re.search(rf"\({re.escape(code)}\)", settings.get(key) or "")
               for key, code in (("origin", origin), ("destination", request.destination))):
        raise ValueError("Trip.com displayed airport filters do not match.")
    if any(settings.get(key) != value for key, value in (
        ("adults", "1"), ("children", "0"), ("infants", "0"), ("cabin", "Y"),
    )):
        raise ValueError("Trip.com displayed passenger count or cabin does not match.")
    # Some return layouts hide the header currency. Every fare still has to
    # carry the exact requested code, in addition to the URL check above.
    if settings.get("currency") and settings["currency"] != request.currency:
        raise ValueError("Trip.com displayed currency does not match.")


def parse_card(card: dict, request: SearchRequest, origin: str, *, returning: bool = False) -> dict:
    amount = re.fullmatch(rf"{re.escape(request.currency)}\s*((?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d{{2}})?)", card.get("price") or "")
    basis = "Round-trip" if request.return_date else "One-way"
    if not amount or card.get("basis", "").casefold() != basis.casefold() or not card.get("airline"):
        raise ValueError("Trip.com card has no matching currency, price basis, or airline.")
    price = float(amount.group(1).replace(",", ""))
    if not isfinite(price) or price <= 0:
        raise ValueError("Invalid Trip.com fare.")
    times = card.get("times") or []
    route = (request.destination, origin) if returning else (origin, request.destination)
    if len(times) != 2 or tuple(t.get("airport") for t in times) != route:
        raise ValueError("Trip.com card airports do not match.")
    dates = []
    for value in times:
        timestamp = re.fullmatch(r"flight-time-(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", value.get("id") or "")
        if not timestamp or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value.get("text") or ""):
            raise ValueError("Trip.com card times do not match the inspected format.")
        parsed = datetime.fromisoformat(timestamp.group(1))
        if parsed.strftime("%H:%M") != value["text"]:
            raise ValueError("Trip.com displayed time differs from its full timestamp.")
        dates.append(parsed.date())
    departure = request.return_date if returning else request.departure_date
    if dates[0] != departure:
        raise ValueError("Trip.com card departure date does not match.")
    duration = card.get("duration") or ""
    if not re.fullmatch(r"\d+h(?: \d+m)?|\d+m", duration):
        raise ValueError("Trip.com card duration is missing or unsupported.")
    stops_text = card.get("stops") or ""
    if re.search(r"\bdirect\b|\bnon.?stop\b", stops_text, re.I):
        stops = 0
    elif re.fullmatch(r"\d+", card.get("stopCountText") or ""):
        stops = int(card["stopCountText"])
    elif card.get("stopDots", 0) > 0 and stops_text:
        stops = card["stopDots"]
    else:
        raise ValueError("Trip.com card stops could not be confirmed.")
    offset = (dates[1] - dates[0]).days
    arrival = times[1]["text"] + (f"{offset:+d}" if offset else "")
    return {"airline": card["airline"], "price": price,
            "departure_time_text": times[0]["text"], "arrival_time_text": arrival,
            "duration_text": duration, "stops": stops}


def card_key(card: dict) -> tuple:
    return (card.get("airline"), card.get("duration"),
            tuple(t.get("id") for t in card.get("times", [])),
            card.get("stops"), card.get("stopCountText"), card.get("stopDots"))


async def read_fares(page, request: SearchRequest, origin: str, deadline: float,
                     *, returning: bool = False) -> tuple[list[tuple[dict, dict]], bool]:
    signature = None
    stable_since = first_fares = None
    latest = []
    last_error = None
    while monotonic() < deadline:
        snapshot = await page.evaluate(READ_DOM)
        if snapshot["blocked"]:
            raise AccessDenied("Trip.com denied browser access or requested verification.")
        try:
            validate_settings(snapshot, request, origin)
            if snapshot["phase"] != ("return" if returning else "outbound"):
                raise ValueError("Trip.com has not loaded the requested flight-selection step.")
        except ValueError as exc:
            last_error = exc
            latest = []
            signature = stable_since = first_fares = None
            await asyncio.sleep(1)
            continue
        latest = []
        malformed = False
        for card in snapshot["cards"]:
            try:
                latest.append((card, parse_card(card, request, origin, returning=returning)))
            except ValueError as exc:
                malformed, last_error = True, exc
        if snapshot["noResults"] and not latest and not snapshot["loading"]:
            return [], True
        current = json.dumps([fare for _, fare in latest], sort_keys=True)
        now = monotonic()
        if latest:
            first_fares = first_fares if first_fares is not None else now
            if current != signature:
                signature, stable_since = current, now
            if now-stable_since >= SETTLE_SECONDS and (len(latest) >= RESULT_LIMIT or now-first_fares >= 10):
                return sorted(latest, key=lambda item: item[1]["price"]), not (snapshot["loading"] or malformed)
        else:
            signature = stable_since = first_fares = None
        await asyncio.sleep(1)
    if latest:
        return sorted(latest, key=lambda item: item[1]["price"]), False
    raise ValueError(f"Trip.com did not provide matching fare cards before the deadline: {last_error or 'still loading'}")


def normalized_flight(outbound: dict, request: SearchRequest, origin: str, returning: dict | None = None) -> dict:
    price = returning["price"] if returning else outbound["price"]
    airline = outbound["airline"]
    if returning and returning["airline"] != airline:
        airline += " · " + returning["airline"]
    flight = {"airline": airline, "origin": origin, "destination": request.destination,
              "website": WEBSITE, "source": WEBSITE, "currency": request.currency, "price": price}
    for prefix, leg in (("outbound", outbound), ("return", returning)):
        if leg:
            flight.update({f"{prefix}_{key}": leg[key] for key in (
                "departure_time_text", "arrival_time_text", "duration_text", "stops",
            )})
    return flight


async def select_outbound(page, card: dict, timeout: int) -> None:
    # Positional card IDs can be reused while results reorder. Require the
    # inspected airline, timestamps, duration, fare and stops at click time.
    locator = page.get_by_test_id(card["testid"])
    for time in card["times"]:
        locator = locator.filter(has=page.get_by_test_id(time["id"]))
    for text in (card["airline"], card["duration"], card["price"], card.get("stops")):
        if text:
            # Partner airlines may occupy separate spans.
            for part in text.split(" · "):
                locator = locator.filter(has_text=part)
    await locator.get_by_role("button", name="Select this fare", exact=True).click(timeout=timeout)


async def find_outbound(page, wanted: dict, request: SearchRequest, origin: str, deadline: float) -> tuple[dict, dict]:
    # Returning to an already inspected list doesn't need another full-list
    # settling delay. Read the matching card again and guard its identity at click.
    while monotonic() < deadline:
        snapshot = await page.evaluate(READ_DOM)
        if snapshot["blocked"]:
            raise AccessDenied("Trip.com denied browser access or requested verification.")
        try:
            validate_settings(snapshot, request, origin)
            if snapshot["phase"] == "outbound":
                for card in snapshot["cards"]:
                    if card_key(card) == card_key(wanted):
                        return card, parse_card(card, request, origin)
        except ValueError:
            pass
        await asyncio.sleep(1)
    raise ValueError("Trip.com outbound fare changed or failed to reload before selection.")


async def validate_selected_outbound(page, outbound: dict, request: SearchRequest, origin: str) -> None:
    snapshot = await page.evaluate(READ_DOM)
    if snapshot["blocked"]:
        raise AccessDenied("Trip.com denied browser access or requested verification.")
    validate_settings(snapshot, request, origin)
    selected = snapshot["selected"]
    date = request.departure_date
    expected_date = f"{date.strftime('%a, %b')} {date.day}"
    expected_arrival = re.sub(r"([+-]\d+)$", r"\1d", outbound["arrival_time_text"])
    if (snapshot["phase"] != "return" or selected["date"] != expected_date
            or selected["departure"] != outbound["departure_time_text"]
            or selected["arrival"] != expected_arrival or selected["duration"] != outbound["duration_text"]):
        raise ValueError("Trip.com selected outbound summary does not match the paired flight.")


async def extract_flights(working_page, request: SearchRequest, origin: str,
                          metadata: dict | None = None) -> list[dict]:
    page = working_page.page
    started = monotonic()
    deadline = started + MAX_WAIT_SECONDS
    flights = []
    complete = True
    try:
        async with asyncio.timeout(MAX_WAIT_SECONDS):
            candidates, complete = await read_fares(page, request, origin, min(deadline, started+60))
            if metadata is not None:
                metadata["timings_seconds"]["outbound_cards"] = round(monotonic()-started, 3)
            if not request.return_date:
                flights = [normalized_flight(fare, request, origin) for _, fare in candidates]
            else:
                # Keep outbound variety: pair each of the eight displayed outbound fares
                # with its cheapest validated return. A displayed outbound fare alone
                # does not establish an actual return itinerary or its final total.
                for index, (card, outbound) in enumerate(candidates[:RESULT_LIMIT]):
                    if monotonic() >= deadline:
                        complete = False
                        break
                    try:
                        if index:
                            await page.locator('[aria-label="Change flight"]').click(
                                timeout=min(10000, max(1, int((deadline-monotonic())*1000))))
                            card, outbound = await find_outbound(page, card, request, origin, min(deadline, monotonic()+15))
                        await select_outbound(page, card, min(10000, max(1, int((deadline-monotonic())*1000))))
                        returns, settled = await read_fares(page, request, origin, min(deadline, monotonic()+20), returning=True)
                        complete = complete and settled
                        if not returns:
                            complete = False
                            continue
                        await validate_selected_outbound(page, outbound, request, origin)
                        flights.append(normalized_flight(outbound, request, origin, returns[0][1]))
                    except AccessDenied:
                        if not flights:
                            raise
                        complete = False
                        break
                    except Exception:
                        # Don't reuse the wrong outbound after failed navigation or
                        # selection. Preserve only the pairs already established.
                        if not flights:
                            raise
                        complete = False
                        break
    except TimeoutError:
        if not flights:
            raise ValueError("Trip.com did not establish a validated itinerary before the deadline.")
        complete = False
    flights = sorted((f for f in flights if request.budget is None or f["price"] <= request.budget),
                     key=lambda f: f["price"])[:RESULT_LIMIT]
    if metadata is not None:
        metadata["results_complete"] = complete
        metadata["timings_seconds"]["pairs_ready"] = round(monotonic()-started, 3)
        if not complete:
            metadata["warning"] = "Trip.com search ended before all displayed fares settled or could be paired. Only validated offers are included; some options may be missing."
    return flights
