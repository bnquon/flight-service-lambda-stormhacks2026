"""Read KAYAK result cards in a Skyvern browser; never start a booking."""

import asyncio
import json
import re
from time import monotonic
from urllib.parse import urlencode, urlsplit, parse_qs

from request import SearchRequest

WEBSITE = "kayak"
SETTLE_SECONDS = 8
MAX_WAIT_SECONDS = 75

# Observed on the Canadian English results page. Fail closed on unsupported
# currencies or layout changes instead of relabeling an unknown amount.
PRICE_PREFIXES = {
    "CAD": ("C$", "CA$", "CAD"), "USD": ("US$", "$", "USD"),
    "EUR": ("€", "EUR"), "GBP": ("£", "GBP"),
    "AUD": ("A$", "AU$", "AUD"), "NZD": ("NZ$", "NZD"),
}

READ_DOM = """() => {
  const visible = e => e.getBoundingClientRect().width && e.getBoundingClientRect().height;
  return {
    url: location.href,
    controls: Array.from(document.querySelectorAll('[role="button"]')).filter(visible)
      .map(e => ({label:e.getAttribute('aria-label'),text:e.innerText.trim()})),
    resultsReady: document.body.innerText.includes('Results ready.'),
    noResults: /no flights found|no results found/i.test(document.body.innerText),
    blocked: /verify you are human|access denied|unusual traffic|captcha|are you a robot/i.test(document.body.innerText),
    cards: Array.from(document.querySelectorAll('[role="group"][aria-label^="Result item "]'))
      .filter(visible).map(e => ({
        airline:e.querySelector('.J0g6-operator-text')?.innerText.trim(),
        price:e.querySelector('.e2GB-price-text')?.innerText.trim(),
        booking_url:e.querySelector('a[href*="/book/flight"]')?.href,
        legs:Array.from(e.querySelectorAll('.hJSA-item')).map(leg => ({
          label:leg.querySelector('input[aria-label^="Leg "]')?.getAttribute('aria-label'),
          times:leg.querySelector('.VY2U .vmXl')?.innerText.trim(),
          stops:leg.querySelector('.JWEO .vmXl')?.innerText.trim(),
          duration:leg.querySelector('.xdW8 .vmXl')?.innerText.trim()
        }))
      }))
  };
}"""


def search_path(request: SearchRequest, origin: str) -> str:
    path = f"/flights/{origin}-{request.destination}/{request.departure_date.isoformat()}"
    if request.return_date:
        path += f"/{request.return_date.isoformat()}"
    return path


async def navigate(page, request: SearchRequest, origin: str) -> None:
    if request.currency not in PRICE_PREFIXES:
        raise ValueError("KAYAK currency is not supported by the inspected price parser.")
    url = "https://www.ca.kayak.com" + search_path(request, origin)
    await page.goto(url + "?" + urlencode({"sort": "price_a", "currency": request.currency}),
                    wait_until="domcontentloaded", timeout=60000)


def validate_settings(snapshot: dict, request: SearchRequest, origin: str) -> None:
    url = urlsplit(snapshot["url"])
    if (url.hostname != "www.ca.kayak.com" or url.path != search_path(request, origin)
            or parse_qs(url.query).get("currency") != [request.currency]):
        raise ValueError("KAYAK results URL does not match the requested airports/dates/currency.")
    controls = snapshot["controls"]
    labels = {control["label"] for control in controls}
    ticket = "Round-trip" if request.trip_type == "round_trip" else "One-way"
    if not {ticket, "1 adult", "Economy"} <= labels:
        raise ValueError("KAYAK ticket type, cabin, or passenger count does not match.")
    if not any(c["label"] == "Select language" and c["text"] == request.currency for c in controls):
        raise ValueError("KAYAK displayed currency does not match.")
    for prefix, date in (("Departure date ", request.departure_date), ("Return date ", request.return_date)):
        if date is None:
            continue
        expected = f"{date.strftime('%a')} {date.day}/{date.month}"
        if prefix + expected not in labels:
            raise ValueError("KAYAK displayed travel dates do not match.")


