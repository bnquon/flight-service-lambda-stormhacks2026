"""Separate, bounded Google Flights navigation test in a Skyvern cloud browser."""

import asyncio
import json
import os
import re
import time

from skyvern import Skyvern

from google_flights_navigation import navigate
from request import SearchRequest


async def inspect_controls(page) -> None:
    controls = await page.locator(
        'input:visible, [role="option"]:visible, [role="dialog"]:visible, button:visible'
    ).evaluate_all("""elements => elements.map(e => ({
        tag: e.tagName, role: e.getAttribute('role'), label: e.getAttribute('aria-label'),
        value: e.value, text: (e.innerText || '').slice(0, 200)
    }))""")
    print("DOM controls:", json.dumps(controls, indent=2), flush=True)


async def main() -> None:
    key = os.environ["SKYVERN_API_KEY"]
    browser = await Skyvern(api_key=key).launch_cloud_browser(timeout=10)
    started = time.monotonic()
    print("Watch live:", browser.app_url, flush=True)
    try:
        page = (await browser.get_working_page()).page
        async with asyncio.timeout(150):
            request = SearchRequest.parse({
                "session_id": "playwright-test", "origins": ["YVR"], "destination": "NRT",
                "departure_date": "2027-04-10", "return_date": "2027-04-20",
            })
            await navigate(page, request, "YVR")
            await page.get_by_text(re.compile(r"^CA\$[\d,]+(?:\.\d{2})?$")).first.wait_for(timeout=45000)
            print("PASS: selected YVR/NRT, confirmed calendar dates, and reached flight results.", flush=True)
            print("Results URL:", page.url, flush=True)
            print("Results page text:", (await page.locator("body").inner_text())[:5000], flush=True)
    except Exception as exc:
        print("FAIL:", type(exc).__name__, str(exc).replace(key, "[REDACTED]"), flush=True)
        if "page" in locals():
            await inspect_controls(page)
        raise SystemExit(1)
    finally:
        print(f"Navigation elapsed: {time.monotonic() - started:.1f}s", flush=True)
        await browser.close()
        session = await browser.skyvern.get_browser_session(browser.browser_session_id)
        print(f"Skyvern recordings available: {len(session.recordings or [])}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
