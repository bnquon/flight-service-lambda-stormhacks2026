# Flight search Lambda

## Workspace layout

This service lives in `flight-service/` inside the `travel-search-services/` workspace. Run
all commands in this README from that directory (`cd flight-service` from the
workspace root). The existing shared virtual environment stays at `../.venv`.

The existing credentials stay at the workspace root. To keep using them, replace
`source .env` below with `source ../.env`. Alternatively, copy the root `.env`
into this directory yourself. Python does not load either file automatically.

Python request validation and Google Flights search through Skyvern Cloud.
Use Python 3.12 or 3.13 for deployment. Skyvern provides the remote browser;
Playwright reads Google Flights directly, and PyMongo stores results.
Both direct dependencies are pinned in `requirements.txt`.
Version 1.0.55 requires its `local` extra for the browser/page API even when the
browser runs in Skyvern Cloud. This installs substantial transitive dependencies.

See [Contract examples](#contract-examples) for the request, final JSON, errors,
and progress messages an orchestrator can expect.

## Status and remaining work

Google Flights searches, request validation, per-origin failure handling, recording
metadata, and Mongo persistence are implemented. Searches have been verified
locally and in Lambda; one-way/USD has also passed locally. The retained HTML POC
receives live browser frames through the local WebSocket bridge, confirmed by the
user. Capture compatibility with saved recordings still needs a focused check.

The deployed Lambda has no public WebSocket endpoint. Production integration needs
an authenticated transport, asynchronous job execution, and result/reconnect recovery.
Recording metadata refresh and retention are unresolved; broader routes, multiple
origins, consent dialogs, and explicit no-results pages need live coverage.
No-result `suggestion` remains `null`.

For a Next.js client, see [the shared integration guide](../docs/nextjs-integration.md).

## Request

```json
{
  "session_id": "group-trip-123",
  "origins": ["YVR"],
  "destination": "NRT",
  "departure_date": "2027-04-10",
  "return_date": "2027-04-20",
  "budget": 1500
}
```

- Defaults: `trip_type: round_trip`, `currency: CAD`.
- Round trips require `return_date`, on or after departure.
- For one-way searches, set `trip_type: one_way` and omit `return_date`.
- Budget is optional, positive, and per person. Omission means no budget filter.
- Airport codes are normalized to uppercase; duplicate origins are removed.
- Currency is normalized to uppercase. Code format is checked, but airport and
  currency availability must be checked by the provider in a later step.
- Extra fields are ignored. Session IDs must be non-empty strings.
- One session can have multiple searches; each execution gets a distinct `search_id`.
- Initial scope: one adult in economy. Extract currently displayed outbound cards,
  with total round-trip prices for round trips. Return-leg details are not extracted.

## Contract examples

These examples describe the current synchronous Lambda implementation. IDs, fares,
timestamps, and URLs below are illustrative. They are not new live search results.
There is no deployed HTTP endpoint or frontend message delivery yet. Final records
are stored in MongoDB when configured.

### Request fields

| Field | Required | Meaning |
| --- | --- | --- |
| `session_id` | Yes | Non-empty app session ID. Multiple searches can share it. |
| `origins` | Yes | Non-empty array of three-letter airport codes; normalized and deduplicated. |
| `destination` | Yes | Three-letter airport code, different from every origin. |
| `departure_date` | Yes | Calendar date in `YYYY-MM-DD` format. |
| `return_date` | For round trips | Must be on or after departure. Omit it or use `null` for one-way. |
| `trip_type` | No | `round_trip` by default; also accepts `one_way`. |
| `currency` | No | Three-letter code, uppercased; defaults to `CAD`. |
| `budget` | No | Positive finite number per person, inclusive maximum; omit or use `null` for no filter. |

One-way request with two origins:

```json
{
  "session_id": "group-trip-123",
  "origins": ["YVR", "SEA"],
  "destination": "NRT",
  "departure_date": "2027-04-10",
  "trip_type": "one_way",
  "currency": "CAD",
  "budget": 1000
}
```

Send the request object directly when invoking Lambda. API Gateway proxy events
can instead contain it as a JSON string in `body`, with `isBase64Encoded: true`
when the body is base64 encoded. The service currently waits for the final result;
it does not return an immediate job acknowledgment.

### Lambda response wrapper

Every handled response uses this shape, even for direct Lambda invocation:

```json
{
  "statusCode": 503,
  "headers": {"Content-Type": "application/json"},
  "body": "{\"error\": {\"code\": \"SEARCH_NOT_CONFIGURED\", \"message\": \"Set SKYVERN_API_KEY to enable search execution.\", \"details\": []}}"
}
```

`body` is a JSON **string**. Parse it to get the record or error object shown below.

| `statusCode` | Parsed body |
| --- | --- |
| `200` | Final search record, including searches with partial or total origin failure. Check `status`. |
| `400` | `INVALID_JSON` or `INVALID_REQUEST` error. No search starts. |
| `503` | `SEARCH_NOT_CONFIGURED` when the Skyvern key is missing or still a placeholder. |

Unexpected SDK/setup errors propagate as Lambda invocation errors rather than this
response wrapper. The `200` wrapper does not mean that every origin succeeded.

### Successful search record

Example parsed `200` body with one flight:

```json
{
  "session_id": "group-trip-123",
  "search_id": "d87b50d4-9a6b-4c26-868b-238e0fe3ae70",
  "status": "complete",
  "request": {
    "origins": ["YVR"],
    "destination": "NRT",
    "departure_date": "2027-04-10",
    "return_date": "2027-04-20",
    "trip_type": "round_trip",
    "currency": "CAD",
    "budget": 1500
  },
  "origins": [
    {
      "origin": "YVR",
      "website": "google_flights",
      "status": "complete",
      "skyvern_browser_session_id": "pbs_example_yvr",
      "live_view_url": "https://app.skyvern.com/browser-session/pbs_example_yvr",
      "replay_url": "https://example.test/recordings/yvr-1.mp4",
      "recordings": [
        {"url": "https://example.test/recordings/yvr-1.mp4", "filename": "yvr-1.mp4"},
        {"url": "https://example.test/recordings/yvr-2.mp4", "filename": "yvr-2.mp4"}
      ],
      "error": null
    }
  ],
  "flights": [
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
  ],
  "suggestion": null,
  "recording_url": "https://example-bucket.s3.us-west-2.amazonaws.com/flights/example.mp4",
  "recording_error": null,
  "delivery_error": null,
  "error": null,
  "created_at": "2026-10-03T18:21:19.623827+00:00",
  "updated_at": "2026-10-03T18:22:39.829280+00:00"
}
```

- `search_id` is generated for each invocation; `request` contains normalized inputs.
- `flights` combines successful origins, applies `price <= budget`, and sorts by price.
- `price` is the displayed fare per person: total round-trip fare for round trips,
  or the one-way fare for one-way searches. No currency conversion is performed.
- Times and duration remain Google display text, not timestamps. `+1`/`+2` arrival
  markers mean the following calendar day/two days later. Only outbound details
  are extracted; return-leg details, flight numbers, and booking links are not provided.
- `live_view_url` comes from Skyvern and can be `null`; it is not a recording URL.
- `recordings` retains all returned segments; filenames can be `null`.
  `replay_url` is the first segment's URL, or `null` when unavailable. Recording
  lookup failure does not fail valid fares. URLs may expire and are not durable storage.
- `suggestion` is currently always `null`. Suggestions are not implemented.

### Empty results and partial failure

These are shortened excerpts of the final record; the full response retains the
same fields as the successful example above.

Confirmed no flights, or every extracted fare over budget:

```json
{
  "status": "complete",
  "flights": [],
  "suggestion": null,
  "error": null
}
```

One origin fails while another completes:

```json
{
  "status": "partially_complete",
  "origins": [
    {
      "origin": "YVR",
      "status": "failed",
      "error": {
        "code": "ORIGIN_SEARCH_FAILED",
        "message": "Search or extraction failed; see worker logs."
      }
    },
    {"origin": "SEA", "status": "complete", "error": null}
  ],
  "error": null
}
```

The final `flights` array preserves SEA's matching fares. If SEA completes with
no matching fares, the overall status is still `partially_complete` and `flights` is empty.

All origins fail:

```json
{
  "status": "failed",
  "flights": [],
  "error": {"code": "SEARCH_FAILED", "message": "All origin searches failed."}
}
```

### Invalid request

Example parsed `400` body when a default round-trip request omits `return_date`:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "The search request is invalid.",
    "details": [
      {"field": "return_date", "message": "Expected a valid date in YYYY-MM-DD format."}
    ]
  }
}
```

Malformed JSON uses `INVALID_JSON`, message `Body must contain valid JSON.`, and
`details: []`. Validation errors can contain multiple field details. Origin execution
errors use their separate per-origin error shape, without a `details` array.

### Progress messages

These versioned JSON messages are currently printed to stdout. They define the
payloads for future backend delivery; they are not sent over a WebSocket yet.

Origin extraction event:

```json
{
  "version": 1,
  "type": "search.status",
  "session_id": "group-trip-123",
  "search_id": "d87b50d4-9a6b-4c26-868b-238e0fe3ae70",
  "timestamp": "2026-10-03T18:21:48+00:00",
  "origin": "YVR",
  "status": "extracting"
}
```

Live browser metadata event:

```json
{
  "version": 1,
  "type": "browser.live_view",
  "session_id": "group-trip-123",
  "search_id": "d87b50d4-9a6b-4c26-868b-238e0fe3ae70",
  "timestamp": "2026-10-03T18:21:25+00:00",
  "origin": "YVR",
  "provider": "skyvern",
  "browser_session_id": "pbs_example_yvr",
  "url": "https://app.skyvern.com/browser-session/pbs_example_yvr"
}
```

`provider` identifies browser hosting; the final record's `website` identifies the
flight site. `url` can be `null`. Video bytes are not part of this metadata event;
the local WebSocket bridge sends separate `browser.frame` events (see below).
Skyvern dashboard viewing requires its supported access/authentication; frontend
embedding has not been verified.

| Message scope | Status values |
| --- | --- |
| Whole search (`origin` omitted) | `searching`, then `complete`, `partially_complete`, or `failed` |
| Individual origin | `searching`, `extracting`, then `complete` or `failed` |

An origin can fail before reaching `extracting`. Origin terminal status messages
include `error` (object or `null`); whole-search messages do not include the final
record or its error. `queued` is not emitted. There is no recording-ready event;
recording metadata is included in the final response only.

## How it works

`lambda_function.lambda_handler` reads direct invocation JSON or an API Gateway
proxy body, validates it through `SearchRequest`, and calls `run_search`.
When `SKYVERN_API_KEY` is absent or `replace_me`, it returns `503` without contacting
Skyvern. Invalid input returns `400` with field errors.

With a real key and the SDK installed, the handler waits for execution and returns
`200` with the final record. Check the record's `status`: `complete`,
`partially_complete`, or `failed`. This is a direct execution slice, not a job queue.
The handler always returns an API Gateway response envelope, including when
invoked directly. Unexpected setup failures surface as Lambda invocation errors.

Each origin gets a separate cloud browser, searched sequentially. Direct Playwright
actions navigate the Google Flights form using inspected labels and roles.
Playwright reads flight-card accessibility labels and displayed times, duration,
and airline spans directly. It reads the selected airports, ticket type, cabin,
passenger count, currency, and full calendar dates; result fares must confirm one
adult. Round-trip labels must explicitly confirm total fare basis. Observed one-way
labels omit that phrase, so their basis comes from the verified page ticket type.
Google-specific validation checks the extracted settings and fare fields. The service applies the optional
budget and sorts the combined flights once. An unreliable origin fails without erasing other origin results.
Empty results are successful only for a confirmed no-flights page, or when all valid
extracted fares exceed the budget. Visible cards are not an exhaustive search.

`service.py` owns browser launch/cleanup, search records, and progress messages.
Website modules expose `navigate(raw_playwright_page, request, origin)` and
`extract_flights(skyvern_page, request, origin)`, which returns validated flights.
A second website can implement those two functions without duplicating browser
management. Google Flights is still the only selected website; origin records and
flights include `website: google_flights` for source identification.
Navigation confirms calendar dates and currency; extraction validates the actual
page settings. Google's internal URL encoding is deliberately not parsed.

Status and browser-view messages are versioned JSON printed to stdout by
`publish_update`. The local WebSocket bridge also forwards them to the test
frontend. Deployed orchestrator delivery is still a TODO; MongoDB stores the
final result after the search finishes.

## Manual setup

To try the handler locally, run this from the project directory with Python 3.12+.
No pip packages or AWS credentials are needed to check validation with the key unset.

```bash
python3 - <<'PY'
from lambda_function import lambda_handler
import json

