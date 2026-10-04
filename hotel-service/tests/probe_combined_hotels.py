"""Explicit live probe: fresh Airbnb + saved Booking.com, or both fresh in parallel.

Run from hotel-service: ../.venv/bin/python tests/probe_combined_hotels.py
No Lambda invocation, booking, Mongo writes, callbacks, or recording uploads.
"""

import asyncio
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
import airbnb
import booking
from delivery import wait_for_recording
from request import SearchRequest

RESULTS_PER_SOURCE = 8


def shortlist(hotels):
    """Keep the eight cheapest valid offers from the loaded batch, per source."""
    return sorted(hotels, key=lambda hotel: hotel['total_price'])[:RESULTS_PER_SOURCE]


async def fresh_source(adapter, request, key, started):
    from skyvern import Skyvern

    source_started = perf_counter()
    client = Skyvern(api_key=key, timeout=90)
    browser = None
    result = {'website': adapter.WEBSITE, 'hotels': [], 'status': 'failed',
              'error': None, 'recordings': [], 'replay_url': None,
              'skyvern_browser_session_id': None, 'timings': {}}
    try:
        async with asyncio.timeout(150):
            browser = await client.launch_cloud_browser(timeout=15)
            result['skyvern_browser_session_id'] = browser.browser_session_id
            result['timings']['browser_ready_seconds'] = round(perf_counter() - source_started, 2)
            page = (await browser.get_working_page()).page
            await adapter.navigate(page, request)
            extracted = await adapter.extract_hotels(page, request)
            result['eligible_count'] = len(extracted)
            result['hotels'] = [{**hotel, 'source': adapter.WEBSITE}
                                for hotel in shortlist(extracted)]
            result['status'] = 'complete'
    except Exception as exc:
        result['error'] = {'code': 'SEARCH_FAILED', 'message': str(exc)}
    finally:
        result['timings']['search_seconds'] = round(perf_counter() - source_started, 2)
        result['timings']['results_ready_at_seconds'] = round(perf_counter() - started, 2)
        if browser is not None:
            cleanup_started = perf_counter()
            try:
                await asyncio.wait_for(browser.close(), timeout=30)
            except Exception as exc:
                result['cleanup_error'] = str(exc)
            result['timings']['cleanup_seconds'] = round(perf_counter() - cleanup_started, 2)
    return client, result


async def collect_recording(client, source):
    if not source['skyvern_browser_session_id']:
        return
    started = perf_counter()
    try:
        async with asyncio.timeout(60):
            recording = await wait_for_recording(client, [source])
            source['replay_url'] = recording['url']
    except Exception as exc:
        source['recording_error'] = {'code': 'RECORDING_NOT_AVAILABLE',
                                     'message': str(exc) or type(exc).__name__}
    source['timings']['recording_lookup_seconds'] = round(perf_counter() - started, 2)


async def fresh_parallel(request_data, request, key):
    started = perf_counter()
    created = datetime.now(timezone.utc).isoformat()
    sources_with_clients = await asyncio.gather(
        fresh_source(booking, request, key, started),
        fresh_source(airbnb, request, key, started),
    )
    sources = [source for _, source in sources_with_clients]
    results_ready = max(source['timings']['results_ready_at_seconds'] for source in sources)
    cleanup_finished = round(perf_counter() - started, 2)
    await asyncio.gather(*(collect_recording(client, source) for client, source in sources_with_clients))
    hotels = sorted([hotel for source in sources for hotel in source['hotels']],
                    key=lambda hotel: hotel['total_price'])
    succeeded = sum(source['status'] == 'complete' for source in sources)
    status = 'complete' if succeeded == 2 else 'partially_complete' if succeeded else 'failed'
    now = datetime.now(timezone.utc).isoformat()
    record = {
        'session_id': request.session_id, 'search_id': str(uuid4()), 'status': status,
        'website': 'multiple', 'request': {key: value for key, value in request_data.items()
                                        if key not in ('session_id', 'callback_url')},
        'hotels': hotels, 'origins': sources,
        'error': None if succeeded else {'code': 'SEARCH_FAILED', 'message': 'Both sources failed.'},
        'created_at': created, 'updated_at': now,
        # Per-source recordings are in origins; a singular replay must not imply both browsers.
        'skyvern_browser_session_id': None, 'live_view_url': None, 'replay_url': None,
        'recordings': [], 'recording_url': None, 'recording_error': None, 'delivery_error': None,
        'probe': {'mode': 'fresh_parallel', 'not_a_saved_production_search': True,
                  'results_per_source': RESULTS_PER_SOURCE,
                  'results_ready_seconds': results_ready, 'cleanup_finished_seconds': cleanup_finished,
                  'including_recording_lookup_seconds': round(perf_counter() - started, 2)},
    }
    validate_hotels(hotels)
    output = ROOT / 'artifacts/combined-fresh-parallel-probe.json'
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'status': status, 'combined_count': len(hotels), 'timings': record['probe'],
                      'sources': [{key: source[key] for key in ('website', 'status', 'timings', 'error')}
                                  | {'count': len(source['hotels']), 'recording_count': len(source['recordings']),
                                     'recording_error': source.get('recording_error')}
                                  for source in sources], 'output': str(output)}, indent=2), flush=True)
    if succeeded != 2:
        raise RuntimeError('Fresh parallel probe did not complete both sources; inspect the saved result.')


