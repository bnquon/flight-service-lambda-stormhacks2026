"""Bounded live KAYAK DOM probe using fresh Skyvern browsers.

Run from flight-service: ../.venv/bin/python tests/probe_kayak.py --runs 2
Saves DOM evidence locally; does not call Lambda or persist search results.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path
from time import perf_counter

from dotenv import dotenv_values
from skyvern import Skyvern

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.ca.kayak.com/flights/YVR-NRT/2027-04-10/2027-04-20?sort=price_a&currency=CAD"
INSPECT = """() => ({
  url: location.href, title: document.title,
  resultsReady: document.body.innerText.includes('Results ready.'),
  text: document.body.innerText.slice(0, 30000),
  controls: Array.from(document.querySelectorAll('input,button,[role="button"],[role="combobox"]'))
    .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().height)
    .slice(0,100).map(e => ({tag:e.tagName,role:e.getAttribute('role'),
      label:e.getAttribute('aria-label'),testid:e.getAttribute('data-testid'),
      name:e.getAttribute('name'),value:e.value,text:e.innerText?.slice(0,250)})),
  cards: Array.from(document.querySelectorAll('.nrc6, [data-testid="result-card"], [data-resultid]'))
    .slice(0,12).map(e => ({tag:e.tagName,classes:e.className,
      text:e.innerText,html:e.outerHTML.slice(0,40000),
      airline:e.querySelector('.J0g6-operator-text')?.innerText,
      price:e.querySelector('.e2GB-price-text')?.innerText,
      bookingUrl:e.querySelector('a[href*="/book/flight"]')?.href,
      legs:Array.from(e.querySelectorAll('.hJSA-item')).map(leg => ({
        label:leg.querySelector('input[aria-label^="Leg "]')?.getAttribute('aria-label'),
        times:leg.querySelector('.VY2U .vmXl')?.innerText,
        stops:leg.querySelector('.JWEO .vmXl')?.innerText,
        duration:leg.querySelector('.xdW8 .vmXl')?.innerText
      }))})),
  testids: [...new Set(Array.from(document.querySelectorAll('[data-testid]'))
    .map(e => e.getAttribute('data-testid')))].slice(0,150)
})"""


async def probe(number):
    key = dotenv_values(ROOT / ".env").get("SKYVERN_API_KEY")
    if not key:
        key = dotenv_values(ROOT.parent / "hotel-service/.env").get("SKYVERN_API_KEY")
    if not key:
        raise RuntimeError("Skyvern key is missing")
    started = perf_counter()
    client = Skyvern(api_key=key, timeout=90)
    browser = None
    report = {"run": number, "requested_url": URL, "timings": {}, "snapshots": []}
    try:
        async with asyncio.timeout(120):
            browser = await client.launch_cloud_browser(timeout=15)
            report["browser_session_id"] = browser.browser_session_id
            report["live_view_url"] = browser.app_url
            report["timings"]["browser_ready_seconds"] = round(perf_counter() - started, 2)
            page = (await browser.get_working_page()).page
            response = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            report["http_status"] = response.status if response else None
            report["timings"]["dom_loaded_seconds"] = round(perf_counter() - started, 2)
            deadline = perf_counter() + 65
            last_text = None
            while True:
                snapshot = await page.evaluate(INSPECT)
                text = snapshot["text"]
                if text != last_text:
                    report["snapshots"].append({"at_seconds": round(perf_counter()-started,2), **snapshot})
                    last_text = text
                if snapshot["cards"] and any("$" in c["text"] for c in snapshot["cards"]):
                    report["outcome"] = "priced_cards"
                    report["timings"]["first_priced_cards_seconds"] = round(perf_counter()-started,2)
                    # Results keep changing after the first priced cards arrive.
                    signature, stable_since = None, perf_counter()
                    while perf_counter() < deadline:
                        current = await page.evaluate(INSPECT)
                        current_signature = [(c.get('airline'), c.get('price'), c.get('legs')) for c in current['cards'][:8]]
                        if current_signature != signature:
                            signature, stable_since = current_signature, perf_counter()
                        if current['resultsReady'] and 'site_results_ready_seconds' not in report['timings']:
                            report['timings']['site_results_ready_seconds'] = round(perf_counter()-started,2)
                        if current['resultsReady'] and perf_counter()-stable_since >= 8:
                            report['outcome'] = 'results_ready_and_top8_stable'
                            break
                        await page.wait_for_timeout(2000)
                    else:
                        report['outcome'] = 'priced_cards_not_settled_before_deadline'
                    report["settled"] = current
                    report["timings"]["settled_snapshot_seconds"] = round(perf_counter()-started,2)
                    report['normalized_flights'] = normalize(current['cards'][:8])
                    break
                if any(word in text.lower() for word in ("verify you are human", "access denied", "unusual traffic", "captcha", "confirm you're a human", "are you a robot")):
                    report["outcome"] = "blocked"
                    break
                if perf_counter() >= deadline:
                    report["outcome"] = "no_priced_cards_before_deadline"
                    break
                await page.wait_for_timeout(2000)
            await page.screenshot(path=str(ROOT / f"artifacts/playwright-kayak-{number}.png"), full_page=False)
    except Exception as exc:
        report["outcome"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}".replace(key, "[REDACTED]")
    finally:
        report["timings"]["search_finished_seconds"] = round(perf_counter()-started,2)
        if browser:
            try:
                await asyncio.wait_for(browser.close(), timeout=20)
            except Exception as exc:
                report["cleanup_error"] = str(exc).replace(key, "[REDACTED]")
        await asyncio.wait_for(client.aclose(), timeout=10)
        output = ROOT / f"artifacts/playwright-kayak-{number}.json"
        output.write_text(json.dumps(report, indent=2) + "\n")
        last = report.get("settled") or (report["snapshots"][-1] if report["snapshots"] else {})
        print(json.dumps({"run": number,"outcome":report["outcome"],
            "http_status":report.get("http_status"),"timings":report["timings"],
            "card_count":len(last.get("cards",[])),"title":last.get("title"),
            "normalized_count":len(report.get('normalized_flights',[])),
            "text_preview":last.get("text", "")[:1000],"error":report.get("error"),
            "output":str(output)},indent=2),flush=True)


def normalize(cards):
    flights = []
    for card in cards:
        price = re.fullmatch(r'C\$\s*([\d,]+(?:\.\d{2})?)', card.get('price') or '')
        legs = card.get('legs') or []
        if not price or len(legs) != 2 or not card.get('airline'):
            raise ValueError('Missing round-trip card fields')
        for index, leg in enumerate(legs):
            origin, destination = ('YVR', 'NRT') if index == 0 else ('NRT', 'YVR')
            if not re.search(rf', {origin} .+ - {destination} ', leg.get('label') or ''):
                raise ValueError('Card airport mismatch')
            if not leg.get('duration') or not leg.get('times'):
                raise ValueError('Missing leg times/duration')
        outbound = legs[0]
        stops = outbound.get('stops') or ''
        if stops.lower() in ('direct', 'nonstop'):
            stops_count = 0
        elif re.fullmatch(r'\d+ stops?', stops):
            stops_count = int(stops.split()[0])
        else:
            raise ValueError('Unrecognized stops')
        times = re.split(r'\s*[–−]\s*', outbound['times'])
        if len(times) != 2:
            raise ValueError('Unrecognized times')
        flights.append({'airline':card['airline'], 'origin':'YVR', 'destination':'NRT',
            'website':'kayak', 'currency':'CAD', 'price':float(price.group(1).replace(',','')),
            'outbound_departure_time_text':times[0], 'outbound_arrival_time_text':times[1],
            'outbound_duration_text':outbound['duration'], 'outbound_stops':stops_count,
            'booking_url':card.get('bookingUrl')})
    return flights


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=2, choices=(1,2,3))
    parser.add_argument("--start-run", type=int, default=1)
    args = parser.parse_args()
    (ROOT / "artifacts").mkdir(exist_ok=True)
    for number in range(args.start_run, args.start_run+args.runs):
        await probe(number)


if __name__ == "__main__":
    asyncio.run(main())
