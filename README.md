# RealRenko — percentage OHLC blocks with full-size gaps

Convert the supplied NIFTY 50 minute history into user-selected percentage-move
bars, return OHLC through a Python API, and plot opening gaps at their real size.

**Defined behavior:** these are **whole-minute, close-confirmed percentage bars**,
not classic fixed-size Renko bricks. A 1% input is a minimum absolute
open-to-close move. Bars retain overshoots and actual observed wicks. Minute
OHLC does not disclose whether high or low occurred first, so the converter
never fabricates intraminute paths or exactly 1% closes.

## Start the API

Python 3.10 or later:

```bash
git clone https://github.com/vinaykumargudi/realrenko.git
cd realrenko
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python nifty_bars.py --download --serve
```

The first command downloads the fixed source URL only when `--download` is
present. To restart without downloading again:

```bash
python nifty_bars.py --serve
```

Endpoints:

- JSON: http://127.0.0.1:8000/bars?percentage=1
- Interactive chart: http://127.0.0.1:8000/chart?percentage=1&start=2020-03-01&end=2020-03-31
- API docs: http://127.0.0.1:8000/docs

`percentage=1` means **1%**, and `percentage=0.5` means **0.5%**. The string
`1%` is also accepted (URL encode the percent sign as `%25`). Dates use
`YYYY-MM-DD`. Both start and end dates are inclusive. First conversion is
computed from the complete CSV; the last four percentage/file-version results
are cached per process. Date filtering happens afterward so changing the chart
window does not change the blocks. The API binds to localhost by default.

Alternatively, `uvicorn main:app --host 127.0.0.1 --port 8000` uses the
downloaded CSV; set `NIFTY_CSV` to use a different local file with the same schema.

## Exact calculation rules

For a requested percentage `p`, bar open `O`, and latest minute close `C`:

```python
threshold_reached = abs(C - O) * Decimal(100) >= O * p
```

- Open = first constituent minute's actual open.
- High = maximum high of all constituent minutes.
- Low = minimum low of all constituent minutes.
- Close = last constituent minute's actual close.
- Complete the block on the first minute close that reaches either threshold.
- The next block begins with the **next minute's actual open**; it can differ
  slightly from the previous close. Its percentage threshold is recalculated
  from that open. There is no fixed-point brick size or two-brick reversal rule.
- A move from 20,000 to 20,200 is +1%. From a new open of 20,200, the +1%
  level is 20,402 and the -1% level is 19,998.
- A minute closing at +1.18% produces a +1.18% bar. It is not rounded to 1%,
  and excess movement is not copied into another synthetic bar.
- An intraminute high crossing +1% while the close is only +0.2% does not finish
  a bar. The high remains a wick. High-low range can exceed 1% in either kind.
- Calculations use Decimal values parsed directly from CSV strings. JSON
  prices become numbers only at the output boundary; threshold decisions never
  use rounded output percentages.

## Gap up / gap down

At every session change, an unfinished normal block is emitted as `partial`.
The next session starts fresh. This deliberately prevents overnight gaps from
being mixed with the previous session's unfinished move.

| Previous last-minute close | New session open | Gap-bar OHLC | Move |
| --- | --- | --- | --- |
| 20,000 | 20,600 | O 20,000; H 20,600; L 20,000; C 20,600 | +3% |
| 20,000 | 19,400 | O 20,000; H 20,000; L 19,400; C 19,400 | -3% |

**Each is ONE full-size gap bar, even when the requested block size is 1%.**
The next normal block begins at the new session's open. Smaller nonzero gaps
are also shown; a zero overnight gap does not create a redundant bridge.

Gap bars have `synthetic_bridge=true` and zero source minutes: their body
connects two observed endpoints. Their high/low are endpoint extrema, not a
claim that the index traded through every intermediate price. Do not use gap
OHLC as executable trading prices in a backtest.

We use the previous **observed minute close**, which may differ from the
official calculated daily closing index. Normal-session gap classification
requires source minute labels 15:29 and 09:15. This matches the normal
09:15–15:30 session when labels are minute starts. Other boundary times are
flagged as `data_gap`, because the history alone cannot establish a normal
overnight gap. Special sessions and halts require a richer exchange calendar
if you need definitive classification.

**Daily reset means partial bars are expected.** A session that never reaches
the selected percentage still appears as a partial OHLC bar. This is not a
continuous multi-day classic Renko algorithm.

## Plot and output fields

`/chart` uses sequential bar IDs for equal candle spacing and actual index
levels on the y-axis. Gap bodies therefore retain their full size. Hovering
shows original timestamps, OHLC, percentage change, and source minute count.