event = {
    "session_id": "local-test",
    "origins": ["YVR"],
    "destination": "NRT",
    "departure_date": "2027-04-10",
    "return_date": "2027-04-20",
}
result = lambda_handler(event, None)
print(result["statusCode"])
print(json.dumps(json.loads(result["body"]), indent=2))
del event["return_date"]
print(lambda_handler(event, None))  # 400: round trips require a return date
PY
```

For a real search, create a virtual environment with a stable Python 3.13 release
and install the SDK with browser support. See the
[shared environment setup](../README.md#shared-local-python-environment) for the
Homebrew interpreter path and both services' local dependencies. Avoid Python
prereleases such as `3.13.0b4`.

```bash
python3.13 -m venv ../.venv
source ../.venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
```

Edit `.env` to replace `SKYVERN_API_KEY`, then load it into your shell:

```bash
set -a
source .env
set +a
```

Run the Python example above again. The first call starts a paid cloud search and
prints progress events followed by the final record. The second remains a validation
error. Real prices and outcomes vary. No AWS keys are needed for Skyvern Cloud.

For Lambda, use the [container image setup](#lambda-container-image) below. It
packages the runtime modules and dependencies for Linux/Python 3.13. Add the
real Skyvern key to Lambda environment variables and configure a 660-second timeout
for this direct execution slice.
The search has a shared 420-second budget and a 240-second cap per origin for
browser launch, navigation, and extraction. Finalization runs outside that cap,
with up to 30 seconds for cleanup plus 10 seconds for immediate recording metadata, so slow
finalization does not discard already extracted fares. It still consumes the shared
budget before the next origin starts. Later origins can fail when that budget is
exhausted.
The 420-second search budget leaves room within the 660-second Lambda timeout
for the final recording wait and upload. Cloud sessions also expire after 15
minutes if cleanup cannot finish.

Use direct Lambda invocation for this slice. Before connecting the dashboard,
decide how to submit jobs asynchronously and retrieve results; a long search should
not depend on the frontend keeping an HTTP request open.

Lambda reads configuration from its environment; Python does not load `.env` files.
Set `MONGODB_URI`, `MONGODB_DATABASE` (default `flight_service`), and
`MONGODB_SEARCH_COLLECTION` (default `searches`) to enable persistence.
An absent or placeholder URI skips storage for local search-only usage.
The WebSocket setting remains unused.

### MongoDB storage contract

`storage.save_search` saves the final response body, including failed/partial searches,
with `_id` equal to `search_id`. Upsert replaces the same search document if saved again;
it does not change the response JSON. MongoDB calls have a 15-second timeout.
If saving fails, the invocation fails rather than returning an unsaved success.
There is no automatic search retry, in-progress persistence, or retrieval endpoint yet.
Recordings that become available later are not refreshed automatically.
The cluster must allow connections from Lambda; a successful local connection alone
does not establish Lambda network access.

## Lambda container image

`Dockerfile` uses AWS's Python 3.13 Lambda base image with the existing
`lambda_function.lambda_handler` entry point. It installs the pinned Skyvern
dependency in Linux; it does not copy your Mac virtual environment or install a
local browser. `.dockerignore` allows only the runtime source, requirements, and
Docker configuration into the build context. `.env`, AWS credentials, tests, and
saved search results are excluded.

### Build manually

Start Docker Desktop, then run from the project directory:

```bash
docker buildx build --platform linux/amd64 --provenance=false --load \
  -t flight-search-service:local .
