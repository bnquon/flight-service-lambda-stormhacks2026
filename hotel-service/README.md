# Booking.com hotel search

Separate Python Lambda service using the same pinned dependencies as flights:
Skyvern Cloud provides the recorded browser, Playwright extracts the DOM, and
Mongo saves final JSON. No AI extraction, pagination, or new dependencies.

## Status

Request validation, Booking.com navigation/extraction, recording metadata,
Mongo storage, and Lambda deployment are implemented. Local and deployed searches
have passed with matching Mongo read-back. Stay prices use explicit total-price
labels rather than nightly prices.

The HTML POC now displays live browser frames through the local WebSocket bridge.
The hotel live feed still needs a real search check; flight live viewing uses the
same capture approach and has been confirmed by the user. Both POCs remain in
`frontend/`. Neither deployed Lambda has a public WebSocket endpoint.

See the [workspace overview](../README.md) for both service contracts and the
[Next.js integration guide](../docs/nextjs-integration.md) for a client example.
Each service has its own Docker build context.

## Configuration and local invocation

Run from this directory. The shared virtual environment is `../.venv`.
`hotel-service/.env` contains the existing Mongo connection and your separate
hotel Skyvern key. For fresh setup, fill in `SKYVERN_API_KEY` yourself. The flight
key remains in the root `.env`; don't source that file for hotel runs.
Hotel records use database `hotel_searches`, collection `searches`:
`MONGODB_DATABASE=hotel_searches` and `MONGODB_SEARCH_COLLECTION=searches`.
Existing records in the flight database haven't been migrated.

For a fresh checkout, copy `.env.example` to `.env` and fill in the values.
Install dependencies only if needed:

```bash
../.venv/bin/python -m pip install -r requirements.txt
```

Run a real search (uses paid Skyvern resources and saves to Mongo when configured):

```bash
set -a
source .env
set +a
../.venv/bin/python - <<'PY'
import json
from lambda_function import lambda_handler
with open("examples/lambda-request.json") as file:
    request = json.load(file)
response = lambda_handler(request, None)
print(response["statusCode"])
print(json.dumps(json.loads(response["body"]), indent=2))
PY
```

The handler also accepts API Gateway JSON-body events. Missing Skyvern configuration
returns 503; invalid requests return 400. A browser/extraction failure returns a
final record with `status: failed`. Unexpected Mongo failures propagate to logs.

## Request

| Field | Required / default | Accepted value |
| --- | --- | --- |
| `session_id` | Required | Non-empty string. |
| `destination` | Required | Non-empty destination text, preferably city and country. |
| `check_in` | Required | Valid `YYYY-MM-DD` date. |
| `check_out` | Required | Valid `YYYY-MM-DD`, strictly after check-in. |
| `adults` | Required | Positive integer; no default. |
| `rooms` | Default 1 | Only integer 1. |
| `currency` | Default CAD | Only CAD. |
| `budget` | Optional / null | Positive finite maximum displayed total-stay price. |


```json
{
  "session_id": "hotel-test-123",
  "destination": "Tokyo, Japan",
  "check_in": "2027-04-10",
  "check_out": "2027-04-20",
  "adults": 2,
  "rooms": 1,
  "currency": "CAD",
  "budget": null
}
```

`session_id`, destination, dates, and adults are required. Check-out must be after
check-in. Rooms and currency default to 1 and CAD; other values aren't supported
in this slice. No children. Budget is optional and applies to the displayed price
for the entire stay and all adults, rather than per person or per night.
Use a destination including its country to reduce ambiguity.

## Results

Final JSON includes the request, IDs, status (`complete` or `failed`), timestamps,
`hotels`, `error`, `skyvern_browser_session_id`, `live_view_url`, `recordings`, and
`replay_url`. `complete` with `hotels: []` means no matching cards survived the
budget/member-price filter or Booking.com explicitly showed zero properties.

Each hotel is deliberately small. Example based on an inspected Tokyo card
(prices change; this is not a completed Skyvern/Lambda run):

