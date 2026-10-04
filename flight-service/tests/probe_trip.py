"""Bounded, read-only Trip.com speed/DOM probe in fresh Skyvern browsers.

Run from flight-service: ../.venv/bin/python tests/probe_trip.py --runs 2
Stops the batch on access denial or a verification challenge. Never books.
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
URL = ('https://ca.trip.com/flights/showfarefirst?dcity=yvr&acity=tyo'
       '&dairport=yvr&aairport=nrt&ddate=2027-04-10&rdate=2027-04-20'
       '&triptype=rt&class=y&quantity=1&childqty=0&babyqty=0'
       '&curr=CAD&locale=en-CA&sort=price')
INSPECT = '''() => ({
 url:location.href,title:document.title,text:document.body.innerText.slice(0,35000),
 settings:{roundTrip:document.querySelector('[data-testid="flightType_RT"]')?.getAttribute('aria-checked'),
  departure:document.querySelector('[data-testid="search_date_depart0"]')?.getAttribute('data-date'),
  return:document.querySelector('[data-testid="search_date_return0"]')?.getAttribute('data-date'),
  origin:document.querySelector('[data-testid="search_city_from0_wrapper"]')?.innerText,
  destination:document.querySelector('[data-testid="search_city_to0_wrapper"]')?.innerText,
  passengers:document.querySelector('[data-testid="passengers_total_num"]')?.innerText,
  currency:document.querySelector('[aria-label="Language/Currency"]')?.innerText},
 cards:[...document.querySelectorAll('[data-testid^="u-flight-card-"]')]
  .filter(e=>e.getBoundingClientRect().width && e.querySelector('[data-testid="u_select_btn"]'))
  .slice(0,8).map(e=>({testid:e.getAttribute('data-testid'),
   text:e.innerText,airline:e.querySelector('[data-testid="flights-name"], .airline-info .flight-name')?.innerText,
   price:e.querySelector('[data-testid^="flight_price_"]')?.innerText,
   priceValue:e.querySelector('[data-testid^="flight_price_"]')?.getAttribute('data-price'),
   priceBasis:e.querySelector('[data-testid="flight-price-tag"]')?.innerText,
   duration:e.querySelector('[data-testid="flightInfoDuration"], .travel-duration [role="group"]')?.innerText,
   times:[...e.querySelectorAll('[data-testid^="flight-time-"]')].map(t=>({text:t.innerText,id:t.getAttribute('data-testid')})),
   airports:[...e.querySelectorAll('[role="textbox"][aria-label]')].map(t=>t.getAttribute('aria-label')),
   stops:e.querySelector('[data-testid="stopInfoText"], .stop-city-text')?.innerText,
   stopCountText:e.querySelector('.stop-num')?.innerText,stopCount:e.querySelectorAll('[data-testid="stopDot"]').length,
   html:e.outerHTML.slice(0,24000)}))
})'''
BLOCKED = re.compile(r'access denied|verify you are human|verification required|security verification|unusual traffic|captcha|are you a robot|trip.com.verification', re.I)


def fares(snapshot):
    # Scope to actual flight cards; calendar/filter prices aren't flight offers.
    return [c for c in snapshot['cards'] if c.get('airline') and c.get('duration')
            and len(c['times']) == 2 and re.fullmatch(r'CAD\s*[\d,]+(?:\.\d{2})?', c.get('price') or '')]


async def probe(number, inspect_return):
    key = (dotenv_values(ROOT / '.env').get('SKYVERN_API_KEY')
           or dotenv_values(ROOT.parent / 'hotel-service/.env').get('SKYVERN_API_KEY'))
    if not key:
        raise RuntimeError('Skyvern key is missing')
    started = perf_counter()
    client = Skyvern(api_key=key, timeout=90)
    browser = None
    report = {'run':number, 'requested_url':URL, 'timings':{}, 'snapshots':[]}
    snapshot = {}
    try:
        async with asyncio.timeout(150):
            browser = await client.launch_cloud_browser(timeout=15)
            report['browser_session_id'] = browser.browser_session_id
            report['timings']['browser_ready_seconds'] = round(perf_counter()-started,2)
            page = (await browser.get_working_page()).page
            response = await page.goto(URL, wait_until='domcontentloaded', timeout=60000)
            report['http_status'] = response.status if response else None
            report['timings']['dom_loaded_seconds'] = round(perf_counter()-started,2)
            deadline = perf_counter()+65
            signature = None
            stable_since = None
            while True:
                snapshot = await page.evaluate(INSPECT)
                now = perf_counter()
                if report['http_status'] in (403,429) or BLOCKED.search(snapshot['text']+' '+snapshot['title']):
                    report['outcome'] = 'blocked'
                    break
                cards = fares(snapshot)
                current = [(c['airline'],c['price'],c['duration'],c['times'],c['stops']) for c in cards]
                if cards and 'first_fare_cards_seconds' not in report['timings']:
                    report['timings']['first_fare_cards_seconds'] = round(now-started,2)
                if len(cards)>=8 and 'eight_fare_cards_seconds' not in report['timings']:
                    report['timings']['eight_fare_cards_seconds'] = round(now-started,2)
                if current != signature:
                    signature,stable_since = current,now
                    report['snapshots'].append({'at_seconds':round(now-started,2),**snapshot})
                if len(cards)>=8 and now-stable_since>=5:
                    report['outcome'] = 'eight_outbound_fares_stable'
                    report['timings']['stable_top8_seconds'] = round(now-started,2)
                    break
                if now>=deadline:
                    report['outcome'] = 'fares_not_stable_before_deadline' if cards else 'no_fares_before_deadline'
                    break
                await page.wait_for_timeout(1000)
            report['outbound'] = snapshot
            expected = {'roundTrip':'true','departure':'2027-04-10','return':'2027-04-20',
                        'origin':'Vancouver (YVR) · Vancouver International Airport',
                        'destination':'Tokyo (NRT) · Narita International Airport',
                        'passengers':'1 adult · Economy','currency':'CAD'}
            report['settings_match'] = all(' '.join(snapshot.get('settings',{}).get(k,'').replace('·',' ').split()) == ' '.join(v.replace('·',' ').split()) for k,v in expected.items())
            if inspect_return and report['outcome']=='eight_outbound_fares_stable' and report['settings_match']:
                report['selected_outbound'] = fares(snapshot)[0]
                select_started = perf_counter()
                await page.get_by_test_id(report['selected_outbound']['testid']).get_by_role('button',name='Select this fare',exact=True).click(timeout=10000)
                return_deadline = perf_counter()+40
                while True:
                    snapshot = await page.evaluate(INSPECT)
                    if BLOCKED.search(snapshot['text']+' '+snapshot['title']):
                        report['outcome'] = 'blocked'
                        break
                    if re.search(r'2\.\s*(?:Returns|Return)',snapshot['text'],re.I) and fares(snapshot):
                        report['timings']['return_fares_seconds'] = round(perf_counter()-started,2)
                        report['timings']['return_selection_wait_seconds'] = round(perf_counter()-select_started,2)
                        report['return_outcome'] = 'return_fares_found'
                        break
                    if perf_counter()>=return_deadline:
                        report['return_outcome'] = 'no_return_fares_before_deadline'
                        break
                    await page.wait_for_timeout(1000)
                report['return'] = snapshot
            (ROOT/f'artifacts/playwright-trip-{number}.html').write_text(await page.content())
            await page.screenshot(path=str(ROOT/f'artifacts/playwright-trip-{number}.png'),full_page=False)
    except Exception as exc:
        report['outcome'] = 'error'
        report['error'] = f'{type(exc).__name__}: {exc}'.replace(key,'[REDACTED]')
    finally:
        report['timings']['search_finished_seconds'] = round(perf_counter()-started,2)
        if browser:
            try:
                await asyncio.wait_for(browser.close(),timeout=20)
            except Exception as exc:
                report['cleanup_error'] = str(exc).replace(key,'[REDACTED]')
        try:
            await asyncio.wait_for(client.aclose(),timeout=10)
        except Exception as exc:
            report['client_cleanup_error'] = str(exc).replace(key,'[REDACTED]')
        output = ROOT/f'artifacts/playwright-trip-{number}.json'
        output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'run':number,'outcome':report['outcome'],'http_status':report.get('http_status'),
            'settings_match':report.get('settings_match'),'timings':report['timings'],
            'outbound_prices':[c['price'] for c in fares(report['outbound'])] if 'outbound' in report else [],
            'return_outcome':report.get('return_outcome'),'return_text':report.get('return',{}).get('text','')[-11000:],
            'error':report.get('error'),'output':str(output)},indent=2),flush=True)
    return report['outcome']


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=int,default=2,choices=(1,2,3))
    parser.add_argument('--start-run',type=int,default=2)
    args = parser.parse_args()
    (ROOT/'artifacts').mkdir(exist_ok=True)
    for number in range(args.start_run,args.start_run+args.runs):
        if await probe(number,inspect_return=(number==args.start_run)) == 'blocked':
            break


if __name__=='__main__':
    asyncio.run(main())
