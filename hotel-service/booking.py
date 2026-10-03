"""Booking.com navigation and DOM extraction; no AI extraction."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from request import SearchRequest

WEBSITE = "booking_com"
RESULTS_HEADING = re.compile(r"(?:properties|property) found", re.I)
NO_RESULTS = re.compile(r"\b(?:0|no) properties found\b", re.I)


def search_url(request: SearchRequest) -> str:
    return "https://www.booking.com/searchresults.en-us.html?" + urlencode({
        "ss": request.destination, "checkin": request.check_in.isoformat(),
        "checkout": request.check_out.isoformat(), "group_adults": request.adults,
        "group_children": 0, "no_rooms": 1, "selected_currency": "CAD",
    })


async def navigate(page, request: SearchRequest) -> None:
    await page.goto(search_url(request), wait_until="domcontentloaded")
    # Booking.com changes this field's accessible label between page variants.
    await page.locator('input[name="ss"]').wait_for()
    heading = page.get_by_role("heading", level=1, include_hidden=True).filter(has_text=RESULTS_HEADING)
    await heading.wait_for()
    dismiss = page.get_by_role("button", name="Dismiss sign-in info.", exact=True)
    if await dismiss.is_visible():
        await dismiss.click()
    # Read-only checks still work if the optional sign-in overlay appears late.
    await page.get_by_role("button", name="Prices in Canadian Dollar CAD", exact=True, include_hidden=True).first.wait_for()
    occupancy = page.get_by_role("button", name=re.compile(r"^Number of travelers and rooms"), include_hidden=True)
    label = await occupancy.get_attribute("aria-label")
    if not re.search(rf"selected: {request.adults} adults? · 0 children · 1 room", label or ""):
        # Booking.com can adjust impossible occupancy; never label those prices as requested.
        raise ValueError("Booking.com changed the requested occupancy.")
    query = parse_qs(urlsplit(page.url).query)
    for key, expected in (("checkin", request.check_in.isoformat()), ("checkout", request.check_out.isoformat())):
        if query.get(key) != [expected]:
            raise ValueError("Booking.com changed the requested dates.")


def parse_card(raw: dict, request: SearchRequest) -> dict | None:
    if "Sign in for this members-only price" in raw["text"]:
        return None
    match = re.search(r"(?:Current price|Price)\s+CAD\s*([\d,]+(?:\.\d+)?)", raw["price"])
    if not raw["name"] or not match or not raw["url"]:
        raise ValueError("Booking.com card is missing its name, link, or CAD price.")
    nights = (request.check_out - request.check_in).days
    if not re.search(rf"\b{nights} nights?\b", raw["stay"]) or not re.search(rf"\b{request.adults} adults?\b", raw["stay"]):
        raise ValueError("Booking.com card price doesn't match the stay and adults.")
    link = urlsplit(raw["url"])
    if link.hostname != "www.booking.com" or not link.path.startswith("/hotel/"):
        raise ValueError("Unexpected hotel URL.")
    query = parse_qs(link.query)
    for key, expected in (("checkin", request.check_in.isoformat()),
                          ("checkout", request.check_out.isoformat()),
                          ("group_adults", str(request.adults)), ("no_rooms", "1")):
        if query.get(key) != [expected]:
            raise ValueError("Booking.com hotel link doesn't match the requested stay.")
    # Remove tracking/session identifiers while retaining parameters needed for this stay.
    url = urlunsplit(("https", "www.booking.com", link.path, urlencode({
        "checkin": request.check_in.isoformat(), "checkout": request.check_out.isoformat(),
        "group_adults": request.adults, "group_children": 0, "no_rooms": 1,
        "selected_currency": "CAD",
    }), ""))
    review = re.search(r"Scored ([\d.]+),.*?([\d,]+) reviews?", raw["review"])
    return {
        "name": raw["name"], "url": url,
        "total_price": float(match.group(1).replace(",", "")), "currency": "CAD",
        "rating": float(review.group(1)) if review else None,
        "review_count": int(review.group(2).replace(",", "")) if review else None,
        "price_note": raw["taxes"] or None,
    }


async def extract_hotels(page, request: SearchRequest) -> list[dict]:
    cards = page.get_by_test_id("property-card")
    heading = await page.get_by_role("heading", level=1, include_hidden=True).inner_text()
    if NO_RESULTS.search(heading):
        return []
    await cards.first.wait_for()
    # Snapshot the initially loaded batch. No pagination or scrolling/infinite loading.
    raw = await cards.evaluate_all(r"""cards => cards.map(card => {
        const text = id => card.querySelector(`[data-testid="${id}"]`)?.textContent.trim() || '';
        return {
            name: text('title'),
            url: card.querySelector('[data-testid="title-link"]')?.href || '',
            // Booking.com can reuse the price test ID for nightly and stay prices.
            // The explicit 'Price CAD …' / 'Current price CAD …' text identifies the displayed stay total.
            price: Array.from(card.querySelector('[data-testid="availability-rate-information"]')?.childNodes || [])
                .map(node => node.textContent).join(' '),
            stay: text('price-for-x-nights'),
            taxes: text('taxes-and-charges'),
            review: card.querySelector('[data-testid="review-score-link"]')?.getAttribute('aria-label') || '',
            text: card.innerText,
        };
    })""")
    hotels = []
    for card in raw:
        hotel = parse_card(card, request)
        if hotel is not None and (request.budget is None or hotel["total_price"] <= request.budget):
            hotels.append(hotel)
    return sorted(hotels, key=lambda hotel: hotel["total_price"])
