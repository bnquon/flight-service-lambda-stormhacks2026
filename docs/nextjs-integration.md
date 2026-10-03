# Next.js live-search integration

Both services expose the same local WebSocket protocol. The HTML frontends in
`flight-service/frontend/` and `hotel-service/frontend/` are retained POCs;
a Next.js client can consume the same events without a frontend SDK.

```text
Next.js client → WebSocket search request → Python worker → Skyvern browser
Next.js <img> ← WebSocket JPEG frames ← Python CDP screencast listener
Next.js results ← search.result ← extraction, browser cleanup, Mongo save
```

This is a view-only stream of browser images, not an interactive remote browser
or a video file. Chrome emits JPEG screencast frames through Playwright's CDP
connection. Python forwards at most five frames per second, at JPEG quality 60
and maximum dimensions 1280×800. The bridge keeps the newest pending frame per
browser for slow clients without dropping status/result events. Frames aren't
logged or saved. Capture runs during navigation/extraction and is disabled when
there is no update listener, including ordinary Lambda invocations.

Flight live viewing has been confirmed by the user. The new hotel preview and
compatibility with saved recordings still need real search checks. The Next.js
example below illustrates the client integration; it hasn't been run in a Next.js app.

## Local setup

Run either service's bridge from that service directory, with its environment
loaded, following the [flight](../flight-service/README.md#simple-frontend--websocket-test)
or [hotel](../hotel-service/README.md#websocket-updates-and-live-viewing) instructions.
Both bind `ws://127.0.0.1:8765`, so use one at a time. The bridge is the search
backend; your Next.js app replaces the HTML frontend for this check.

The bridges allow these local page origins:

- `http://localhost:8080` and `http://127.0.0.1:8080` for the HTML POCs.
- `http://localhost:3000` and `http://127.0.0.1:3000` for Next.js.

If your app uses another origin, add that exact trusted origin to the `origins`
list in that service's `websocket_test_server.py`. Keep the bridge on loopback.

In your Next.js `.env.local`, point the service you're trying at the local bridge:

```dotenv
NEXT_PUBLIC_FLIGHT_WS_URL=ws://127.0.0.1:8765
NEXT_PUBLIC_HOTEL_WS_URL=ws://127.0.0.1:8765
```

These are public endpoint URLs. Skyvern keys, Mongo URIs, and AWS credentials
belong only on the Python backend. Both values are identical locally because
only one bridge is running. Production should provide a separate endpoint per
service, or an orchestrator that explicitly routes service requests.

## Wire contract

Send the appropriate service request:

```json
{"action":"search","request":{"session_id":"trip-123","origins":["YVR"],"destination":"NRT","departure_date":"2027-04-10","return_date":"2027-04-20","trip_type":"round_trip","currency":"CAD"}}
```

```json
{"action":"search","request":{"session_id":"trip-123","destination":"Tokyo, Japan","check_in":"2027-04-10","check_out":"2027-04-20","adults":2,"rooms":1,"currency":"CAD"}}
```

Every server event has `version: 1`, `type`, `session_id`, `search_id`, and
`timestamp` (UTC ISO), with its payload at the top level. IDs can be null for
errors before a search starts.

| Event | Payload / handling |
| --- | --- |
| `search.status` | `status`, optionally flight `origin` and `error`. Display progress; don't treat it as the final result. |
| `browser.live_view` | `provider`, `browser_session_id`, `url`; flights add `origin`, hotels add `website: booking_com`. Optional Skyvern dashboard link, possibly requiring login. |
| `browser.stream` | `origin`, `browser_session_id`, `status`: `starting`, `live`, `ended`, or `unavailable`. Preview failure doesn't fail the search. |
| `browser.frame` | `origin`, `browser_session_id`, `mime_type: image/jpeg`, `data`: base64 JPEG. Set image `src` to `data:image/jpeg;base64,...`. |
| `search.result` | `result`: final record directly, not the Lambda wrapper. It can have `status: failed`; flights can also be `partially_complete`. |
| `search.error` | `error: {code, message, details?}`. `SEARCH_BUSY` means the existing job is still running; other errors terminate the attempted request. |

For stream/frame events, `origin` is an airport code for flights and
`booking_com` for hotels. Flight origins run sequentially; retain each origin's
preview separately. `browser_session_id` identifies the browser that produced it.

Wait for `search.result` after Mongo saving, rather than the earlier overall
`search.status: complete`. One connection supports one active job. Disconnecting
or unmounting does not cancel the backend worker. Reconnecting has no replay,
job recovery, or result retrieval; avoid automatically resubmitting paid searches.
Saved recordings are separate final-result metadata and can appear after the
immediate lookup. The current feed doesn't stream recording files.

## Client component example

Put this in `app/components/LiveSearch.tsx`. It connects on mount and starts a
search only when the button is clicked and the socket is open. Refs track the
active session/search without stale React state in WebSocket callbacks.

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

type Preview = { src?: string; status?: string; browserSessionId: string };
type ActiveSearch = { sessionId: string; searchId: string | null };

export default function LiveSearch({
  websocketUrl,
  request,
}: {
  websocketUrl: string;
  request: Record<string, unknown>;
}) {
  const socketRef = useRef<WebSocket | null>(null);
  const activeRef = useRef<ActiveSearch | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Connecting…");
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [result, setResult] = useState<unknown>(null);

  useEffect(() => {
    let disposed = false;
    const socket = new WebSocket(websocketUrl);
    socketRef.current = socket;
    activeRef.current = null;
    setConnected(false);
    setBusy(false);
    setStatus("Connecting…");
    setPreviews({});
    setResult(null);

    socket.onopen = () => {
      if (disposed) return;
      setConnected(true);
      setStatus("Connected");
    };
    socket.onmessage = ({ data }) => {
      if (disposed) return;
      let event;
      try { event = JSON.parse(data); } catch { return; }
      if (!event || event.version !== 1) return;

      const active = activeRef.current;
      if (!active) return;
      if (event.session_id && event.session_id !== active.sessionId) return;
      if (event.search_id) {
        if (active.searchId && event.search_id !== active.searchId) return;
        active.searchId = event.search_id;
      }

      if (event.type === "browser.frame" || event.type === "browser.stream") {
        if (!event.origin || !event.browser_session_id) return;
        if (event.type === "browser.frame" &&
            (event.mime_type !== "image/jpeg" || typeof event.data !== "string")) return;
        setPreviews((previous) => {
          const old = previous[event.origin];
          if (old && old.browserSessionId !== event.browser_session_id &&
              (event.type === "browser.frame" || event.status !== "starting")) return previous;
          const preview = old?.browserSessionId === event.browser_session_id
            ? old : { browserSessionId: event.browser_session_id };
          return {
            ...previous,
            [event.origin]: event.type === "browser.frame"
              ? { ...preview, src: `data:image/jpeg;base64,${event.data}` }
              : { ...preview, status: event.status },
          };
        });
      } else if (event.type === "search.status") {
        setStatus(`${event.origin ?? "Search"}: ${event.status}`);
      } else if (event.type === "search.result") {
        setResult(event.result);
        setStatus(`Finished: ${event.result.status}`);
        activeRef.current = null;
        setBusy(false);
      } else if (event.type === "search.error") {
        setStatus(`${event.error.code}: ${event.error.message}`);
        if (event.error.code !== "SEARCH_BUSY") {
          activeRef.current = null;
          setBusy(false);
        }
      }
    };
    socket.onerror = () => {
      if (!disposed) setStatus("WebSocket connection error");
    };
    socket.onclose = () => {
      if (disposed) return;
      setConnected(false);
      setBusy(false);
      activeRef.current = null;
      setPreviews((previous) => Object.fromEntries(
        Object.entries(previous).map(([origin, preview]) => [origin, {
          ...preview,
          status: preview.status === "starting" || preview.status === "live"
            ? "disconnected" : preview.status,
        }]),
      ));
      setStatus("Disconnected; the worker may still finish on the backend");
    };
    return () => {
      disposed = true;
      socket.close();
      if (socketRef.current === socket) socketRef.current = null;
      activeRef.current = null;
    };
  }, [websocketUrl]);

  function startSearch() {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN || activeRef.current) return;
    const sessionId = crypto.randomUUID();
    activeRef.current = { sessionId, searchId: null };
    setBusy(true);
    setPreviews({});
    setResult(null);
    setStatus("Starting search…");
    try {
      socket.send(JSON.stringify({
        action: "search", request: { ...request, session_id: sessionId },
      }));
    } catch {
      activeRef.current = null;
      setBusy(false);
      setStatus("Couldn't send the search request");
    }
  }

  return (
    <section>
      <button disabled={!connected || busy} onClick={startSearch}>Start search</button>
      <p aria-live="polite">{status}</p>
      {Object.entries(previews).map(([origin, preview]) => (
        <figure key={origin}>
          <figcaption>{origin}: {preview.status ?? "waiting"}</figcaption>
          {preview.src && <img src={preview.src} alt={`Live browser for ${origin}`}
            style={{ width: "100%", maxWidth: 1280 }} />}
        </figure>
      ))}
      {result !== null && <pre>{JSON.stringify(result, null, 2)}</pre>}
    </section>
  );
}
```

Use a regular `<img>` for rapidly changing data URLs. If your frontend has a
Content Security Policy, allow `data:` images and the backend URL in `connect-src`.
The component retains the last frame after the stream ends or disconnects and
clears it when starting another search. Render `result.flights` or `result.hotels`
as your own cards/table once the final record arrives.

Example page for flights:

```tsx
import LiveSearch from "./components/LiveSearch";

export default function Page() {
  return <LiveSearch
    websocketUrl={process.env.NEXT_PUBLIC_FLIGHT_WS_URL!}
    request={{
      origins: ["YVR"], destination: "NRT",
      departure_date: "2027-04-10", return_date: "2027-04-20",
      trip_type: "round_trip", currency: "CAD",
    }}
  />;
}
```

For hotels, use `NEXT_PUBLIC_HOTEL_WS_URL` and replace the request with:

```tsx
request={{
  destination: "Tokyo, Japan", check_in: "2027-04-10", check_out: "2027-04-20",
  adults: 2, rooms: 1, currency: "CAD",
}}
```

Choose future travel dates when running a search. Optional `budget` is the fare
per person for flights or total stay price for hotels.

## Production backend

A hosted HTTPS frontend needs a public `wss://` endpoint. Neither deployed Lambda
has one today, and a function ARN is not a WebSocket URL. Deploying this Next.js
component alone won't make the local bridge accessible to users.

Provide a persistent backend/orchestrator that owns client connections, starts
search workers, and forwards the events above. Directly invoking the current
Lambda returns only final JSON; it doesn't deliver live frames. The worker needs
a transport listener around `run_search`/the handler, equivalent to the local
bridge's `forward_updates`, to capture and route frames. A separate worker process
also needs a channel to send its events back to the connection-owning backend.

Before using this beyond the POC, implement authentication/authorization, trusted
frontend origins, session-to-job routing, resource limits, and reconnect/result
recovery. Keep frame traffic ephemeral and apply backpressure as the local bridge
does. Configure separate flight/hotel public URLs for the client example.