```

This downloads the base image and dependencies and can take several minutes.
The target is `linux/amd64`; deploy the image to an `x86_64` Lambda function.
The build flag and base-image setup follow the
[AWS Python container guide](https://docs.aws.amazon.com/lambda/latest/dg/python-image.html).

### Check imports and invoke locally without a server

This short container command imports the cloud browser SDK and calls the handler
directly. It does not publish ports or make a paid search. Since no API key is
passed to the container, the expected response is `503 SEARCH_NOT_CONFIGURED`.

```bash
docker run --rm --platform linux/amd64 --entrypoint python \
  flight-search-service:local -c '
import json
from skyvern import Skyvern
import skyvern.library.skyvern_browser
from lambda_function import lambda_handler
event = {
    "session_id": "container-check",
    "origins": ["YVR"],
    "destination": "NRT",
    "departure_date": "2027-04-10",
    "return_date": "2027-04-20",
}
print(json.dumps(lambda_handler(event, None), indent=2))
'
```

This checks Linux imports and direct handler execution, not the Lambda Runtime API
or an actual cloud search. The image was built and this check passed with a
read-only filesystem and writable `/tmp`. It returned the expected
`503 SEARCH_NOT_CONFIGURED`. The image is approximately 2.39 GB uncompressed;
runtime behavior during a real cloud search still needs verification.

### Deployment handoff

After the image check succeeds:

1. Confirm the intended AWS account using the named CLI profile and choose a region.
2. Create an ECR repository in that region, tag this image, and push it there.
3. Create a Lambda function from the ECR image using architecture `x86_64` and an
   execution role with CloudWatch logging permission. Keep the image's handler command.
4. Set timeout to 660 seconds and `SKYVERN_API_KEY` in Lambda environment variables.
   Also set the three MongoDB variables to enable final-result storage.
   The image contains no keys; frontend configuration is not needed yet.
5. Invoke Lambda directly with a contract request, then inspect returned JSON and
   CloudWatch logs for extraction, recording metadata, and cleanup.

### Current deployment

Lambda `flight-search-service` is deployed in account `481665099496`, region
`us-west-2`, using profile `flight-deploy`, x86_64, 2048 MB memory, and a 660-second
timeout. The ECR repository is `flight-search-service`; the execution role is
`flight-search-service-lambda`, trusted by Lambda with `AWSLambdaBasicExecutionRole`.
Skyvern and Mongo configuration belong in Lambda environment variables.

The latest reviewed search returned nine CAD flights with `status: complete`,
and Mongo read-back matched the entire response. See [the deployment checkpoint](#latest-code-review-deployment)
for its digest and artifact references. The local streaming POC is a separate
transport; it has not been deployed as a public endpoint.

To invoke manually (uses paid cloud resources):

```bash
aws lambda invoke --function-name flight-search-service \
  --region us-west-2 --profile flight-deploy \
  --cli-binary-format raw-in-base64-out \
  --payload file://examples/lambda-request.json \
  --cli-read-timeout 700 artifacts/lambda-response.json