| `kind` | Meaning | Plot color |
| --- | --- | --- |
| `move` | First completed-minute close reached the percentage threshold | Green / red |
| `session_gap` | Observed normal-session close to next session open | Blue / purple |
| `partial` | Threshold not reached before a session/data boundary or input end | Grey |
| `data_gap` | Missing interval or nonstandard session endpoints; path unknown | Amber |

Every bar includes `open`, `high`, `low`, `close`, `move_pct`, `range_pct`,
`start`, `end`, `known_at`, `bar_id`, `source_minutes`, `threshold_reached`,
`finalized`, `synthetic_bridge`, and `reason`.

`start` is inclusive and `end` is the boundary after the last minute. A normal
bar is known only at that minute's end. Gap endpoints are known at the new open.
A pending block finalized when a discontinuity is detected records the new
timestamp in `known_at`; do not use future finalization information earlier.
The final `end_of_input` partial has `finalized=false` and can grow when new
completed minutes arrive. Only completed source minute candles should be sent
to the converter; it cannot detect an unfinished live candle from OHLC alone.

## Dataset and cleaning assumptions

Source: [Nifty50-Minute-Data CSV](https://raw.githubusercontent.com/sandeepkapri/Nifty50-Minute-Data/refs/heads/main/nifty50_candlestick_data.csv).
No market-data provider credentials are required. This is a third-party
dataset, not independently verified exchange tick history.

The downloaded snapshot has **852,087 rows**, spanning **9 January 2015,
09:15 IST to 27 March 2024, 12:59 IST**, across 2,282 observed dates.
It is not live/current market data.

- Parse `Date` explicitly as `DD-MM-YYYY` and interpret times as IST.
- Assume the source uses minute-start labels. The file itself does not provide
  authoritative tick-level timing metadata.
- **10,693 timestamps contain nonzero seconds.** Normalize these to their
  minute labels, explicitly reporting the count.
- **Four minutes contain two raw records after normalization.** Default
  cleaning merges them chronologically using first open / maximum high /
  minimum low / last close, reducing the series to **852,083 minute buckets**.
  This is an explicit cleaning assumption, not proof of the vendor's intended
  correction policy. Affected minute labels are listed in metadata.
- To reject those four collisions, call
  `read_minutes(path, duplicate_policy="error")`. Raw duplicate or out-of-order
  timestamps are always rejected. Invalid OHLC, NaN, infinity, and nonpositive
  prices also fail validation; no rows are silently skipped.
- Missing intraday intervals interrupt aggregation. A `data_gap` bridge shows
  endpoint displacement and does not invent the missing path.
- Dates outside the normal 375-minute template are listed in metadata.
  No official exchange calendar is applied, so completely missing sessions
  cannot be distinguished from holidays. Never forward-fill missing prices.

## Generate files or use from Python

```bash
python nifty_bars.py --percentage 1 --output bars.json --html bars.html
python nifty_bars.py --percentage 0.5 --start 2024-03-01 --end 2024-03-27 --output bars.json
```

The HTML is self-contained and works offline after generation. The standard
library alone is sufficient for CSV-to-JSON conversion.

```python
from nifty_bars import convert_minutes, read_minutes

result = convert_minutes(read_minutes("nifty50_candlestick_data.csv"), percentage="1%")
bars = result["bars"]
print(bars[0])
# Optional: pd.DataFrame(bars) if your existing application uses pandas.
```

Existing services can call `convert_minutes()` with an iterable of `Candle`
objects whose prices are Decimal and whose timestamps are minute-start IST.
This implementation recomputes a batch; there is no persistent live-stream
state or chunk-boundary continuation API.

## Verification

```bash
python -m pip install -r requirements-dev.txt
python -m unittest -v
python validate_source.py --csv nifty50_candlestick_data.csv --percentage 1 --output validation.json
```

All 14 unit/API tests passed. Tests cover exact threshold equality, decimal
rounding, both gap directions, unsplit overshoots, ambiguous intraminute paths,
missing data, unfinished blocks, normalization, invalid inputs, date filters,
the JSON endpoint, and gap OHLC passed to the plot.

The full-source 1% audit reconstructs every bar's OHLC from its constituent
minutes, checks first threshold crossing, verifies each minute is used exactly
once, and verifies every gap's endpoints. Results and the input SHA-256 are in
`examples/validation_1pct.json`.

The snapshot produces 1,026 completed move bars, 2,315 partial bars, 2,257 normal
session gaps, and 57 unknown-interval bridges. These are conversion counts,
not backtest returns or evidence of strategy profitability.

References: [FastAPI query parameters](https://fastapi.tiangolo.com/tutorial/query-params/),
[Plotly candlesticks](https://plotly.com/python/candlestick-charts/),
[NSE market timings](https://www.nseindia.com/static/market-data/market-timings).