```json
{
  "name": "remm Roppongi",
  "url": "https://www.booking.com/hotel/jp/remm-roppongi.html?checkin=2027-04-10&checkout=2027-04-20&group_adults=2&group_children=0&no_rooms=1&selected_currency=CAD",
  "total_price": 1942.0,
  "currency": "CAD",
  "rating": 8.6,
  "review_count": 3445,
  "price_note": "Additional charges may apply"
}
```

`rating` is the guest score out of 10, not the star rating. Rating and review count
are null when no review label is shown. Results are sorted by displayed stay
price. `price_note` preserves Booking.com's tax/fee disclosure: **total_price is
not guaranteed to be the final checkout amount**, and budget filtering doesn't
add unlisted charges. Member-only sign-in prices are skipped. Results cover the
initially loaded batch of accommodations, including apartments; no hotel-only
filter, scrolling, or pagination is implemented.

## Inspected DOM selectors

Observed on October 3, 2026, using an English/CAD Tokyo search for April 10–20,
2027, two adults and one room. The initial batch contained 15 property cards.
Generated CSS class names aren't used.

| Element | Playwright selector |
| --- | --- |
| Destination | `locator('input[name="ss"]')`; accessible label varies between page versions |
| Currency | `get_by_role("button", name="Prices in Canadian Dollar CAD", exact=True).first` |
| Occupancy | Button name beginning `Number of travelers and rooms` |
| Result summary | Level-1 heading containing `properties found` / `property found`, with `include_hidden=True` for the sign-in overlay |
| Sign-in popup close | `get_by_role("button", name="Dismiss sign-in info.", exact=True)` when visible |
| Property card | `get_by_test_id("property-card")` |
| Name | Within card: `[data-testid="title"]` |
| Hotel link | Within card: `[data-testid="title-link"]` |
| Total stay price | Explicit `Price CAD …` or `Current price CAD …` text within `[data-testid="availability-rate-information"]`; the price test ID can also refer to a nightly price |
| Nights / adults | Within card: `[data-testid="price-for-x-nights"]` |
| Guest score / count | Within card: `[data-testid="review-score-link"]` aria-label |
| Tax/fee disclosure | Within card: `[data-testid="taxes-and-charges"]` |
| Date controls (navigation skips these) | `[data-testid="searchbox-dates-container"]`, `date-display-field-start`, `date-display-field-end` |

Navigation goes directly to `/searchresults.en-us.html` with `ss`, `checkin`,
`checkout`, `group_adults`, `group_children=0`, `no_rooms=1`, and
`selected_currency=CAD`. Requested dates/occupancy are checked against the page
URL, occupancy label, card stay text, and hotel links. This avoids slow calendar
interaction. Booking.com may change these selectors or block cloud browsers;
no retry or bot-check bypass is implemented. Plain HTTP homepage inspection
returned a JavaScript bot challenge. The failed Lambda recording instead showed
loaded results behind a sign-in modal; stable destination selectors and
read-only checks including hidden roles resolve that observed failure.

## WebSocket updates and live viewing

From `hotel-service/`, **terminal 1**:

```bash
set -a
source .env
set +a
../.venv/bin/python websocket_test_server.py
```

If bridge dependencies are missing, install them from `requirements-dev.txt`.
In **terminal 2**, also from `hotel-service/`:

```bash
python3 -m http.server 8080 --bind 127.0.0.1 --directory frontend
```

Open **http://127.0.0.1:8080**, connect, and start a search. This uses paid Skyvern
resources and saves to Mongo when configured. Both service bridges use port 8765
and frontend port 8080; run one service at a time.

The page shows progress, the live browser image, hotel results, and final JSON.
The bridge accepts `http://localhost:8080` and `http://127.0.0.1:8080`, plus
the same hosts on port 3000 for Next.js.
Secrets stay in Python, never in frontend files.

Send `{"action":"search","request":{...hotel inputs...}}` to
`ws://127.0.0.1:8765`. Events have `version: 1`, `type`, `session_id`, `search_id`,
and `timestamp`, plus:

| Type | Payload |
| --- | --- |
| `search.status` | `status`: searching, extracting, complete, or failed |
| `browser.live_view` | `provider`, `website: booking_com`, `browser_session_id`, `url` |
| `browser.stream` | `origin: booking_com`, `browser_session_id`, `status`: starting, live, ended, or unavailable |
| `browser.frame` | `origin: booking_com`, `browser_session_id`, `mime_type: image/jpeg`, `data`: base64 JPEG |
| `search.result` | `result`: final hotel record after Mongo saving |
| `search.error` | `error`: code, message, optional details |

Chrome CDP captures JPEG frames during navigation/extraction, with quality 60,
maximum dimensions 1280×800, and at most five forwarded frames per second.
The bridge coalesces pending frames per browser for slow clients, preserving
status and result events. This is view-only. Capture failures mark the preview
unavailable while the search continues. Frames aren't logged or stored; ordinary
Lambda calls have no frame listener and don't capture a live feed.

`browser.live_view.url` is an optional Skyvern dashboard link that may require a
login. Saved recordings are separate and may be unavailable at the immediate
lookup. Wait for `search.result`, not `search.status: complete`, before considering
storage finished. Each connection accepts one active search. Disconnecting doesn't
cancel the worker, and reconnecting doesn't recover its events.

See [the Next.js guide](../docs/nextjs-integration.md) for a reusable component,
local origin configuration, and production transport requirements.

## Lambda deployment

Build from this directory, independently of the flight image:

```bash
docker buildx build --platform linux/amd64 --provenance=false --load \
  -t hotel-search-service:latest .
```

The separate ECR repository and Lambda `hotel-search-service` are deployed in
`us-west-2`, using the existing Lambda execution role, x86_64 architecture,
2048 MB memory, and a 660-second timeout. For future updates, configure the
hotel `.env` values as Lambda environment variables; credentials never enter the
Docker image. AWS credentials/profile can be reused. No deployed HTTP/WebSocket
endpoint exists yet; direct invocation is the first deployment step.

## Latest local test artifacts

Ignored local files: `artifacts/local-result.json` (latest successful local response),
`artifacts/local-summary.json` (timing and Mongo read-back check),
`artifacts/local-run.log` (traceback), and `artifacts/diagnostic-page.html` /
`diagnostic-page.png` (the diagnostic run that reached results). The latest Mongo
read-back matched the saved final response exactly. These artifacts aren't
committed and may include browser session/tracking data.

## Deployed review check

October 3, 2026: `hotel-search-service` is active in account `481665099496`, region
`us-west-2`, with the hotel key and database `hotel_searches`, collection `searches`.
The corrected image returned `status: complete`, 16 accommodations, in 37.3 seconds
for Tokyo, April 10–20, 2027, two adults and one room, CAD. Mongo read-back matched
all response fields. The follow-up Skyvern lookup found one recording; the final
response's immediate recording metadata can still be empty.

Earlier timeouts were traced through the Lambda recording to a changed destination
label and a sign-in modal hiding accessibility roles. Navigation now uses the
inspected stable field name, dismisses the observed popup, and reads settings
through late overlays. Stay prices are parsed from explicit regular/discounted
total-price labels, not the nightly price selector.

Image digest: `sha256:a15ba107daaccdae0cbb93976b464a0176f027f38135ff68cc9e0be4eec5dfce`.
Actual response and summary: ignored `artifacts/lambda-reviewed-*` files.
Review findings: [code review](../docs/code-review.md). Six focused offline hotel
regressions cover integer budgets, invalid numeric inputs, regular/discounted
stay totals, member-only prices, and no-result heading wordings. Run manually:

```bash
../.venv/bin/python -m unittest discover -s tests -p test_contract.py
```

Remaining work: more destinations/occupancies and real empty-results cases,
recording metadata refresh, a live hotel preview check, deployed orchestrator/WebSocket transport.
Older hotel records in the flight database have not been moved automatically.
