# Trip.com browser probe

Fresh Skyvern browsers searched YVR → NRT, April 10–20, 2027, one adult,
economy, CAD. All navigations returned HTTP 200; no access denial or robot
challenge was observed. This is evidence for one route, not deployment coverage.

| Final measured run | Browser ready | Eight outbound fares | Stable for five seconds |
| --- | ---: | ---: | ---: |
| 4 | 9.72s | 36.08s | 42.04s |
| 5 | 8.20s | 32.10s | 37.58s |

Times include browser startup. Run 4 selected the cheapest outbound; return fares
appeared at 47.50 seconds, 5.46 seconds after selection. The return list showed
NRT → YVR departing April 20 and a CAD 1,289 round-trip total for the cheapest
pair. No return fare, booking, personal data, or payment was submitted.

An earlier exploratory run detected calendar prices before actual fares, so its
first-price timer must not be used. Another run received an alternate card
layout that the initial detector missed. The final probe handles both layouts.

## DOM evidence

- Card scope: `[data-testid^="u-flight-card-"]`, with `u_select_btn`.
- Price: `[data-testid^="flight_price_"]`; fare basis: `flight-price-tag`.
- Airline: `flights-name`, or `.airline-info .flight-name` in the alternate layout.
- Duration: `flightInfoDuration`, or `.travel-duration [role="group"]`.
- Times: `[data-testid^="flight-time-"]`; IDs contain full local timestamps.
- Airport codes: the time element's enclosing `[role="textbox"]`.
- Stops: `stopInfoText`/`stopDot`, or `.stop-city-text`/`.stop-num`.
- Full dates: `search_date_depart0` and `search_date_return0` expose `data-date`.
- Passenger/cabin attributes: `[data-search-pac-panel="true"]`.

The broad flight accessibility label incorrectly described layover time as total
duration in the captured sample. Read the dedicated duration element instead.
Round-trip outbound cards do not contain a selected return itinerary. Prices
and return details must be read again after selecting that outbound.

## Scope and rerun

`probe_trip.py` measures eight outbound cards and inspects the first outbound's
return list. It stops the batch on access denial or verification. It does not
exercise the production adapter's full eight-pair selection, Lambda, Mongo,
callback, recording archive, or dashboard path. Other routes, one-way searches,
other currency layouts, and return-to-outbound navigation need coverage.

From `flight-service`:

```sh
../.venv/bin/python tests/probe_trip.py --runs 2 --start-run 6
```

Ignored JSON, HTML and screenshots are saved under `artifacts/playwright-trip-*`.
The production adapter replaces KAYAK; the probe and historical KAYAK evidence
are kept separately from the Lambda image.
