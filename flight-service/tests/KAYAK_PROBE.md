# KAYAK browser probe — 2026-10-03

Three fresh Skyvern cloud browser sessions searched YVR → NRT, 2027-04-10
through 2027-04-20, round trip, one adult, economy, CAD.

Direct results URL:

`https://www.ca.kayak.com/flights/YVR-NRT/2027-04-10/2027-04-20?sort=price_a&currency=CAD`

| Run | Browser ready | First priced cards | Later observation |
| --- | ---: | ---: | --- |
| 1 | 11.97s | 23.38s | At 28.48s, cards existed but search was still 50% complete |
| 2 | 8.21s | 19.80s | At 25.00s, the page said results ready |
| 3 | 11.29s | 22.02s | Results ready first observed at 24.23s; completion + stability guard did not pass by 81.26s |

Times include browser startup. All three navigations returned HTTP 200 and
showed real priced cards; no access-denied or CAPTCHA page was observed.
Run 3 extracted eight flights into the existing service fields, checking both
leg airport labels, CAD price syntax, times, duration, and outbound stops.
These are exploratory observations on one route/date pair, not a production
reliability guarantee or a Google Flights speed comparison.

## DOM selectors observed

| Data | Locator within each `.nrc6` card |
| --- | --- |
| Accessible card group | `[role="group"][aria-label^="Result item "]` |
| Airline display | `.J0g6-operator-text` |
| Displayed price | `.e2GB-price-text` |
| Flight legs | `.hJSA-item` (outbound then return) |
| Leg airline/airports/times label | `input[aria-label^="Leg "]` |
| Leg departure/arrival text | `.VY2U .vmXl` |
| Leg stops | `.JWEO .vmXl` |
| Leg duration | `.xdW8 .vmXl` |
| Booking link | `a[href*="/book/flight"]` |

Search controls have role `button` and accessible names such as `Round-trip`,
`Flight origin input Vancouver`, `Flight destination input Tokyo`,
`Departure date Sat 10/4`, `Return date Tue 20/4`, `1 adult`, and `Economy`.
Controls appear twice in the page, so callers must scope or disambiguate them.
Full dates including the year were encoded in the results URL; full-year
calendar validation was not exercised.

## Remaining work before a Lambda adapter

- The completion banner can revert from `Results ready.` to `50% complete.`.
  Run 3 hit the bounded deadline despite readable results. Define a stopping
  rule that separately tracks stable eligible cards and provider completion;
  mark incomplete searches explicitly rather than claim fully settled fares.
- CSS classes above are observed implementation details and can change.
  Prefer accessible selectors where available and reject malformed cards.
- Validate full search settings independently, including dates/year, ticket
  type, passengers, currency, and per-person round-trip price basis.
- Preserve both legs for deduplication. Several cards share the same outbound
  but have different return options; outbound-only deduplication loses trips.
- Booking links include session-specific tokens; their lifetime and ability
  to reopen in another browser have not been tested.
- Production search, Lambda, backend, and frontend are not changed by this probe.

## Re-run

From `flight-service`, with the existing Skyvern key in `.env`:

```sh
../.venv/bin/python tests/probe_kayak.py --runs 2 --start-run 4
```

Each run uses a fresh cloud browser and closes it afterwards. JSON snapshots,
card HTML, normalized samples, and screenshots are saved under the ignored
`artifacts/playwright-kayak-*` paths. The first two historical runs captured a
five-second follow-up snapshot; the current script uses the stricter guard
introduced for run 3. Artifact timing includes screenshot work in
`search_finished_seconds`; use `first_priced_cards_seconds` for first fares.
