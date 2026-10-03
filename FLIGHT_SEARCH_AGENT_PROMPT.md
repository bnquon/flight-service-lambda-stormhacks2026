# Flight Search Agent Planning Prompt

```text
We want to build a flight-search agent for a WhatsApp group travel assistant. Help us plan and develop it incrementally. First inspect the existing repo and summarize its structure, conventions, and any relevant AWS, MongoDB, WebSocket, or Skyvern setup. Then propose a small first milestone. Don’t make code changes until we’ve reviewed the plan.

Product behavior:
- The WhatsApp agent gathers travel details from group chat and invokes this service.
- The service receives an app-level session_id, one or more origins, a destination, and specific travel dates.
- Budget is optional and is per person. If omitted, search without a budget.
- The service searches only; it must not book flights.
- Search Google Flights using Skyvern.
- Extract flight details into structured JSON after the search results load.
- Sort matching flights by price by default.
- If extraction is unreliable or fails, mark the relevant origin search as failed rather than saving guessed flight details.
- If there are no matching flights, return an optional suggestion field. Propose a simple initial suggestion rule for us to review.

Multiple origins:
- A request may contain multiple origins, for example ["YVR", "SEA"].
- Search each origin against the same destination and dates. Include the origin on every flight result.
- Investigate whether Skyvern supports opening and managing multiple tabs reliably in one browser session.
- Recommend whether to use one tab per origin in one browser session or a separate browser session per origin. Consider reliability, runtime, replay, and dashboard display.
- Keep the origin searches grouped under the same app-level session_id and search_id.
- Include the origin in relevant progress updates.
- Recommend how to report partial failure: if one origin fails but others return valid results, should the overall search be partially complete or failed? Preserve successful origin results either way.

Dashboard updates and live browser view:
- The dashboard uses WebSockets for live search status updates. Do not design this around dashboard polling.
- Send structured JSON messages over the WebSocket for meaningful status changes, such as queued, searching, extracting, complete, partially complete, and failed.
- Define a versioned message envelope and payloads. Messages should include session_id and search_id, plus origin where relevant.
- Keep WebSocket status messages separate from the final search record in MongoDB. MongoDB is the source for retrieving current and final search results; WebSocket messages notify connected dashboard clients of changes.
- The dashboard should also be able to display Skyvern’s live browser view while searches are running. Treat this separately from status messaging: send the dashboard the browser session ID and appropriate live-view URL or connection details when available. Do not assume browser video is streamed as JSON over the WebSocket.
- If each origin uses its own browser session, send a live-view message for each origin and include that origin in the message.
- Verify how Skyvern’s live-view URL is meant to be embedded and authenticated before exposing it to dashboard clients.
- We want to retain a replay for later review. First verify what Skyvern supports for replay and retention; don’t assume the live-view URL is also a replay URL. Recommend whether replay can stay with Skyvern or needs separate recording/storage.
- Screenshots are not part of the current plan.
- Identify what WebSocket backend already exists in the repo. If none exists, recommend the smallest suitable AWS approach for connecting dashboard clients and publishing updates from the search worker. Don’t assume a Lambda function should hold a long-lived browser stream.

Search completion and execution:
- Search completion handling is undecided. Investigate likely Skyvern run times and Lambda execution limits, then recommend whether to wait synchronously or use an asynchronous job/status flow.
- Don’t start servers or run commands that bind ports.

Example MongoDB record (adjust it if the repo suggests a better fit):
{
  "session_id": "...",
  "search_id": "...",
  "status": "queued | searching | extracting | partially_complete | complete | failed",
  "origins": [
    {
      "origin": "YVR",
      "status": "searching | extracting | complete | failed",
      "skyvern_browser_session_id": "...",
      "live_view_url": "...",
      "replay_url": null,
      "error": null
    }
  ],
  "flights": [],
  "suggestion": null,
  "error": null,
  "created_at": "...",
  "updated_at": "..."
}

Example WebSocket status message (refine based on existing conventions):
{
  "version": 1,
  "type": "search.status",
  "session_id": "...",
  "search_id": "...",
  "status": "extracting",
  "origin": "YVR",
  "message": "Reading flight options",
  "timestamp": "..."
}

Example separate live-view message:
{
  "version": 1,
  "type": "browser.live_view",
  "session_id": "...",
  "search_id": "...",
  "origin": "YVR",
  "provider": "skyvern",
  "browser_session_id": "...",
  "url": "..."
}

Develop in these steps, reviewing each step before moving on:
1. Inspect the repository and report existing patterns, relevant files, and unknowns. Propose the request/response and MongoDB document schemas, plus WebSocket message types.
2. Agree on the service boundary: input validation, status values, error shape, and whether multiple searches can share one session_id. Recommend a separate search_id if needed.
3. Map the current WebSocket infrastructure. Define how the dashboard subscribes to a session/search and how the backend publishes messages to the right clients. If infrastructure is missing, propose an option before implementing it.
4. Verify Skyvern browser session behavior, multi-tab support, live viewing, extraction output, cleanup, and replay availability from available docs or existing project configuration. Identify any authentication or URL exposure concerns.
5. Recommend synchronous versus asynchronous execution based on expected Skyvern runtime, Lambda limits, and the existing WebSocket setup.
6. Implement the smallest useful slice: request validation and a clear service interface, without connecting to external services unless configured.
7. Add the Skyvern search and extraction flow, with structured output validation and explicit failure handling.
8. Add MongoDB persistence and publish WebSocket status updates for state changes. Keep messages small and avoid sending large result payloads unless the existing dashboard pattern calls for that.
9. Add the dashboard integration contract for status messages and live-view connection details. Do not build a dashboard unless it already belongs in this repo or we ask.
10. Add replay handling only after confirming Skyvern capability and deciding where replay data should live.
11. Review the complete diff, identify remaining decisions, and give us the exact manual steps needed to configure and run it.

At each step, show the proposed changes and explain assumptions before continuing. Keep changes small and preserve existing work. Don’t add dependencies unless necessary; explain any new dependency. Don’t run tests unless we ask.
```
