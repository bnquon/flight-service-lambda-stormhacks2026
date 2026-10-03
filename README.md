# Travel search services

Separate Python Lambda services for flight and hotel searches.

Repository: [bnquon/travel-search-services](https://github.com/bnquon/travel-search-services).
The root workspace is `travel-search-services/`; both services live one level below it.

| Directory | Purpose |
| --- | --- |
| [flight-service/](flight-service/README.md) | Existing Google Flights search, Skyvern browser sessions, Mongo persistence, and local live-browser HTML POC. |
| [hotel-service/](hotel-service/README.md) | Booking.com search with its own Skyvern key, Mongo collection, Lambda image, and local live-browser HTML POC. |

Run each service’s commands from its own directory. The existing `.venv/` and
`.env` remain at the workspace root so existing local credentials are preserved.
Service-specific `.env` files are ignored by Git; the hotel service should use its
own Skyvern API key. Never put credentials in the frontend or a Docker image.

See the [flight README](flight-service/README.md) for the existing contract,
local commands and deployment notes. See the [Next.js integration guide](docs/nextjs-integration.md) for the shared WebSocket contract and a client component example.

## Folder structure

The two service directories are siblings inside the parent workspace:

```text
travel-search-services/         # parent workspace
├── README.md                   # this overview
├── .gitignore
├── .git/
├── .venv/                      # shared local Python environment
├── .env                        # existing flight credentials; ignored
├── atlas-credentials.env        # existing Mongo credentials; ignored
├── flight-service/
│   ├── README.md
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── lambda_function.py
│   ├── request.py / service.py / storage.py / updates.py
│   ├── google_flights.py / google_flights_navigation.py
│   ├── websocket_test_server.py
│   ├── frontend/               # flight-only test UI
│   ├── tests/ / examples/ / artifacts/
│   └── .env.example
└── hotel-service/
    ├── README.md
    ├── Dockerfile
    ├── requirements.txt
    ├── lambda_function.py
    ├── request.py / service.py / storage.py / updates.py
    ├── booking.py
    ├── websocket_test_server.py
    ├── frontend/               # hotel HTML POC
    ├── examples/ / artifacts/
    ├── .env                    # separate hotel key; ignored
    └── .env.example
```

## Service overview

| | Flights | Hotels |
| --- | --- | --- |
| Website | Google Flights | Booking.com |
| Input | Origins, destination airport, travel dates, trip type, currency, optional budget | Destination, check-in/out, adults, one room, CAD, optional stay budget |
| Output list | `flights` | `hotels` |
| Browser | Skyvern Cloud, controlled/extracted with Playwright | Same approach, separate key |
| Storage | Existing Mongo flight collection | Same cluster, `hotel_searches` database → `searches` collection |
| Deployment | Existing AWS Lambda | AWS Lambda `hotel-search-service` |
| Frontend | HTML POC with live frames and WebSocket bridge | HTML POC with live frames and WebSocket bridge |

Both return final JSON with IDs, status, request, errors, timestamps, live-view
URL, and recording metadata. Flights keep browser metadata per origin; hotels
keep it at the top level. Their local sockets deliver progress, live JPEG browser frames,
final results, and errors. Both HTML frontends stay in their service's `frontend/`
directory as POCs. Flight live viewing has been confirmed by the user; the new
hotel live feed still needs a real search check.

The local bridge runs the Python handler on your machine. The deployed Lambdas
return final JSON and have **no public WebSocket endpoint**. For a Next.js app,
use the [integration guide](docs/nextjs-integration.md); publishing the frontend
alone doesn't deploy a live-search backend.

## Calling the deployed services over HTTP

The public Lambda Function URLs are:

| Service | URL |
| --- | --- |
| Flights | https://oi4ykhnpbgrgeedjtljdjdg6qe0luuug.lambda-url.us-west-2.on.aws/ |
| Hotels | https://qhz6talpesw4nfnbipkxnxxsq40ivfah.lambda-url.us-west-2.on.aws/ |

The root [.env.example](.env.example) defines `FLIGHT_SERVICE_URL` and
`HOTEL_SERVICE_URL`. They are also saved in the ignored root `.env`. Copy these
variables into the **orchestrator/backend's environment** to configure its HTTP
calls. The search Lambdas themselves do not need these variables. The URLs are
public addresses, not API keys; keep AWS, Skyvern, and Mongo credentials private.

Send a `POST` with `Content-Type: application/json` and the matching service's
[request fields](#expected-inputs). For example, from a JavaScript backend:

```js
const response = await fetch(process.env.HOTEL_SERVICE_URL, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    session_id: "trip-123",
    destination: "Tokyo, Japan",
    check_in: "2027-04-10",
    check_out: "2027-04-20",
    adults: 2,
    currency: "CAD",
  }),
});
const result = await response.json();
if (!response.ok) throw new Error(result.error?.message ?? "Hotel request failed");
// Check result.status too: a search can return HTTP 200 with status "failed".
```

Function URLs return the final record as the HTTP JSON body; callers do **not**
parse a nested `body` string. The Lambda wrapper below applies to direct AWS SDK
invocations. HTTP calls wait for the full search and storage to finish, so the
calling backend must allow enough time for the search. These URLs do not deliver
WebSocket updates or live browser frames. Backend-to-backend calls do not need
CORS; direct browser calls from another origin need CORS configured on the URL.

## Expected inputs

Send a JSON request directly to the appropriate Lambda. These services search;
they do not reserve rooms, book flights, or process payments.

### Flights

```json
{
  "session_id": "trip-123",
  "origins": ["YVR"],
  "destination": "NRT",
  "departure_date": "2027-04-10",
  "return_date": "2027-04-20",
  "trip_type": "round_trip",
  "currency": "CAD",
  "budget": 1500
}
```

| Field | Required / default | Expected value |
| --- | --- | --- |
| `session_id` | Required | Non-empty string identifying your app session. |
| `origins` | Required | Non-empty list of three-letter airport codes. Uppercased and deduplicated. |
| `destination` | Required | Three-letter airport code, different from all origins. |
| `departure_date` | Required | Valid `YYYY-MM-DD` date. |
| `return_date` | Required for round trips | Valid date on/after departure. Omit or use null for one-way. |
| `trip_type` | `round_trip` | `round_trip` or `one_way`. |
| `currency` | `CAD` | Three-letter code, uppercased; availability depends on Google Flights. |
| `budget` | Optional / null | Positive finite maximum fare per person for the selected trip. |

Flights currently assume **one adult, economy**. Round-trip prices are for the
whole round trip, while times/duration/stops describe the outbound option only.
An `adults` field does not change flight occupancy; extra request fields are ignored.

### Hotels

```json
{
  "session_id": "trip-123",
  "destination": "Tokyo, Japan",
  "check_in": "2027-04-10",
  "check_out": "2027-04-20",
  "adults": 2,
  "rooms": 1,
  "currency": "CAD",
  "budget": 2000
}
```

| Field | Required / default | Expected value |
| --- | --- | --- |
| `session_id` | Required | Non-empty string identifying your app session. |
| `destination` | Required | Non-empty destination text; include country to reduce ambiguity. |
| `check_in` | Required | Valid `YYYY-MM-DD` date. |
| `check_out` | Required | Valid date strictly after check-in. |
| `adults` | Required | Positive integer; no default. |
| `rooms` | `1` | Only integer 1 is supported. |
| `currency` | `CAD` | Only CAD is supported. |
| `budget` | Optional / null | Positive finite maximum displayed stay price for all adults in one room. |

No children are included. The hotel budget is **for the entire stay**, not per
night or per person. Extra fields are ignored. Neither validator currently checks
whether dates are in the future; callers should send future travel dates.

## Expected outputs

### Lambda wrapper (both services)

```json
{
  "statusCode": 200,
  "headers": {"Content-Type": "application/json"},
  "body": "<JSON string containing the final record>"
}
```

Parse `body` with `JSON.parse(response.body)` in JavaScript or
`json.loads(response["body"])` in Python. Both handlers are synchronous: they
return after searching, browser cleanup, and Mongo saving when configured.

| Response | Meaning |
| --- | --- |
| `200` | Final record. **Check its `status`**; a failed search can still use this wrapper. |
| `400` | Invalid JSON or invalid fields. Body contains `error.code`, `message`, and `details`. |
| `503` | Skyvern API key missing or still `replace_me`. Body contains `SEARCH_NOT_CONFIGURED`. |
| Invocation exception | Unexpected setup or Mongo failure; inspect worker/Lambda logs. |

Handled request error example:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "The search request is invalid.",
    "details": [{"field": "adults", "message": "Expected a positive integer."}]
  }
}
```

### Final record fields

| Field | Flights | Hotels |
| --- | --- | --- |
| `session_id`, `search_id` | App session ID and generated search UUID | Same |
| `status` | `complete`, `partially_complete`, or `failed` | `complete` or `failed` |
| `request` | Normalized flight inputs | Normalized hotel inputs |
| Results | `flights`: sorted by `price` | `hotels`: sorted by `total_price` |
| Browser metadata | In each `origins[]` entry | At the top level |
| `error` | Null or `{code, message}`; origin errors also in `origins[]` | Null or `{code, message}` |
| `created_at`, `updated_at` | UTC ISO timestamps | Same |
| Extra fields | `suggestion`: currently null | `website`: `booking_com` |

Browser metadata consists of `skyvern_browser_session_id`, `live_view_url`,
`replay_url`, and `recordings` (`[{"url": "...", "filename": "..."}]`). URLs may
be null, and recordings may be empty until Skyvern finishes processing them.
Each flight origin entry also has `origin`, `website`, `status`, and `error`.

One item from `flights` (illustrative):

```json
{
  "airline": "Example Air",
  "outbound_departure_time_text": "11:55 PM",
  "outbound_arrival_time_text": "1:00 PM+2",
  "outbound_duration_text": "21 hr 5 min",
  "outbound_stops": 1,
  "price": 1324,
  "currency": "CAD",
  "origin": "YVR",
  "destination": "NRT",
  "website": "google_flights"
}
```

One item from `hotels` (illustrative):

```json
{
  "name": "Example Hotel",
  "url": "https://www.booking.com/hotel/jp/example.html?checkin=2027-04-10&checkout=2027-04-20&group_adults=2&group_children=0&no_rooms=1&selected_currency=CAD",
  "total_price": 1800,
  "currency": "CAD",
  "rating": 8.4,
  "review_count": 2423,
  "price_note": "Additional charges may apply"
}
```

Hotel rating is a guest score out of 10, not stars. `rating` and `review_count`
can be null, and `price_note` can be null. The displayed stay total may exclude
additional charges; budget filtering does not add those charges. Hotel results
include other accommodation types, skip member-only prices, and cover only the
initially loaded batch. Flight results cover displayed outbound cards.

`complete` with an empty list means no matching results survived the filters or
the provider explicitly showed no results. `failed` with an empty list means the
search couldn't finish. A failed hotel record uses `SEARCH_FAILED`; flight
records can also contain `ORIGIN_SEARCH_FAILED` per origin.

### Frontend WebSocket messages

Local bridges accept `{"action":"search","request":{...service input...}}`.
Each event has top-level `version: 1`, `type`, `session_id`, `search_id`, and
`timestamp`, plus the fields below. IDs can be null for errors before a search starts.

| Event | Payload |
| --- | --- |
| `search.status` | `status`; flight origin events also include `origin` and may include `error` |
| `browser.live_view` | `provider`, `browser_session_id`, `url`; flights add `origin`, hotels add `website` |
| `browser.stream` | `origin`, `browser_session_id`, `status`: `starting`, `live`, `ended`, or `unavailable` |
| `browser.frame` | `origin`, `browser_session_id`, `mime_type: image/jpeg`, `data`: base64 JPEG |
| `search.result` | `result`: the final record directly, **not** the Lambda wrapper |
| `search.error` | `error`: code, message, optional details |

Wait for `search.result` before treating storage as finished; an overall final
`search.status` is emitted before Mongo saving. Display `browser.frame` as an image for the in-page preview; `browser.live_view`
is an optional Skyvern dashboard link. Frames use airport codes for flight
`origin` and `booking_com` for hotels. See the [Next.js example](docs/nextjs-integration.md).

## Local POCs and remaining work

Follow the [flight setup](flight-service/README.md#simple-frontend--websocket-test)
or [hotel setup](hotel-service/README.md#websocket-updates-and-live-viewing).
Both use bridge port 8765 and frontend port 8080, so run one service at a time.
Run Python with `../.venv/bin/python` from each service directory. Load flight
secrets from its `.env` or the parent `.env`; load hotel secrets from its own `.env`.

The searches are deployed as `flight-search-service` and `hotel-search-service`
in `us-west-2`, with successful search/Mongo checks. Live frames are currently
available through the local bridges. Production still needs a persistent WebSocket
backend, authentication, job routing, and reconnect/result recovery. Recordings
can appear after the immediate lookup; refresh and retention remain unresolved.
Broader website variants and empty-results pages also need live checks.

Full flight examples: [flight contract](flight-service/README.md#contract-examples).
Hotel details: [hotel README](hotel-service/README.md).

## Code overview

Both services follow the same small pipeline:

```text
lambda_function.lambda_handler
  → SearchRequest.parse
  → service.run_search
  → Skyvern cloud browser + website navigation/extraction
  → browser close + recording metadata
  → storage.save_search
  → final JSON response
```

| File | Responsibility |
| --- | --- |
| `lambda_function.py` | Decode input, return validation/configuration errors, wrap final JSON. |
| `request.py` | Validate and normalize that service's search inputs. |
| `service.py` | Own browser lifetime, status events, final record, and Mongo save. |
| `google_flights*.py` / `booking.py` | Website-specific navigation and DOM parsing. |
| `storage.py` | Upsert the final record by `search_id`. |
| `updates.py` | Emit status JSON and forward events to a scoped transport listener. |
| `live_browser.py` | Capture Chrome screencast JPEG frames while navigation/extraction runs. |
| `websocket_test_server.py` | Local test transport; runs the same handler in a worker thread. |

Flights execute one browser per origin sequentially and preserve successful fares
if another origin fails. Hotels execute one browser for one destination/stay.
Small entrypoint/transport copies keep each Docker image independent; their
interfaces and dependencies remain consistent.

Review findings and resolutions: [code review](docs/code-review.md).
