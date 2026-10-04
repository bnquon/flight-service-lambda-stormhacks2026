"""Direct Playwright navigation using controls inspected on Google Flights."""

import re
from urllib.parse import urlencode

from request import SearchRequest
from search_logging import log_event, log_step


async def select_airport(page, label: str, code: str) -> None:
    await page.get_by_role("combobox", name=label, exact=True).click()
    # The summary field opens a dialog whose actual input is labeled "Where else?".
    dialog = page.get_by_role("dialog", name=re.compile(r"Enter your (origin|destination)"))
    await dialog.get_by_role("combobox").fill(code)
    await dialog.get_by_role("option", name=re.compile(rf"\b{code}\b")).first.click()


async def navigate(page, request: SearchRequest, origin: str) -> None:
    page.set_default_timeout(15000)
    with log_step("navigation.open_google_flights"):
        await page.goto("https://www.google.com/travel/flights?" + urlencode({"hl": "en", "curr": request.currency}))
    with log_step("navigation.select_origin"):
        await select_airport(page, "Where from?", origin)
    with log_step("navigation.select_destination"):
        await select_airport(page, "Where to?", request.destination)
    log_event("navigation.trip_type", "started", trip_type=request.trip_type)
    if request.trip_type == "one_way":
        await page.get_by_role("combobox").filter(has_text="Round trip").click()
        await page.get_by_role("option", name="One way", exact=True).click()

    log_event("navigation.dates", "started")
    await page.get_by_role("textbox", name="Departure", exact=True).click()
    await page.get_by_role("textbox", name="Departure", exact=True).last.fill(request.departure_date.strftime("%m/%d/%Y"))
    await page.get_by_role("textbox", name="Departure", exact=True).last.press("Tab")
    if request.return_date:
        await page.get_by_role("textbox", name="Return", exact=True).last.fill(request.return_date.strftime("%m/%d/%Y"))
        await page.get_by_role("textbox", name="Return", exact=True).last.press("Tab")
    done = page.get_by_role("button", name=re.compile(r"^Done\."))
    summary = await done.get_attribute("aria-label")
    for travel_date in (request.departure_date, request.return_date):
        if travel_date and f"{travel_date.strftime('%B')} {travel_date.day}, {travel_date.year}" not in summary:
            raise ValueError("Calendar did not accept the requested dates.")
    await done.click()
    log_event("navigation.dates", "completed")
    with log_step("navigation.submit"):
        await page.get_by_role("button", name="Search", exact=True).click()
    log_event("navigation.wait_results", "started")
    # Wait on page state, not a fixed sleep. Unexpected page variants fail visibly.
    no_results = page.get_by_text(re.compile(r"^(No flights|No results).*", re.I))
    results = page.get_by_text(re.compile(r"^(Top|Best|Other) (departing )?flights$", re.I))
    await results.or_(no_results).first.wait_for(timeout=45000)
    await page.get_by_text(re.compile(r"^\d+ results returned\.$")).or_(no_results).first.wait_for(
        state="attached", timeout=45000,
    )
    # Verify route/dates from extracted page settings, not Google's internal URL encoding.
    await page.get_by_role("button", name=f"Currency {request.currency}", exact=True).wait_for()
    log_event("navigation.wait_results", "completed")
    # TODO: verify no-results and additional currencies with real searches before production.