def parse_leg(leg: dict, origin: str, destination: str) -> dict:
    if not re.search(rf", {re.escape(origin)} .+ - {re.escape(destination)} ", leg.get("label") or ""):
        raise ValueError("KAYAK card airports do not match the requested route.")
    times = re.split(r"\s*[–−]\s*", leg.get("times") or "")
    if len(times) != 2 or not all(re.fullmatch(r"\d{1,2}:\d{2}\s*[ap]m(?:\+\d+)?", t, re.I) for t in times):
        raise ValueError("Unrecognized KAYAK departure/arrival times.")
    duration = leg.get("duration") or ""
    if not re.fullmatch(r"(?:\d+h)(?: \d+m)?|\d+m", duration):
        raise ValueError("Unrecognized KAYAK duration.")
    stops = leg.get("stops") or ""
    if stops.lower() in ("direct", "nonstop"):
        count = 0
    elif re.fullmatch(r"\d+ stops?", stops):
        count = int(stops.split()[0])
    else:
        raise ValueError("Unrecognized KAYAK stops.")
    return {"departure_time_text": times[0], "arrival_time_text": times[1],
            "duration_text": duration, "stops": count}


def parse_card(card: dict, request: SearchRequest, origin: str) -> dict:
    prefixes = "|".join(re.escape(prefix) for prefix in PRICE_PREFIXES[request.currency])
    price = re.fullmatch(rf"(?:{prefixes})\s*((?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d{{2}})?)", card.get("price") or "")
    if price is None or not card.get("airline"):
        raise ValueError("Missing KAYAK airline or matching currency price.")
    amount = float(price.group(1).replace(",", ""))
    if amount <= 0:
        raise ValueError("Invalid KAYAK fare.")
    legs = card.get("legs") or []
    expected = 2 if request.trip_type == "round_trip" else 1
    if len(legs) != expected:
        raise ValueError("KAYAK card ticket type does not match.")
    outbound = parse_leg(legs[0], origin, request.destination)
    flight = {"airline": card["airline"], "origin": origin, "destination": request.destination,
              "website": WEBSITE, "source": WEBSITE, "currency": request.currency, "price": amount,
              **{f"outbound_{field}": value for field, value in outbound.items()}}
    if expected == 2:
        returning = parse_leg(legs[1], request.destination, origin)
        flight.update({f"return_{field}": value for field, value in returning.items()})
    booking_url = card.get("booking_url")
    if booking_url and urlsplit(booking_url).hostname == "www.ca.kayak.com":
        flight["booking_url"] = booking_url
    return flight


async def extract_flights(working_page, request: SearchRequest, origin: str,
                          metadata: dict | None = None) -> list[dict]:
    page = working_page.page
    started = monotonic()
    deadline = started + MAX_WAIT_SECONDS
    signature = None
    stable_since = first_prices = None
    while monotonic() < deadline:
        snapshot = await page.evaluate(READ_DOM)
        if snapshot["blocked"]:
            raise ValueError("KAYAK denied browser access.")
        try:
            validate_settings(snapshot, request, origin)
            flights = [parse_card(card, request, origin) for card in snapshot["cards"]]
        except ValueError:
            # Loading placeholders/partially rendered settings are not usable offers.
            signature = stable_since = None
            await asyncio.sleep(1)
            continue
        if snapshot["noResults"] and not flights:
            if metadata is not None:
                metadata["results_complete"] = True
            return []
        eligible = sorted((f for f in flights if request.budget is None or f["price"] <= request.budget),
                          key=lambda f: f["price"])[:8]
        # Booking URLs carry session tokens; ignore them when measuring stability.
        current = json.dumps([{k: v for k, v in f.items() if k != "booking_url"} for f in eligible], sort_keys=True)
        now = monotonic()
        if flights:
            first_prices = first_prices if first_prices is not None else now
            if current != signature:
                signature, stable_since = current, now
            # The loading banner can regress. Stable cards are usable after a
            # bounded settling period, but explicitly mark that source as partial.
            if now - stable_since >= SETTLE_SECONDS and (snapshot["resultsReady"] or now-first_prices >= 20):
                if metadata is not None:
                    metadata["results_complete"] = snapshot["resultsReady"]
                    metadata["timings_seconds"]["cards_settled"] = round(now-started, 3)
                    if not snapshot["resultsReady"]:
                        metadata["warning"] = "Stable fares captured while KAYAK still reported loading."
                return eligible
        await asyncio.sleep(1)
    raise ValueError("KAYAK did not provide stable, matching flight cards before the deadline.")