def validate_hotels(hotels):
    for hotel in hotels:
        assert isinstance(hotel['name'], str) and hotel['name']
        assert isinstance(hotel['url'], str) and hotel['url'].startswith('https://')
        assert isinstance(hotel['total_price'], (float, int)) and hotel['total_price'] > 0
        assert hotel['currency'] == 'CAD'
        assert hotel['rating'] is None or 0 <= hotel['rating'] <= 10
        assert hotel['review_count'] is None or isinstance(hotel['review_count'], int)
        assert isinstance(hotel['price_note'], (str, type(None)))


async def main():
    from skyvern import Skyvern
    import skyvern.library.skyvern_browser

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fresh', action='store_true', help='Search both sites fresh in parallel and collect recording links')
    args = parser.parse_args()
    request_data = json.loads((ROOT / 'artifacts/lambda-retest-request.json').read_text())
    request = SearchRequest.parse(request_data)
    key = dotenv_values(ROOT / '.env').get('SKYVERN_API_KEY')
    if not key:
        raise RuntimeError('Hotel Skyvern key is missing')
    if args.fresh:
        await fresh_parallel(request_data, request, key)
        return
    saved = json.loads((ROOT / 'artifacts/lambda-retest-response.json').read_text())
    booking_record = json.loads(saved['body'])
    assert saved['statusCode'] == 200 and booking_record['status'] == 'complete'
    for key, value in booking_record['request'].items():
        assert request_data.get(key) == value, f'Saved Booking.com input differs: {key}'

    browser = None
    try:
        browser = await Skyvern(api_key=key, timeout=90).launch_cloud_browser(timeout=15)
        page = (await browser.get_working_page()).page
        async with asyncio.timeout(100):
            await airbnb.navigate(page, request)
            airbnb_hotels = shortlist(await airbnb.extract_hotels(page, request))
        assert airbnb_hotels, 'Airbnb returned no normalized listings'
        booking_hotels = [{**hotel, 'source': 'booking_com'} for hotel in shortlist(booking_record['hotels'])]
        hotels = sorted(booking_hotels + airbnb_hotels, key=lambda hotel: hotel['total_price'])
        validate_hotels(hotels)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            'session_id': request.session_id, 'search_id': str(uuid4()), 'status': 'complete',
            'website': 'multiple', 'request': booking_record['request'], 'hotels': hotels,
            'error': None, 'created_at': now, 'updated_at': now,
            'skyvern_browser_session_id': None, 'live_view_url': None, 'replay_url': None,
            'recordings': [], 'recording_url': None, 'recording_error': None, 'delivery_error': None,
            'probe': {'results_per_source': RESULTS_PER_SOURCE,
                      'booking_snapshot_created_at': booking_record['created_at'],
                      'airbnb_fetched_at': now, 'not_a_saved_production_search': True},
        }
        output = ROOT / 'artifacts/combined-airbnb-probe.json'
        output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
        summary = {'booking_count': len(booking_hotels), 'airbnb_count': len(airbnb_hotels),
                   'combined_count': len(hotels), 'cheapest': hotels[0], 'output': str(output)}
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    finally:
        if browser is not None:
            await asyncio.wait_for(browser.close(), timeout=30)


if __name__ == '__main__':
    asyncio.run(main())