```

The CLI `StatusCode: 200` confirms invocation transport success; also parse the
response `body` and inspect its `statusCode` and search `status`. Lambda needs
outbound access to Skyvern and Mongo. Configure Atlas network access for the
production deployment; temporary all-IP test access should be removed or expired.

## Skyvern notes

Implementation follows the official [browser automation guide](https://github.com/Skyvern-AI/skyvern/blob/main/docs/developers/browser-automations/overview.mdx)
and [actions reference](https://github.com/Skyvern-AI/skyvern/blob/main/docs/developers/browser-automations/actions-reference.mdx):
`launch_cloud_browser`, `get_working_page`, direct Playwright actions,
and `browser.close`. Google Flights extraction now uses the raw Playwright page;
there are no `page.extract` or AI action calls in the search flow.
The [cloud browser reference](https://github.com/Skyvern-AI/skyvern/blob/main/docs/sdk-reference/browser-automation/launch-cloud-browser.mdx)
defines the browser timeout in minutes. The dependency is pinned to the
[published 1.0.55 release](https://pypi.org/project/skyvern/1.0.55/).

The SDK-supplied `browser.app_url` is logged for manual viewing when available;
it remains null if the SDK does not provide one.
It is not a verified public embed URL or a replay URL. Authentication and embedding
still need verification before frontend integration.
The Playwright test confirmed that Skyvern produces a recording for direct actions.
No screenshot capture/storage is added by this service.

## Recording storage and result delivery

Browser metadata remains in each origin's `recordings` and `replay_url`. The
worker, when a bucket is configured, polls Skyvern every 3 seconds for up to
`RECORDING_WAIT_SECONDS` (default 90 seconds total), selects the first available
recording across origins, and uploads one video
to `flights/` in `RECORDINGS_S3_BUCKET` (`RECORDINGS_S3_REGION=us-west-2`). It does
not combine recordings. A blank bucket skips upload.

The top-level `recording_url` is the public S3 playback URL, or null. It does not
expire like a signed URL; anyone with the URL can view the stored object. A
recording timeout or upload error sets `recording_error: {code, message}` without
changing search status or dropping fares. Provider URLs remain separate and may
expire.

After saving the final record to Mongo, the worker sends it once as JSON to
`FLIGHT_RESULTS_POST_URL` when configured. Leave that variable blank to skip
POST delivery. The receiver must be a backend endpoint; it receives the record,
not the Lambda HTTP wrapper. POST failures set `delivery_error: {code, message}`
and are saved back to Mongo, preserving the search results. There are no delivery
retries.

Both error fields are null when no error occurred. Waiting/uploading adds time
before the final response. This does not change live WebSocket streaming.
See [shared configuration](../README.md#recording-storage-and-result-delivery).

## Offline search tests

Run from the project directory; only Python's standard library is needed:

```bash
python3 -m unittest discover -s tests -v
```

The tests use fake cloud/browser responses and do not load `.env`, contact Skyvern,
or use paid credits. They cover request and Lambda input validation, extraction
validation, multiple origins, one-way requests, CAD/USD, combined price sorting,
budget boundaries, empty results, partial/all failures, missing watch URLs,
browser cleanup, cancellation, and exhaustion of the shared time budget.
They also cover recording segments, empty/failed metadata lookup, skipped lookup
after failed cleanup, and recordings from failed extractions.

Service tests mock navigation and extraction, then run real contract validation
against fake results. DOM parsing tests use nine captured cards and compare all
flight fields with the previous AI output. Passing offline tests does not verify
live Google selectors, recording, streaming, or no-results page variants.
Those still need separate live searches. No-result suggestions remain unimplemented.

## Separate Playwright navigation test

With the virtual environment installed and `.env` loaded, run:

```bash
../.venv/bin/python playwright_flights_test.py
```

This is separate from the Lambda search flow. It opens a Skyvern cloud browser,
uses direct Playwright actions to select YVR/NRT and April 10–20, 2027, and prints
the results page text. It uses paid cloud-browser resources but no AI navigation
or extraction calls. It closes the session afterward and checks recording metadata.

Selectors were inspected on the live English Google Flights page: airport
combobox labels, the autocomplete dialog's combobox, options containing the airport
code, date textbox labels, and the date confirmation button's accessible name.
The test and Lambda flow now share these actions in `google_flights_navigation.py`.
No generated CSS classes are used. Unexpected consent dialogs or changed labels
can still break this test; it prints visible control metadata when navigation fails.

The successful test reached results in 17.7 seconds excluding browser launch,
displayed nine results. That earlier run also checked Google's search URL;
the current navigation code uses calendar confirmation instead of URL decoding. Skyvern reported one MP4 recording for that completed session. This is
a single-run measurement, not a guaranteed runtime or evidence of stable selectors
across every Google Flights variant.

Local one-way/USD now passed in the full extraction flow. Live no-results,
additional currencies, and consent-dialog variants still need checks.

## Simple frontend / WebSocket test

This is a local test harness: the Python bridge runs `lambda_handler` on your
laptop, using the same Skyvern/Playwright/Mongo pipeline as the deployed image.
The page sends a search request through a WebSocket and receives real progress,
Skyvern watch links, final flights, and errors. No AWS credentials are needed.
Starting a search uses paid Skyvern cloud-browser resources and saves to Mongo
when configured. The deployed Lambda doesn't have a WebSocket endpoint yet.

Fresh environments can install the bridge dependency with:

```bash
../.venv/bin/python -m pip install -r requirements-dev.txt
```

From `flight-service/`, **terminal 1** loads your server-side `.env` and starts the bridge:

```bash
set -a
source .env
set +a
../.venv/bin/python websocket_test_server.py
```

**Terminal 2** serves only the public frontend files:

```bash
python3 -m http.server 8080 --bind 127.0.0.1 --directory frontend
```

Open **http://127.0.0.1:8080**, click **Connect**, then **Start search**.
The default request is YVR–NRT, April 10–20, 2027, round-trip, CAD.
Inputs also support comma-separated origins, one-way, currency, and an optional budget.
All secrets stay in the Python process; the frontend receives no Skyvern API key,
Mongo URI, or AWS credentials. The bridge binds only to loopback and accepts the
frontend's `localhost:8080` / `127.0.0.1:8080` origins, plus the same hosts on
port 3000 for Next.js.

### What the page shows

- WebSocket connection state and per-origin progress.
- Each browser's live watch link as soon as the session is ready.
- An in-page live browser preview using JPEG screencast frames over the WebSocket.
  Select an origin to view its browser; the last frame stays visible when it ends.
  The Skyvern dashboard link is an optional alternative.
- Final fares and the complete JSON response after the Mongo save finishes.
- Errors, including validation, missing configuration, and failed Mongo writes.

Each connection permits one active search. Disconnecting or closing the page
doesn't cancel the worker: it completes cleanup and storage, but the disconnected
page won't receive further events. Reconnecting doesn't replay or recover the old
job. Use the terminal logs/Mongo record to inspect it; a new search creates a new ID.
An overall `search.status: complete` precedes Mongo saving, so the frontend waits
for `search.result` before allowing another search.

### Wire messages and Next.js integration

Send `{"action":"search","request":{...flight inputs...}}` over the socket.
Events include `search.status`, `browser.live_view`, `browser.stream`,
`browser.frame`, `search.result`, and `search.error`, all with version/session/search
IDs and a timestamp. Final results are the record itself, not the Lambda wrapper.

See the [shared contract and Next.js client example](../docs/nextjs-integration.md)
for sending requests, displaying frames per origin, handling busy/error states,
and configuring local origins or a hosted endpoint. The HTML POC remains here for
manual checks without Next.js.

### Live video inside your frontend

The local bridge now relays Chrome screencast frames through the existing
Playwright CDP connection to an image preview in the frontend. Capture runs during
navigation and extraction, with JPEG quality 60, maximum dimensions 1280×800, and
at most five forwarded frames per second. This is a read-only browser preview.
The bridge keeps only the latest pending frame per browser for slow clients,
while preserving status and result events. Capture/acknowledgement failures mark
the preview unavailable without failing the search. Capture is disabled when
there is no update listener, including ordinary Lambda invocations. Frame bytes
are neither printed to logs nor stored with results.

Two additional messages use the same version/session/search/timestamp envelope:

```json
{"type":"browser.stream","origin":"YVR","browser_session_id":"pbs_example_yvr","status":"starting"}
{"type":"browser.frame","origin":"YVR","browser_session_id":"pbs_example_yvr","mime_type":"image/jpeg","data":"<base64 JPEG>"}
```

Stream status is `starting`, `live` after the first frame, `ended` on normal
shutdown, or `unavailable` on capture failure. The frontend retains the last frame
after shutdown or disconnection and clears previews when a new search starts.
Live flight frames have been confirmed by the user. Recording compatibility
still needs a focused live check.

`browser.live_view.url` remains an optional Skyvern dashboard link and may require
a Skyvern login. Use the top-level `result.recording_url` for the uploaded S3
playback video. Per-origin `recordings` and `replay_url` retain provider metadata.

### Connecting after deployment

`ws://127.0.0.1:8765` is only the local test bridge. The deployed Lambda currently
has no public WebSocket endpoint, and its function ARN isn't a WebSocket URL.
A hosted frontend will need a backend/orchestrator with a `wss://` endpoint that
starts searches and forwards these events, including `browser.frame` and
`browser.stream`. Authentication, allowed frontend origins,
and reconnect/recovery behavior still need to be added for deployment.
Keep Skyvern, Mongo, and AWS credentials on the backend.

