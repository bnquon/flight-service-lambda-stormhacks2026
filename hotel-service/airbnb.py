"""Read-only Airbnb search adapter using the existing hotel result contract."""

from contextlib import contextmanager
import re
from time import monotonic
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from request import SearchRequest
from search_logging import log_step

WEBSITE = "airbnb"
CARDS = '[data-testid="card-container"]'


def search_url(request: SearchRequest) -> str:
    destination = quote(request.destination.replace(", ", "--").replace(" ", "-"), safe="-")
    return f"https://www.airbnb.ca/s/{destination}/homes?" + urlencode({
        "checkin": request.check_in.isoformat(), "checkout": request.check_out.isoformat(),
        "adults": request.adults, "currency": request.currency,
    })


@contextmanager
def navigation_phase(timings: dict | None, phase: str):
    started = monotonic()
    try:
        with log_step(phase):
            yield
    finally:
        if timings is not None:
            timings[phase] = round(monotonic() - started, 3)


async def navigate(page, request: SearchRequest, timings: dict | None = None) -> None:
    with navigation_phase(timings, "navigation.open_airbnb"):
        response = await page.goto(search_url(request), wait_until="domcontentloaded", timeout=45000)
        if response and response.status >= 400:
            raise ValueError(f"Airbnb returned HTTP {response.status}.")
    with navigation_phase(timings, "navigation.wait_cards"):
        await page.locator(CARDS).first.wait_for(timeout=35000)
    with navigation_phase(timings, "navigation.validate_settings"):
        query = parse_qs(urlsplit(page.url).query)
        for key, expected in (("checkin", request.check_in.isoformat()),
                              ("checkout", request.check_out.isoformat()), ("adults", str(request.adults))):
            if query.get(key) != [expected]:
                raise ValueError("Airbnb changed the requested stay.")
        location = page.get_by_role("button", name=re.compile(r"^Location"))
        if request.destination.split(",")[0].casefold() not in (await location.inner_text()).casefold():
            raise ValueError("Airbnb changed the requested destination.")
        guests = page.get_by_role("button", name=re.compile(r"^Guests"))
        if not re.search(rf"\b{request.adults}\s+guests?\b", await guests.inner_text()):
            raise ValueError("Airbnb changed the requested guest count.")
    # Hydration first exposes one card, then the rest. Require a stable priced batch
    # rather than returning as soon as the first listing appears.
    with navigation_phase(timings, "navigation.wait_stable_prices"):
        await page.wait_for_function(r"""selector => {
            const cards = [...document.querySelectorAll(selector)];
            const ready = cards.length > 0 && cards.every(card =>
                /\$[\d,]+(?:\.\d+)?\s*CAD\s+total/.test(card.textContent) ||
                /unavailable/i.test(card.textContent));
            const signature = cards.map(card => card.textContent).join('|');
            const state = window.__fareAirbnbProbe;
            if (!ready || !state || state.signature !== signature) {
                window.__fareAirbnbProbe = {signature, since: performance.now()};
                return false;
            }
            return performance.now() - state.since >= 5000;
        }""", arg=CARDS, timeout=35000)


def parse_card(raw: dict, request: SearchRequest) -> dict | None:
    price = re.search(r"\$([\d,]+(?:\.\d+)?)\s*CAD\s+total", raw["price"])
    if not price:
        if re.search(r"unavailable", raw["text"], re.I):
            return None
        raise ValueError("Airbnb card has no explicit CAD stay total.")
    if not raw["name"]:
        raise ValueError("Airbnb card has no listing name.")
    link = urlsplit(raw["url"])
    if link.scheme != "https" or link.hostname != "www.airbnb.ca" or not re.fullmatch(r"/rooms/\d+", link.path):
        raise ValueError("Unexpected Airbnb listing URL.")
    query = parse_qs(link.query)
    for key, expected in (("check_in", request.check_in.isoformat()),
                          ("check_out", request.check_out.isoformat()), ("adults", str(request.adults))):
        if query.get(key) != [expected]:
            raise ValueError(f"Airbnb listing {key} doesn't match the requested stay: expected {expected}, got {query.get(key)}.")
    total = float(price.group(1).replace(",", ""))
    if total <= 0:
        raise ValueError("Airbnb total must be positive.")
    if request.budget is not None and total > request.budget:
        return None
    review = re.search(r"([\d.]+) out of 5 average rating, ([\d,]+) reviews?", raw["text"])
    original_rating = float(review.group(1)) if review else None
    if original_rating is not None and not 0 <= original_rating <= 5:
        raise ValueError("Invalid Airbnb rating.")
    return {
        "name": raw["name"],
        "url": urlunsplit(("https", "www.airbnb.ca", link.path, urlencode({
            "check_in": request.check_in.isoformat(), "check_out": request.check_out.isoformat(),
            "adults": request.adults, "currency": "CAD",
        }), "")),
        "total_price": total, "currency": "CAD",
        # Existing dashboard renders /10. This is a scale conversion, not equivalent review systems.
        "rating": round(original_rating * 2, 2) if original_rating is not None else None,
        "review_count": int(review.group(2).replace(",", "")) if review else None,
        "price_note": "Displayed Airbnb stay total; checkout price and fees not verified.",
        "source": WEBSITE, "property_type": raw["property_type"],
        "original_rating": original_rating, "original_rating_scale": 5,
    }


async def extract_hotels(page, request: SearchRequest) -> list[dict]:
    raw = await page.locator(CARDS).evaluate_all("""cards => cards.map(card => {
        const text = id => card.querySelector(`[data-testid="${id}"]`)?.textContent.trim() || '';
        return {
            name: text('listing-card-name'),
            property_type: text('listing-card-title'),
            price: text('price-availability-row'),
            url: card.querySelector('a[href*="/rooms/"]')?.href || '',
            text: card.textContent,
        };
    })""")
    hotels = {}
    for card in raw:
        hotel = parse_card(card, request)
        if hotel is not None:
            hotels[hotel["url"]] = hotel
    return sorted(hotels.values(), key=lambda hotel: hotel["total_price"])
