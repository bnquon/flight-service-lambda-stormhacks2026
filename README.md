# Flight search Lambda

Python request validation and Google Flights search through Skyvern Cloud.
Use Python 3.12 or 3.13 for deployment. Skyvern provides the remote browser and AI
extraction SDK; PyMongo stores results.
Both direct dependencies are pinned in `requirements.txt`.
Version 1.0.55 requires its `local` extra for the browser/page API even when the
browser runs in Skyvern Cloud. This installs substantial transitive dependencies.

See [Contract examples](#contract-examples) for the request, final JSON, errors,
and progress messages an orchestrator can expect.

## Progress and next steps

Last updated: October 3, 2026. The search slice is deployed to Lambda and has passed
one successful live search. Final-result MongoDB persistence is implemented; backend delivery
and the asynchronous production flow are still missing. Implementation and live verification are tracked separately.

### Implemented

- [x] Python Lambda handler with direct/proxy input and structured errors.
- [x] Request validation; round-trip/CAD defaults; multiple origins and optional budget.
- [x] Google Flights navigation through Playwright in a Skyvern cloud browser.
- [x] Structured extraction, search-setting validation, source attribution, and price sorting.
- [x] Separate browser per origin, partial failure handling, deadlines, and cleanup.
- [x] Versioned status and watch-link events printed to stdout.
- [x] Best-effort recording metadata lookup with all segments in final JSON.
- [x] Contract examples and local setup instructions.
- [x] Lambda Dockerfile and allowlist for the Docker build context.
- [x] Built `flight-search-service:local` for Linux/amd64; verified SDK imports and
  direct handler execution with a read-only filesystem and writable `/tmp`.
- [x] Pushed the image to ECR and deployed Lambda in `us-west-2`.
- [x] Deployed live search returned nine flights; invalid input returned `400`.
- [x] Standards/simplicity review and 46 passing offline tests at this checkpoint.
- [x] Save final search records in MongoDB, using `search_id` as `_id`.

### What has actually been verified

| Capability | Evidence | Still missing |
| --- | --- | --- |
| Round-trip Google Flights extraction in CAD | Local and deployed YVR–NRT searches returned nine structured fares; deployed run used the refactored code. | Broader routes and page variants. |
| Multiple origins, budget filtering, empty results, and partial failures | Offline tests pass with fake browser/extraction responses. | Live multi-origin and explicit no-flights cases. |
| One-way and other currencies | Request/extraction/service logic passes offline tests. | Live navigation and extraction checks. |
| Recording creation | Deployed session closed successfully; immediate response had no recordings, later metadata lookup returned one. | Deferred metadata refresh, URL expiry, and retention. |
| Live viewing | Browser was viewable in Skyvern's dashboard during earlier runs. | Frontend embedding, viewer authentication, and access behavior. |
| Status messaging | JSON envelopes are implemented and tested locally. | Actual delivery to the orchestrator/frontend. |
| MongoDB storage | Lambda saved both a failed-search record and a successful nine-fare search. The successful document matched the entire response body exactly on read-back. | Restrict network access for production; add result retrieval. |
| Lambda deployment | Active in account `481665099496`, `us-west-2`; live search and invalid-input checks passed. | Other live cases and production integration. |

Offline tests mock Google navigation and cloud responses. They do not establish
live selector reliability, extraction accuracy, recording availability, or streaming.

### Next search and recording checks

1. [ ] Inspect Google Flights result cards and implement Playwright extraction into
   the same JSON contract; compare output with known fares before replacing AI extraction.
2. [ ] Run a live one-way search and a round-trip search with two origins; inspect
   actual Google settings, JSON fares, and session cleanup. These additional paid
   live cases have not been run; the deployed check covered one round-trip origin.
3. [ ] Check a non-CAD search, an explicit no-flights page, and consent-dialog
   behavior. Only change selectors or handling when these cases show a real need.
4. [ ] Decide how to refresh recording metadata after completion: the deployed test
   confirmed recordings can appear after the immediate lookup. Verify expiry and
   retention, and multiple segments when available.
5. [x] Deploy and test in account `481665099496`, profile `flight-deploy`, region
   `us-west-2`. The account administrator created the required execution role.

### Missing integrations and decisions

- [ ] Decide asynchronous submission/worker execution; the current handler waits
  for the entire search. There is no queue, immediate job acknowledgment, or result endpoint.
- [x] Add MongoDB persistence for final records.
- [ ] Add in-progress persistence and a result retrieval endpoint.
- [ ] Choose the orchestrator delivery transport and connect `publish_update`.
  There is no existing HTTP destination or WebSocket backend; `.env` settings are placeholders.
- [ ] Verify and integrate frontend live-view authentication/embedding.
- [ ] Decide recording retention, URL refresh, and whether durable storage is needed.
- [ ] Agree on a no-result suggestion rule; `suggestion` currently stays `null`.

Adding more flight websites is optional future work. The navigation/extraction
interface is ready for another module, but only Google Flights is implemented.

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
flight site. `url` can be `null`. Video bytes are not part of these messages.
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
Then Skyvern's `page.extract(..., schema=...)` reads the displayed flight cards
into JSON without an AI navigation task. Google-specific validation checks the
extracted route/dates/currency and fare basis. The service applies the optional
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
`publish_update`. They do not reach the frontend yet. The WebSocket destination
is still a TODO; MongoDB stores the final result after the search finishes.

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

For a real search, create a virtual environment and install the SDK with browser support:

```bash
python3 -m venv .venv
source .venv/bin/activate
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
packages the six runtime modules and dependencies for Linux/Python 3.13. Add the
real Skyvern key to Lambda environment variables and configure a 660-second timeout
for this direct execution slice.
The search has a shared 600-second budget and a 240-second cap per origin for
browser launch, navigation, and extraction. Finalization runs outside that cap,
with up to 30 seconds for cleanup plus 10 seconds for recording metadata, so slow
finalization does not discard already extracted fares. It still consumes the shared
budget before the next origin starts. Later origins can fail when that budget is
exhausted.
Cloud sessions also expire after 15 minutes if cleanup cannot finish.

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

Profile `flight-deploy` now authenticates as `flight-service` in account
`481665099496`, region `us-west-2`. ECR and Lambda access checks succeeded.
Repository `flight-search-service` contains the current image tag `mongo`.
Image URI: `481665099496.dkr.ecr.us-west-2.amazonaws.com/flight-search-service:mongo`.
Deployed digest: `sha256:0f96d4a93c8a6c90d911e3c0698fbc6cfccfcd39a2c2cebeb7ffa14fdd050535`.
The account administrator created the execution role:

- Name: `flight-search-service-lambda`
- Trusted AWS service: Lambda (`lambda.amazonaws.com`)
- Attached policy: `AWSLambdaBasicExecutionRole`

Lambda `flight-search-service` is active, with timeout 660 seconds, 2048 MB memory,
and architecture `x86_64`. ARN:
`arn:aws:lambda:us-west-2:481665099496:function:flight-search-service`.
The Skyvern key and MongoDB configuration are stored in Lambda's environment,
not in the image.

The initial live request in `examples/lambda-request.json` returned `statusCode: 200`,
`status: complete`, and nine CAD flights sorted by price. CloudWatch reported
115.8 seconds of execution and 608 MB peak memory. The Skyvern browser session
`pbs_581428363942155146` was confirmed `completed`. Immediate recording metadata
was empty; a later lookup returned one recording. The returned response is preserved
unchanged in `artifacts/lambda-result.json`, and the later session lookup is saved
separately in `artifacts/lambda-session-check.json`. These files are ignored by Git.
An additional invocation with `{}` returned `400 INVALID_REQUEST` without a search.

The Mongo-enabled image is deployed and has the three MongoDB environment variables.
A fresh Lambda search reached the MongoDB save but the invocation failed with
`ServerSelectionTimeoutError` and `TLSV1_ALERT_INTERNAL_ERROR` after 112.7 seconds.
The exact image successfully pinged MongoDB from the local network. After activating
the Atlas all-IP access-list entry, the deployed retry successfully saved its final
record. A read-back matched the entire Lambda response body exactly.

The retry search itself failed during Skyvern AI extraction with `httpx.ReadTimeout`;
MongoDB stored `status: failed`, zero flights, and one recording segment.
Search ID: `6b5e05b0-3169-4999-8a6f-fae00ed07aee`.
Lambda duration was 108.0 seconds. The retry response, parsed result, verification
summary, and log tail are in `artifacts/lambda-mongo-*`. The successful save
confirms that the Atlas access-list change resolved the deployed Mongo connection
issue; the Skyvern timeout is a separate unresolved search failure.
The updated image is deployed. The Skyvern client API timeout is explicitly
180 seconds instead of the
SDK default of 60 seconds. The per-origin search deadline remains 240 seconds.
This allows longer extraction responses; it does not speed up AI or guarantee success.
The 180-second timeout was verified with a fresh paid live Lambda search.
It returned `statusCode: 200`, `status: complete`, and nine CAD flights, sorted by
price from CAD 1,324 to CAD 1,987. MongoDB read-back matched the complete
response body exactly. Search ID: `933de981-a9ed-4195-b579-fcd39cabea30`.
Lambda duration was 124.8 seconds, with 625 MB peak memory.
The latest successful response, result, summary, and log tail are saved in
`artifacts/lambda-mongo-*`; these replace the previous retry artifacts.
One successful retry establishes this run worked, not that longer timeouts
resolve every future extraction delay.
See [MongoDB Lambda networking guidance](https://www.mongodb.com/docs/atlas/manage-connections-aws-lambda/)
for private networking or a NAT gateway with a fixed Elastic IP; public Lambda
connections otherwise require an all-IP entry. A temporary `0.0.0.0/0` test entry
allows access from the public Internet and still requires strong database credentials.
Remove or expire it after testing. Connection reuse across warm invocations is a
future improvement; this slice opens and closes one client for each final save.

To repeat the paid live search manually:

```bash
aws lambda invoke --function-name flight-search-service \
  --region us-west-2 --profile flight-deploy \
  --cli-binary-format raw-in-base64-out \
  --payload file://examples/lambda-request.json \
  --cli-read-timeout 700 artifacts/lambda-response.json
```

The CLI's `StatusCode: 200` only confirms invocation transport success. Parse the
saved response's JSON `body` and inspect its `statusCode` and search `status` too.

### Previous account attempts

The execution role `flight-search-service-lambda` was created in account
`888888838474` with `AWSLambdaBasicExecutionRole` for CloudWatch logging.
The latest requested region is `us-east-1`. Deployment checks in both `us-west-2`
and `us-east-1` failed with explicit denies
from organization service control policy `p-6is7bqli` for `ecr:DescribeRepositories`
and `lambda:GetFunction`. The IAM user also cannot read that policy. The account
owner reports a Free plan; AWS's newer signup experience can use AWS-managed
policies and a fixed project region. Confirm the project's assigned region in
AWS Settings before deciding whether a plan or policy change is needed. For a
customer-managed organization, its administrator must confirm allowed access.
IAM user permissions alone cannot override the explicit deny. No image push or
deployed live test occurred in that previous account.

Retry with newly configured user `Lambda_user` in the same account authenticated
successfully. In `us-east-1`, ECR now reports a missing identity-based permission
for `ecr:DescribeRepositories`; Lambda still reports the same explicit SCP deny
for `lambda:GetFunction`. Changing users did not resolve the deployment blocker.

The deployed function will need outbound access to Skyvern Cloud. Avoid
adding a VPC for this initial deployment unless its outbound connectivity is configured.

The live test request is saved in `examples/lambda-request.json`.

## Skyvern notes

Implementation follows the official [browser automation guide](https://github.com/Skyvern-AI/skyvern/blob/main/docs/developers/browser-automations/overview.mdx)
and [actions reference](https://github.com/Skyvern-AI/skyvern/blob/main/docs/developers/browser-automations/actions-reference.mdx):
`launch_cloud_browser`, `get_working_page`, direct Playwright actions,
`page.extract` with a schema, and `browser.close`. Extraction JSON is returned to
this service; it is not a task-run `output` from `agent.run_task`.
The [cloud browser reference](https://github.com/Skyvern-AI/skyvern/blob/main/docs/sdk-reference/browser-automation/launch-cloud-browser.mdx)
defines the browser timeout in minutes. The dependency is pinned to the
[published 1.0.55 release](https://pypi.org/project/skyvern/1.0.55/).

The SDK-supplied `browser.app_url` is logged for manual viewing when available;
it remains null if the SDK does not provide one.
It is not a verified public embed URL or a replay URL. Authentication and embedding
still need verification before frontend integration.
The Playwright test confirmed that Skyvern produces a recording for direct actions.
No screenshot capture/storage is added by this service; Skyvern uses screenshots
internally for its AI actions.

## Recording metadata

After a browser closes successfully, the service makes one bounded
`get_browser_session(browser_session_id)` request. It copies the SDK's recording
URLs and filenames into each origin's `recordings` array. `replay_url` points to the
first recording for convenience; use the full array when there are multiple segments.
The dashboard watch URL remains separate from these recording URLs.

Recording lookup is best effort: missing metadata, a timeout, or an API error leaves
`recordings: []` and `replay_url: null` without failing valid flight results. Failed
extractions can still include recordings for debugging. If browser cleanup fails,
the lookup is skipped. There are no polling loops, downloads, or extra dependencies.

These are provider-supplied URLs, not permanent replay storage. Recordings might
not be ready immediately and URLs may expire. Keep the browser session ID for a
later metadata lookup. URL expiration, retention, and viewer access still need live
verification. The retrieval path passed offline tests and ran in the deployed live
search. That immediate lookup returned no recordings; a later session lookup found
one recording, confirming that availability can lag search completion. No deferred
refresh is implemented yet.

## Next review

Use [Progress and next steps](#progress-and-next-steps) as the checklist. Update the
verification table after each live check; keep implemented features separate from
features that have been demonstrated against Google Flights and Skyvern Cloud.

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

Service tests run the real extraction/validation code against fake extracted JSON;
navigation is mocked. Passing these tests does not verify live Google selectors,
AI extraction accuracy, recording, streaming, or the one-way/no-results page variants.
Those still need separate live searches. No-result suggestions remain unimplemented.

## Separate Playwright navigation test

With the virtual environment installed and `.env` loaded, run:

```bash
.venv/bin/python playwright_flights_test.py
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

One-way, non-CAD, no-results, and consent-dialog variants still need live checks.

## JSON extraction check

The Lambda flow was tested with YVR/NRT, April 10–20, 2027, round-trip, CAD.
Playwright navigation followed by Skyvern extraction returned `status: complete`
and nine structured flight results sorted by price. Total elapsed time was 82.2
seconds including browser startup and cleanup. The local result is saved in
`artifacts/search-result.json` (ignored by Git); fares can change on later runs.
Use the handler example above with `.env` loaded to run another paid search.