## Latest code-review deployment

October 3, 2026: the reviewed image is deployed to `flight-search-service` in
`us-west-2`. Removed unused extraction schema metadata while preserving every
flight field and numerical/search-setting check. The local WebSocket update
listener is included; Lambda still emits updates to logs without a public socket.

A real deployed round-trip YVR–NRT search returned **9 flights**, `status: complete`,
in **75.8 seconds**. Mongo read-back matched the complete final record in
`flight_service.searches`. Recording metadata was still empty at the immediate
follow-up lookup. See ignored `artifacts/lambda-reviewed-*` for this invocation.

Image digest: `sha256:624e16412b27a5bfbe4b52cdb91546bb76bca15d94e7bdc7428e4461e1358420`.
Both-axis findings and resolutions: [code review](../docs/code-review.md).

## Recording delivery deployment

The recording wait, S3 archive, and optional results POST code is deployed in
`us-west-2`; Lambda reports `Active` / `Successful`.

Image digest: `sha256:5702258a283d8d93669701dc685466f9556a6f573a73fb652a1567707943fbf9`.

Recording uploads are enabled with
`RECORDINGS_S3_BUCKET=travel-search-recordings-481665099496-us-west-2`.
Bucket and upload role permissions were configured by the user. A live Lambda
check confirmed recording upload, public HTTP 200 access, and exact Mongo
read-back. Result POST delivery is disabled
until its receiver URL is configured.

Verified recording run: `9c64e55f-da14-44fc-b58b-3bbef548c2e7` returned 9 results
in 48.5 seconds. The uploaded MP4 is H.264, 1280×720, with
no recording error. POST delivery remains untested while its URL is blank.
