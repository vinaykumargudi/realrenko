> Archived version 2.3 behavior. Use `session_mode=daily` / `--session-mode daily` for its daily remainders. Current defaults and stock downloads are documented in the root README.

# RealRenko — percentage / ATR bricks, daily remainders and full-size gaps


## Version 2.3: ATR sizing and new-high markers

Choose **Sizing → ATR · daily**, then select **7**, **10**, **14**, or enter a
custom positive whole-number length and press **Apply**. Percentage sizing
remains available. The **ATR length in the top toolbar sizes Renko bricks**;
it is separate from the ATR length inside the Supertrend indicator settings.

- Chart: http://127.0.0.1:8000/chart?sizing=atr&atr_period=14
- API: http://127.0.0.1:8000/bars?sizing=atr&atr_period=7
- Custom example: http://127.0.0.1:8000/chart?sizing=atr&atr_period=21

### How ATR sets the brick size

The period counts **daily candles**, not minute candles or Renko bricks.
The current implementation uses **1 × prior daily Wilder ATR** as the full
brick's point size for the entire next observed session. For example, a
120-point ATR gives full bricks of +120 or −120 points; their percentages will
vary with price. Reversals still use one brick. Daily remainders and full-size
opening gaps remain visible, using the same session reset as percentage mode.

For each eligible daily candle:

```text
TR = max(high − low, abs(high − previous close), abs(low − previous close))
first TR = high − low
initial ATR = mean(first N true ranges)
next ATR = (previous ATR × (N − 1) + new TR) / N
```

