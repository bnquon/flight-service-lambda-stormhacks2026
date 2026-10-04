# Travel search services

Separate Python Lambda services for flight and hotel searches.

Repository: [bnquon/travel-search-services](https://github.com/bnquon/travel-search-services).
The root workspace is `travel-search-services/`; both services live one level below it.

| Directory | Purpose |
| --- | --- |
| [flight-service/](flight-service/README.md) | Google Flights + Trip.com search, separate Skyvern recordings, Mongo persistence, and local live-browser HTML POC. |
| [hotel-service/](hotel-service/README.md) | Booking.com search with its own Skyvern key, Mongo collection, Lambda image, and local live-browser HTML POC. |

Run each service’s commands from its own directory. The existing `.venv/` and
`.env` remain at the workspace root so existing local credentials are preserved.
Service-specific `.env` files are ignored by Git; the hotel service should use its
own Skyvern API key. Never put credentials in the frontend or a Docker image.

## Shared local Python environment

Create the shared `.venv` with a **stable Python 3.13 release**. Python 3.13
prereleases such as `3.13.0b4` are incompatible with the installed `orjson`
binary and can fail when either service imports Skyvern.

From the workspace root on an Apple Silicon Mac with Homebrew Python installed:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -r flight-service/requirements-dev.txt -r hotel-service/requirements-dev.txt
```

On other systems, use the path to your stable Python 3.13 interpreter. If an
existing `.venv` uses a prerelease, move it aside before creating the replacement;
running `venv` over it does not reliably replace the old environment. Restart
both WebSocket servers after replacing `.venv`.

See the [flight README](flight-service/README.md) for the existing contract,
local commands and deployment notes. See the [Next.js integration guide](docs/nextjs-integration.md) for the shared WebSocket contract and a client component example.

## Run both services locally

Use the shared `.venv` from the setup above and run each WebSocket bridge in a
separate terminal. Both can run together: flights use port **8765** and hotels
use port **8766**. The Python handlers run locally and use Skyvern Cloud browsers
for searches.

The commands below start from the `travel-search-services/` repository root.
Keep existing credential files. For a fresh checkout without them, create the
flight environment at the root and a separate hotel environment:

```bash
cp flight-service/.env.example .env
cp hotel-service/.env.example hotel-service/.env
```

Fill in `SKYVERN_API_KEY` in each file, using the separate hotel key in
`hotel-service/.env`. Set the Mongo values if you want searches saved;
the hotel environment uses `MONGODB_DATABASE=hotel_searches` and
`MONGODB_SEARCH_COLLECTION=searches`. The bridge scripts read exported environment
variables, so load the appropriate file before starting each process.

**Terminal 1 — flights:**

```bash
cd flight-service
set -a
source ../.env
set +a
../.venv/bin/python websocket_test_server.py
```

Flight endpoint: `ws://127.0.0.1:8765`.

**Terminal 2 — hotels:**

```bash
cd hotel-service
set -a
source .env
set +a
WS_PORT=8766 ../.venv/bin/python websocket_test_server.py
```

Hotel endpoint: `ws://127.0.0.1:8766`. Port 8766 is also the hotel's default;
`WS_PORT` lets you override it.

Leave both terminals running. Restart the affected bridge after changing its
code or environment, and use `Ctrl+C` to stop it. If recording uploads are enabled,
the local environment also needs `boto3` and AWS credentials with upload access;
see [recording storage](#recording-storage-and-result-delivery).

### Connect the Fare orchestrator and dashboard

Set these values in `fare-orchestrator-service/.env`:

```dotenv
MOCK_LLM=false
MOCK_TRAVEL=false
DASHBOARD_URL=http://localhost:3000
FLIGHT_SERVICE_WS_URL=ws://127.0.0.1:8765
HOTEL_SERVICE_WS_URL=ws://127.0.0.1:8766
SEARCH_FRONTEND_ORIGIN=http://localhost:3000
```

Run the orchestrator with `go run .` from its repository root. Restart it after
changing its environment. The configured WebSocket URLs select the local search
bridges; leaving them empty selects the Lambda HTTP URLs instead.

In `fare-frontend/web/.env.local`, configure:

```dotenv
NEXT_PUBLIC_ORCHESTRATOR_URL=http://localhost:8000
NEXT_PUBLIC_ORCHESTRATOR_WS_URL=ws://localhost:8000
```

Run `npm run dev` from `fare-frontend/web/` and open `http://localhost:3000`.
The dashboard receives search progress and browser frames through the
orchestrator. Keep Skyvern, Mongo, and AWS credentials in the service environments.

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
| Website | Google Flights + Trip.com | Booking.com + Airbnb |
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
| `recording_url` | One public S3 playback URL, or null | Same |
| `recording_error`, `delivery_error` | Null or `{code, message}` | Same |
| `created_at`, `updated_at` | UTC ISO timestamps | Same |
| Extra fields | `suggestion`: currently null | `website`: `booking_com` |

Browser metadata consists of `skyvern_browser_session_id`, `live_view_url`,
`replay_url`, and `recordings` (`[{"url": "...", "filename": "..."}]`). URLs may
be null. `recordings` contains provider metadata; `recording_url` is the single
public S3 copy intended for playback.
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
The bridges use ports 8765 (flight) and 8766 (hotel), as shown in
[the simultaneous startup instructions](#run-both-services-locally). Both standalone
HTML POCs use frontend port 8080, so serve one POC at a time. In the hotel POC,
set the WebSocket URL field to `ws://127.0.0.1:8766` before connecting.
Run Python with `../.venv/bin/python` from each service directory. Load flight
secrets from its `.env` or the parent `.env`; load hotel secrets from its own `.env`.

The searches are deployed as `flight-search-service` and `hotel-search-service`
in `us-west-2`, with successful search/Mongo checks. Live frames are currently
available through the local bridges. Production still needs a persistent WebSocket
backend, authentication, job routing, and reconnect/result recovery. Recordings
are polled before S3 upload; bucket retention remains a configuration choice.
Broader website variants and empty-results pages also need live checks.

Full flight examples: [flight contract](flight-service/README.md#contract-examples).
Hotel details: [hotel README](hotel-service/README.md).

## Recording storage and result delivery

Set these values in each Lambda's environment (and the appropriate local `.env`):

```env
RECORDINGS_S3_BUCKET=travel-search-recordings-481665099496-us-west-2
RECORDINGS_S3_REGION=us-west-2
RECORDING_WAIT_SECONDS=90
FLIGHT_RESULTS_POST_URL=
HOTEL_RESULTS_POST_URL=
```

Use `FLIGHT_RESULTS_POST_URL` for flights and `HOTEL_RESULTS_POST_URL` for hotels.
These are backend receivers that accept JSON, not frontend page addresses. Blank
POST URLs skip delivery; a blank bucket skips S3 storage.

The bucket and receiver settings belong in `.env` locally and in each Lambda
environment for AWS. Generated recording URLs do **not** belong in `.env`: each
search returns its own `recording_url`, which is also stored in Mongo.

**TODO — frontend result delivery:** supply the two backend POST receiver URLs,
set `FLIGHT_RESULTS_POST_URL` / `HOTEL_RESULTS_POST_URL` in the corresponding
Lambda environments, and verify each receiver accepts the final search record.
Both values are currently blank.

The Lambda Python runtime includes `boto3` for S3 access. For local upload tests,
install it if missing: `../.venv/bin/python -m pip install boto3` from a service
directory. Use local AWS credentials with permission to upload to the bucket.

When the bucket is configured, after closing the browser the worker waits up to
90 seconds total for Skyvern recording metadata, checking every 3 seconds. It
uploads one available recording to S3 under
`flights/` or `hotels/`; flights use the first available recording across origins,
without combining videos. `recording_url` is a public playback URL with no signed
URL expiry. Anyone with the URL can view it while the object remains stored.

The worker saves the final record to Mongo, then sends that record once with an
HTTP `POST` and `Content-Type: application/json`. This is the same JSON record as
the parsed Lambda response body, without the HTTP wrapper. There are no delivery
retries. Until a receiver URL is configured, Lambda still returns the final record
and saves it when Mongo is configured.

Recording timeout/upload errors leave `recording_url: null` and set
`recording_error`. POST errors set `delivery_error` and save it back to Mongo;
neither changes the search's status or discards its results. Browser metadata and live WebSocket frames remain
separate: this adds playback after completion, without changing live streaming.

The delivery code is deployed to both Lambdas, with updates confirmed
`Active` / `Successful`. Both Lambda environments now enable recording uploads
to `travel-search-recordings-481665099496-us-west-2`. The user confirmed bucket
and role setup; the CLI user cannot inspect the bucket policy. Live Lambda
checks confirmed public HTTP 200 MP4 playback and exact Mongo read-back for
both services (9 flights in 48.5 seconds; 17 hotels in 40 seconds). POST delivery
remains disabled until the receiver URLs are supplied.

### AWS bucket setup (admin required)

The configured bucket is `travel-search-recordings-481665099496-us-west-2`.
An administrator created it and configured the policies below after the CLI user
was denied `s3:CreateBucket`. Both Lambda environments now reference this bucket.

An AWS administrator can set it up in the console:

1. Create that bucket in `us-west-2`, with **Bucket owner enforced** ownership.
2. Keep **Block public ACLs** and **Ignore public ACLs** enabled. At the bucket
   level, disable **Block public bucket policies** and **Restrict public buckets**.
3. Add [the recording read policy](infra/recordings-bucket-policy.json) under
   bucket **Permissions → Bucket policy**. It permits public reads only under
   `flights/` and `hotels/`.
4. Add [the upload policy](infra/recordings-upload-policy.json) as an inline policy
   on the existing `flight-search-service-lambda` role, used by both Lambdas.
5. Set `RECORDINGS_S3_BUCKET` to that bucket name in both Lambda environments,
   and use `RECORDINGS_S3_REGION=us-west-2`.

If an account-level public-access block applies, a bucket setting cannot override
it. The administrator must decide whether this public playback setup is allowed.
No account-level settings are changed by this setup.

Alternatively, an administrator can temporarily give the deployment user
[the scoped setup policy](infra/recordings-setup-policy.json) to create/configure
this bucket and attach the upload role policy. It does not grant blanket S3 access
or permission to disable account-level public-access blocking.

## Code overview

Both services follow the same small pipeline:

```text
lambda_function.lambda_handler
  → SearchRequest.parse
  → service.run_search
  → Skyvern cloud browser + website navigation/extraction
  → browser close + wait for recording + S3 upload
  → storage.save_search
  → optional result POST
  → final JSON response
```

| File | Responsibility |
| --- | --- |
| `lambda_function.py` | Decode input, return validation/configuration errors, wrap final JSON. |
| `request.py` | Validate and normalize that service's search inputs. |
| `service.py` | Own browser lifetime, status events, final record, and Mongo save. |
| `google_flights*.py` / `booking.py` | Website-specific navigation and DOM parsing. |
| `storage.py` | Upsert the final record by `search_id`. |
| `delivery.py` | Wait for one recording, upload to S3, and POST the final record. |
| `updates.py` | Emit status JSON and forward events to a scoped transport listener. |
| `live_browser.py` | Capture Chrome screencast JPEG frames while navigation/extraction runs. |
| `websocket_test_server.py` | Local test transport; runs the same handler in a worker thread. |

Flights execute one browser per origin sequentially and preserve successful fares
if another origin fails. Hotels execute one browser for one destination/stay.
Small entrypoint/transport copies keep each Docker image independent; their
interfaces and dependencies remain consistent.

Review findings and resolutions: [code review](docs/code-review.md).

## Go orchestrator completion callbacks

Both search request contracts accept an optional `callback_url` (HTTP or HTTPS).
The Go backend supplies a unique URL per invocation when its
`ORCHESTRATOR_PUBLIC_URL` is configured. After recording processing and Mongo
storage finish, the Lambda POSTs the complete saved search record to that URL,
including `session_id`, `search_id`, terminal `status`, flight/hotel results,
`recording_url`, and recording errors. This happens before returning the final
synchronous HTTP response. No authentication is required.

A supplied `callback_url` takes precedence over `FLIGHT_RESULTS_POST_URL` or
`HOTEL_RESULTS_POST_URL`. Omitting it preserves the existing environment-based
delivery behavior. Callback failures retain the search results and populate
`delivery_error`; the orchestrator can use the direct response as a fallback.
Deploy both updated Lambda images to activate the new request field.

## Search step logging

Both workers emit structured Python logs to the local terminal or Lambda
CloudWatch. Each entry includes `service`, `step`, `phase`, and a timestamp;
search steps include `session_id` and `search_id`, and flight origin steps
include `origin`. Timed steps include `elapsed_ms` on completion or failure.
Logs cover request handling, browser launch/page access, website navigation,
extraction and result counts, browser cleanup, recording polling/download/S3
upload, Mongo saves, and result POST delivery. Optional operations report
`skipped` when unconfigured. Logs do not include request bodies, credentials,
callback URLs, or browser frame data. Existing status events remain unchanged.

Step logs default to `INFO`. Set `LOG_LEVEL=WARNING` or `LOG_LEVEL=ERROR` in
each service environment to reduce output. Deploy the updated service images
to enable these logs in the hosted Lambdas.


## Deployed live previews

Both flight sources (Google Flights and Trip.com) and both accommodation sources
(Booking.com and Airbnb) emit browser previews. Local WebSocket bridges still use
their existing listener. For HTTP Lambda invocations, the orchestrator supplies a
per-search `progress_callback_url` pointing to
`POST /travel-search/events/{requestID}` on its publicly reachable backend.
Set `ORCHESTRATOR_PUBLIC_URL` in the orchestrator to that backend's public base URL;
localhost cannot receive callbacks from AWS. Final results continue through the
existing `callback_url` and HTTP response contract.

The progress sender runs off the browser event loop, bounds its queue to 32 events,
and forwards at most two frames per second per browser over HTTP. Delivery failures
are best effort and never fail extraction. Frame and stream events include `website`,
`origin`, and `browser_session_id`; consumers must key previews by website plus
origin to keep Google and Trip.com separate. No additional Lambda environment
variables or dependencies are required. Recording processing still precedes final
result delivery and is independent of the preview transport.
