# RealRenko — exact percentage blocks with full-size gaps

Convert NIFTY 50 minute history into fixed-percentage Renko-style blocks, return
OHLC through FastAPI, and plot overnight gaps as one full-size bar.

## Version 2 fixes normal-bar overshoots

**`percentage=1` now makes every completed normal block exactly +1% or -1%
relative to that block's open.** The previous implementation treated 1% as a
minimum and allowed normal 1.3%, 1.4%, and 2.18% bars. That behavior was wrong
for the requested chart and has been removed.

Excess movement carries into subsequent blocks within the session. Gap bars
keep their complete size. Partial remainders are hidden by default.

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
cached results. Version 2 JSON identifies itself with
`metadata.mode = "exact_percentage_close"` and `algorithm_version = "2.0.0"`.

- JSON: http://127.0.0.1:8000/bars?percentage=1
- Chart: http://127.0.0.1:8000/chart?percentage=1&start=2020-03-01&end=2020-03-31
- Include remainders: http://127.0.0.1:8000/chart?percentage=1&include_partials=true
- API docs: http://127.0.0.1:8000/docs

`1` means 1%, `0.5` means 0.5%; `1%` is also accepted (encode `%` as `%25`
in URLs). `start` and `end` are inclusive IST dates, `YYYY-MM-DD`. Conversion
precedes date filtering, so changing the window does not change block geometry.

The service reads the local downloaded CSV and caches four percentage/file
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
will not produce a block. Multiple blocks from one close share `known_at`; no
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
are retained as partial records and hidden by default; request
`include_partials=true` or CLI `--include-partials` to inspect them. Consequently,
not every observed price change creates a displayed normal block.

## Output and plotting

| Kind | Meaning | Color |
| --- | --- | --- |
| `move` | Exact-percentage completed normal block | Green/red |
| `session_gap` | Unsplit normal-session endpoint gap | Blue/purple |
| `data_gap` | Unsplit unknown-interval endpoint bridge | Amber |
| `partial` | Unfinished residual, only when requested | Grey |

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
including hidden partials; `returned_bar_counts` describes the actual response.

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
python nifty_bars.py --percentage 1 --include-partials --output bars.json
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