[ATR formula and RMA reference](https://www.tradingview.com/support/solutions/43000501823-average-true-range-atr/).
We aggregate actual minute OHLC into daily OHLC, then use only earlier daily
candles for sizing. A session's own range never changes its brick size, and a
later daily ATR never resizes older bricks. Changing the display dates happens
after conversion and keeps the prior history used for warm-up.

A daily candle is eligible only if all **375 minutes, 09:15–15:29 IST**, are
observed. Incomplete or nonstandard prior sessions are skipped for ATR and
listed in `atr_skipped_prior_sessions`; the previous ATR carries forward. The
next eligible true range references the last eligible close, so a gap across
excluded dates is included in that range. No exchange calendar is inferred.
This explicit convention can differ from another charting platform's ATR Renko.

Until N eligible prior daily candles exist, **warm-up remainders and gaps still
appear**, but no full ATR bricks are invented. Zero ATR behaves the same way
until a positive prior ATR exists. The inspector shows **Target brick size**
and **ATR through** for each selected brick. ATR mode returns:

- `metadata.sizing = "atr"`, `mode = "prior_daily_atr_close"`, `percentage = null`.
- `atr_period`, `atr_timeframe = "1D"`, `atr_smoothing` and the sizing policy.
- Per brick: `brick_size_points`, `atr_value`, `atr_as_of`, and
  `atr_status` (`ready`, `warmup`, or `zero_atr`).
- `move_pct` remains the actual movement divided by that brick's own open.

Python and CLI:

```python
from nifty_bars import convert_minutes, read_minutes
result = convert_minutes(read_minutes("nifty50_candlestick_data.csv"),
                         sizing="atr", atr_period=14)
```

```bash
python nifty_bars.py --sizing atr --atr-period 7 --output atr7.json --html atr7.html
```

### Spot a breakout to a new history high

The **● ATH*** toggle is on by default. Gold dots mark bricks/daily candles
whose source observations exceeded every earlier observed source high. The
**ATH*** text appears at the start of a consecutive run of marked bricks to
reduce clutter. Select the brick to see the prior record, new source high,
first breakout time and number of new-high observations grouped into it.
Standalone `--html` exports include equivalent gold markers.

**ATH* means a record within the available CSV history, which starts on
9 January 2015. It is not a verified lifetime all-time high.** The first source
high seeds the baseline without a breakout marker; equal highs do not qualify.
The rule is a strict break of a **high**, not a close-confirmed breakout. A wick
can set a new record even if the minute closes lower. Renko OHLC stays unchanged.

The comparison is calculated over all source history before date filtering,
using original minute highs, not synthetic brick highs. A gap open beyond the
record marks the gap itself at its observed opening time. Intraminute highs
are known only at minute completion. Several new highs can belong to one brick;
extra bricks from the same confirming minute do not duplicate the event.
A record made before a full brick forms is attached to that brick once it
forms, or to its remainder. Its original event time remains in the inspector.
These historical markers are annotations, not real-time trade alerts.

API `ath_breakout` is null or contains `previous_high`, `high`, `first_at`,
`last_at`, `known_at` and `count`. Metadata records `ath_reference`,
`ath_history_start`, `ath_latest_high` and `ath_breakout_observations`.

## Version 2.2: always show the daily remainder

The Renko chart now draws touching, wick-free rectangles. It always includes
**full percentage bricks → daily remainder → next opening gap**, so each
brick's close equals the next brick's open. Weekends and overnight intervals
have no empty time slots. Remainders are lighter green/red bricks, gap ups are
blue, gap downs are purple, and a flat session is a horizontal tick at its
unchanged price. A large gap remains one large brick.

For a **3%** setting, assuming a steady move from an opening price of 100:

| Day's close | Day's bricks | Next open at +1% | Gap brick |
| --- | --- | --- | --- |
| 102 (+2%) | 100 → 102 remainder | 103.02 | 102 → 103.02 |
| 104 (+4%) | 100 → 103 full brick; 103 → 104 remainder | 105.04 | 104 → 105.04 |
| 96 (−4%) | 100 → 97 full brick; 97 → 96 remainder | 96.96 | 96 → 96.96 |

Every percentage is measured from that brick's own open. The 103 → 104
remainder is **1 price point**, or **0.9708737864%**, and is not rounded to 1%.
This preserves both the exact 3% full bricks and actual closing prices. A day
with reversals can create additional full bricks along its minute-close path;
the examples above describe steady moves, not a rule based only on daily net change.

Python, `/bars`, and CLI exports include remainders by default. The chart
always includes gaps and remainders, including when opening an older URL with
`include_partials=false` or `gaps=false`. Explicit sparse API/CLI exports remain
available through `include_partials=false` / `--no-include-partials`; those
exports deliberately omit price connections. `kind="partial"` remains the API
field for a remainder, for compatibility.

## Interactive chart workspace

Open **http://127.0.0.1:8000/chart** after starting the existing API. The new UI
uses the locally bundled TradingView Lightweight Charts 5.2.1 library. There is
no CDN request, paid charting subscription, or frontend build step.

- **Drag left/right or up/down** to move the chart. Dragging turns off price
  autoscaling so vertical movement is possible. **Auto** restores price fitting.
- Scroll/pinch to zoom, drag either axis to scale, or use +/− controls and keys.
- Use **Latest blocks** for a readable recent window, **Fit** for all loaded
  bars, and **Log** for a logarithmic price scale. Home fits; End shows the latest.
- Hover for OHLC and timestamps, click a block to pin it, or use the inspector.
- Add horizontal price levels with the — tool; clear them with the erase button.
- Percentage presets (including 3%), date inputs, light/dark mode,
  fullscreen and PNG export are included. Fullscreen availability depends on
  the browser. PNG export captures the chart canvas.

### See NIFTY's actual long-term movement

The **View** selector offers **Renko blocks**, **NIFTY daily trend**, and
**NIFTY daily candles**. **Renko blocks** is the default. The **2015–20**
shortcut keeps your selected view and changes only the date window. To see
larger individual bricks, zoom in; **Latest blocks** starts with a readable
recent window. Daily price views remain available through the View selector.

The daily views use real daily OHLC aggregated from the provided minute data
through `/history`, not percentage-block geometry. Prices are not rebased to
100 or normalized. In the source window from 9 January 2015 to 31 December
2020, first observed open is 8,285.45 and last observed close is 13,967.00.
See `examples/daily_price_validation.json` for the full-source check. These are
observed source prices, not official NSE daily settlement/closing calculations.

The **Loaded window** change is first displayed open to last displayed close;
it is neither a trading return nor the change of only the zoomed viewport.
Daily candles with incomplete/nonstandard minute coverage are identified in
the inspector. The supplied history ends in March 2024, so this is historical
charting rather than a live market feed.

### Indicators

Open **ƒx Indicators** to enable:

- **Supertrend**: adjustable ATR length (default 10) and factor (default 3),
  using Wilder ATR and trailing upper/lower bands.
- **EMA**: adjustable length (default 20), with a first-close recursive seed.
- **SMA**: adjustable length (default 50), with the normal full-window warm-up.

Daily views calculate on actual daily OHLC. Renko indicators calculate on all
full bricks, daily remainders, and gap bars, including the current unfinished
remainder. That last remainder and its indicator values can change when more
minutes arrive. A period counts bricks, not minutes or days. The controls
identify this input. These are indicators on this custom Renko variant; values
can differ from a standard Renko platform or a different data feed, candle
type, history length or initialization.

All available earlier history is loaded for indicator warm-up before applying
the date window; panning/zooming never restarts an indicator. Insufficient ATR
or SMA warm-up stays blank rather than displaying invented values. Supertrend
is shown as a stepped line, colored green for bullish and red for bearish.

These are built-in JavaScript implementations of public indicator formulas.
There is no Pine Script interpreter or automatic TradingView community-script
import. Formula references: [Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/)
and [ATR / RMA](https://www.tradingview.com/support/solutions/43000734653-how-are-adr-and-atr-calculated/).

### UI implementation and checks

`static/chart.html`, `chart.css`, and `chart.mjs` provide the interface.
`chart-data.mjs` preserves original OHLC and assigns unique, equally spaced
internal coordinates to each displayed bar. Multiple bricks confirmed in one
minute remain separate; labels and crosshair dates map back to real IST times.
Internal coordinates are never presented as market timestamps.

`static/renko-series.mjs` implements full-width brick rendering using the
[custom series API](https://tradingview.github.io/lightweight-charts/docs/5.0/plugins/custom_series).
Rectangles share horizontal boundaries; their height comes from the exact
open/close price coordinates. A minimum one-pixel stroke keeps flat or tiny
bricks visible without changing stored prices. There are no source wicks.

`static/indicators.mjs` holds the testable indicator calculations.
The bundled chart library, Apache-2.0 license, and TradingView copyright notice
are in `static/vendor/`. Required attribution remains visible in the UI.

Python checks (30 tests): `python -m unittest -v` after installing
`requirements-dev.txt`. Optional frontend developer checks (Node 20+):

```bash
npm ci
npm test
```

Nine frontend tests cover hand-calculated indicators, no-lookahead behavior,
actual chart OHLC, duplicate-time bricks, historical price levels, touching
rectangle geometry at different pixel ratios, source-high markers, custom ATR
controls, and DOM control interactions. The DOM smoke test uses a stubbed chart engine. A real
browser/canvas visual and gesture check was not available in the build
environment; the test is not a substitute for that check.

`/chart` serves the new interface. `make_chart()` and CLI `--html` continue to
produce the older, standalone Plotly export for compatibility.


Convert NIFTY 50 minute history into fixed-percentage Renko-style blocks, return
OHLC through FastAPI, and plot overnight gaps as one full-size bar.

## Version 2 fixes normal-bar overshoots

**`percentage=1` now makes every completed normal block exactly +1% or -1%
relative to that block's open.** The previous implementation treated 1% as a
minimum and allowed normal 1.3%, 1.4%, and 2.18% bars. That behavior was wrong
for the requested chart and has been removed.

Excess movement carries into subsequent blocks within the session. Gap bars
keep their complete size. Daily and final remainders are shown by default.

**OHLC semantics changed:** normal blocks now have calculated, wick-free OHLC.
Actual source highs/lows remain separately available in `observed_ohlc` and
`trigger_minute`. Minute OHLC cannot establish the true price path or crossing
time for each synthetic block. This is a close-based projection, not tick
reconstruction, and calculated boundaries are not promised execution prices.

## Run or update

Python 3.10+:

```bash
git clone https://github.com/vinaykumargudi/realrenko.git
cd realrenko
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python nifty_bars.py --download --serve
```

If already installed, stop the running server with Ctrl+C, then:

```bash
git pull --ff-only
python nifty_bars.py --serve
```

Restarting matters: an existing process still holds the previous code and
cached results. Current JSON identifies itself with `algorithm_version = "2.3.0"`.
Percentage mode remains `metadata.mode = "exact_percentage_close"`; ATR mode
is `"prior_daily_atr_close"`.

- JSON: http://127.0.0.1:8000/bars?percentage=1
- Chart: http://127.0.0.1:8000/chart?percentage=1&start=2020-03-01&end=2020-03-31
- Continuous 3% Renko: http://127.0.0.1:8000/chart?percentage=3
- API docs: http://127.0.0.1:8000/docs

`1` means 1%, `0.5` means 0.5%; `1%` is also accepted (encode `%` as `%25`
in URLs). `start` and `end` are inclusive IST dates, `YYYY-MM-DD`. Conversion
precedes date filtering, so changing the window does not change block geometry.

The service reads the local downloaded CSV and caches four sizing/period/file
versions per process. It does not download on each request. To use another
local CSV, pass `--csv PATH` or set `NIFTY_CSV` when running
`uvicorn main:app --host 127.0.0.1 --port 8000`. API defaults to localhost.

## Percentage calculation and leftover movement

Let `A` be the current block anchor and `p` the requested percentage:

```python
upper = A * (1 + p / 100)
lower = A * (1 - p / 100)
```

The first anchor is the observed session open. For each **completed minute
close**, emit every crossed threshold. After each normal block, move the anchor
to that block's calculated close. Keep that anchor for the next minute; do not
reset it to the latest observed price. Reversals need one block, not the
traditional two-block Renko reversal convention.

Examples for `p=1`, starting at 100:

| Observed close | Completed normal blocks | Remainder |
| --- | --- | --- |
| 101.3 | 100 → 101 | 101 → 101.3, pending |
| 101.4 | 100 → 101 | 101 → 101.4, pending |
| 102.18 | 100 → 101 → 102.01 | 102.01 → 102.18, pending |
| 97.82 | 100 → 99 → 98.01 | 98.01 → 97.82, pending |

Percentages **compound**: two +1% blocks total +2.01%. They are not two
fixed one-point blocks. If a close reaches 103.2 from 100, the calculated closes
are 101, 102.01, and 103.0301, leaving the remainder pending.

For each completed normal block:

- Open = previous anchor.
- Close = exactly `open * (1 ± p/100)`.
- High = `max(open, close)`; low = `min(open, close)`.
- Both the body percentage and high-low percentage equal `p` (within numeric
  precision). Raw candle wicks are never attached to these plotted blocks.
- Decimal arithmetic is used for thresholds; JSON prices are floats. Do not
  round prices before plotting or recalculate thresholds from rounded values.

A source minute whose high crosses a level but whose close does not cross it
will not confirm a full percentage brick. Its closing displacement stays in the
remainder, which is emitted at session/data boundaries and at end of input. Multiple blocks from one close share `known_at`; no
intraminute crossing order or timestamps are fabricated.

## Gap handling and session reset

At every session/data boundary, retain the last residual as a separately
labelled partial, then reset the anchor to the next observed open.

| Previous observed close | Next session open | One gap-bar OHLC | Move |
| --- | --- | --- | --- |
| 20,000 | 20,600 | O 20,000; H 20,600; L 20,000; C 20,600 | +3% |
| 20,000 | 19,400 | O 20,000; H 20,000; L 19,400; C 19,400 | -3% |

Gaps are never split into 1% blocks. Their reference is the **actual previous
observed close**, not the last calculated block boundary. Normal blocks after
a gap restart at the new session open. Small nonzero overnight gaps are also
preserved; zero gaps are omitted.

`session_gap` requires normal-session endpoint minute labels 15:29 and 09:15.
Missing intraday intervals or nonstandard session endpoints are `data_gap`:
their endpoint displacement is shown, but the intervening path is unknown.
These gap bodies do not claim trades occurred at intermediate prices.

**Session reset remains enabled.** Remainders carry between minutes within a
session, but do not carry across overnight or missing-data boundaries. They
are emitted as remainder bricks before the next gap and are always displayed
in the chart. If the entire session stays below the threshold, its net closing
move still appears as a remainder. A flat session retains a zero-height record.
If the last source minute already ended exactly at a full brick boundary, no
extra zero-height remainder is needed. Missing data is still identified as an
unknown-interval bridge; the converter does not invent its intraminute path.

## Output and plotting

| Kind | Meaning | Color |
| --- | --- | --- |
| `move` | Exact-percentage completed normal block | Green/red |
| `session_gap` | Unsplit normal-session endpoint gap | Blue/purple |
| `data_gap` | Unsplit unknown-interval endpoint bridge | Amber |
| `partial` | Daily/final remainder, always shown in the chart | Lighter green/red (flat: grey) |

The x-axis uses equal positions for returned blocks; elapsed time varies.
Hover labels include OHLC, percentage, timestamps and bar type. Every normal
block's actual plotted OHLC matches the requested percentage, not just its label.

Key fields:

- `open`, `high`, `low`, `close`: **calculated block geometry**.
- `move_pct`, `range_pct`: block movement/range as percentages of its open.
- `synthetic_ohlc=true`: all returned block geometries are synthetic.
- `synthetic_bridge=true`: only session/data-gap bridges, not normal blocks.
- `observed_ohlc`: separately aggregated actual OHLC for newly consumed input
  minutes; null for additional blocks generated by the same observation.
  These observations are audit context, not an intrabrick trading path.
- `trigger_minute`: the actual minute OHLC that confirmed a normal block or
  last updated a partial; can repeat on multiple blocks. Null for gap bridges.
- `source_minutes`: newly consumed minute count. Extra blocks from the same
  minute have zero. Counts sum to all input minutes **only with partials included**.
- `start`, `end`, `known_at`: source/confirmation bounds. Normal blocks are
  known only at minute end; extra blocks from the same close can have equal
  start/end bounds. Gap endpoints are known at the new open.
- `finalized=false`: final `end_of_input` partial, which can change on more input.

Pass completed input minutes only. This is a batch converter, without persisted
live-stream/chunk continuation state. `metadata.bar_counts` counts all types,
including remainders; `returned_bar_counts` describes the actual response.

## Source data and explicit cleaning

[Source CSV](https://raw.githubusercontent.com/sandeepkapri/Nifty50-Minute-Data/refs/heads/main/nifty50_candlestick_data.csv)
is a third-party historical file, not a live feed or independently verified
tick history. The tested snapshot has 852,087 raw rows from 9 January 2015,
09:15 IST to 27 March 2024, 12:59 IST, across 2,282 observed dates.

- Parse dates as `DD-MM-YYYY`; assume minute-start IST timestamp labels.
- Floor 10,693 nonzero-second labels to the minute, reporting their count.
- Four minute buckets contain two raw rows. Default cleaning uses chronological
  first open / maximum high / minimum low / last close: 852,083 normalized
  minutes. This assumption and affected timestamps appear in metadata.
- Use `read_minutes(path, duplicate_policy="error")` to reject those collisions.
  Raw duplicates, unsorted timestamps, invalid/nonpositive OHLC, NaN and infinity
  always fail validation. There is no silent skipping or forward filling.
- Missing intervals interrupt projection. Normal 375-minute session exceptions
  are reported; no official exchange calendar is applied. Entire missing
  sessions cannot be distinguished from holidays, and special sessions/halts
  may be flagged as unknown boundaries.
- Gap references use the last observed minute close, which can differ from the
  official calculated daily closing index.

## Python, files and verification

```python
from nifty_bars import convert_minutes, read_minutes

result = convert_minutes(read_minutes("nifty50_candlestick_data.csv"), "1%")
blocks = result["bars"]
# Optional: pandas.DataFrame(blocks)
```

```bash
python nifty_bars.py --percentage 1 --output bars.json --html bars.html
python nifty_bars.py --percentage 3 --output bars_3pct.json
python -m pip install -r requirements-dev.txt
python -m unittest -v
python validate_source.py --csv nifty50_candlestick_data.csv --percentage 1 --output validation.json
```

Generated HTML works offline. CSV-to-JSON conversion requires only the standard
library; plotting/API use the declared dependencies.

Version 2 passed **17 tests**, including the reported +/−1.3%, 1.4%, and 2.18%
overshoots, multiple blocks per minute, remainder carry, reversal, full gaps,
partial filtering, API output, and actual OHLC passed to the chart.

The full-source 1% audit checked **1,033 normal blocks**, 2,317 partials, and
2,314 gap/unknown-interval bridges. Every normal block's actual returned body
and range is 1%, with maximum floating-point error approximately 2.31e-14
percentage points. All 852,083 normalized minutes are accounted for with
partials included. See `examples/validation_1pct.json` for checks and source
SHA-256. These counts are conversion results, not strategy returns.


The version 2.2 **3%** source audit checked **47 full bricks**, **2,317
remainders**, and **2,314 session/data-gap bridges**. All **4,677 adjacent
price connections** match exactly in the returned JSON; all **852,083 source
minutes** are accounted for. Full bricks remain exactly 3% to floating-point
precision. See `examples/validation_3pct.json`. These are dataset validation
counts, not performance results.


For the ATR and record-high source audit, run:

```bash
python validate_atr_source.py --csv nifty50_candlestick_data.csv --output validation_atr_and_highs.json
```

This independently aggregates daily true ranges, checks 1% and ATR 7/10/14
brick sizes against prior-session Wilder ATR, verifies price continuity and
source-minute coverage, and matches every grouped breakout back to original
source highs. Results are in `examples/validation_atr_and_highs.json`.
The Python fixtures additionally cover custom periods, warm-up, zero ATR,
incomplete sessions, API/CLI parity, and independence from future prices.
