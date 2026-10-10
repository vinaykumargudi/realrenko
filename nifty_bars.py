"""Close-confirmed percentage OHLC bars with explicit, unsplit gap bridges.

Python 3.10+. The converter/CLI use only the standard library. API/plotting
dependencies are in requirements.txt. Run `python nifty_bars.py --help`.

This is whole-minute aggregation, NOT exact-size Renko or tick reconstruction.
Every input minute belongs to exactly one non-synthetic output bar. Prices are
Decimal throughout calculation; conversion to floats occurs only for output.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

SOURCE_URL = "https://raw.githubusercontent.com/sandeepkapri/Nifty50-Minute-Data/refs/heads/main/nifty50_candlestick_data.csv"
IST = ZoneInfo("Asia/Kolkata")
MINUTE = timedelta(minutes=1)
HUNDRED = Decimal(100)
DEFAULT_CSV = Path(__file__).with_name("nifty50_candlestick_data.csv")


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime  # Start of a COMPLETED one-minute candle, in IST.
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    normalized_labels: int = 0  # Count of original non-zero-second labels.
    source_rows: int = 1


def parse_percent(value: str | float | Decimal) -> Decimal:
    """1, '1', and '1%' mean one percent; 0.01 means 0.01 percent."""
    try:
        p = Decimal(str(value).strip().removesuffix("%").strip())
    except InvalidOperation as exc:
        raise ValueError("percentage must be a number such as 1 or '1%'") from exc
    if not p.is_finite() or not Decimal(0) < p < HUNDRED:
        raise ValueError("percentage must be finite and strictly between 0 and 100")
    return p


def download_csv(destination: Path = DEFAULT_CSV) -> Path:
    """Download the fixed user-supplied source atomically. No arbitrary URL input."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
            temp_path = Path(tmp.name)
            with urllib.request.urlopen(SOURCE_URL, timeout=120) as response:
                shutil.copyfileobj(response, tmp)
        # Check format before replacing an existing download.
        with temp_path.open(encoding="utf-8-sig", newline="") as src:
            fields = next(csv.reader(src))
        if not {"Instrument", "Date", "Time", "Open", "High", "Low", "Close"}.issubset(fields):
            raise ValueError("Downloaded file does not have the expected CSV columns")
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return destination


def read_minutes(path: str | Path, duplicate_policy: str = "merge") -> Iterable[Candle]:
    """DD-MM-YYYY, IST minute-start labels; normalize raw records by minute.

    This source has non-zero seconds and four minute collisions. Default merge
    explicitly uses first open / maximum high / minimum low / last close in
    raw timestamp order. This is a data-cleaning assumption, reported in output.
    Set duplicate_policy='error' to reject collisions instead. Never sort or
    silently deduplicate conflicting source records.
    """
    if duplicate_policy not in ("merge", "error"):
        raise ValueError("duplicate_policy must be 'merge' or 'error'")
    pending = None
    previous_raw = None
    with Path(path).open(encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        required = {"Instrument", "Date", "Time", "Open", "High", "Low", "Close"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Missing CSV columns: {sorted(required - set(reader.fieldnames or []))}")
        for line, row in enumerate(reader, 2):
            try:
                if row["Instrument"].strip() != "Nifty50":
                    raise ValueError("expected only Nifty50; do not mix instruments")
                # Manual parsing is considerably faster than strptime for 850k rows.
                d, m, y = map(int, row["Date"].split("-"))
                hh, mm, ss = map(int, row["Time"].split(":"))
                raw_ts = datetime(y, m, d, hh, mm, ss, tzinfo=IST)
                if previous_raw is not None and raw_ts <= previous_raw:
                    raise ValueError("Duplicate or unsorted raw timestamps")
                previous_raw = raw_ts
                # This minute dataset sometimes labels a candle at hh:mm:01.
                # Explicitly floor LABELS, not prices; collisions use the stated policy.
                ts = raw_ts.replace(second=0)
                current = Candle(ts, *(Decimal(row[k]) for k in ("Open", "High", "Low", "Close")), int(ss != 0))
                validate_candle(current, None)
                if pending is not None and current.timestamp == pending.timestamp:
                    if duplicate_policy == "error":
                        raise ValueError(f"Multiple records in minute {ts.isoformat()}")
                    pending = Candle(ts, pending.open, max(pending.high, current.high),
                                     min(pending.low, current.low), current.close,
                                     pending.normalized_labels + current.normalized_labels,
                                     pending.source_rows + 1)
                else:
                    if pending is not None:
                        yield pending
                    pending = current
            except (ValueError, InvalidOperation, KeyError) as exc:
                raise ValueError(f"Invalid CSV row {line}: {exc}") from exc
        if pending is not None:
            yield pending


def validate_candle(c: Candle, previous: Candle | None) -> None:
    if c.timestamp.tzinfo is None or c.timestamp.utcoffset() is None:
        raise ValueError("Use timezone-aware timestamps")
    if c.timestamp.utcoffset() != timedelta(hours=5, minutes=30):
        raise ValueError("Normalize candle timestamps to Asia/Kolkata before conversion")
    if c.timestamp.second or c.timestamp.microsecond:
        raise ValueError("Expected minute-start timestamps without seconds/fractions")
    if previous is not None and c.timestamp <= previous.timestamp:
        raise ValueError("Duplicate or unsorted timestamps; repair the source explicitly")
    values = (c.open, c.high, c.low, c.close)
    if any(not v.is_finite() or v <= 0 for v in values):
        raise ValueError("All OHLC prices must be finite and positive")
    if not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high:
        raise ValueError(f"Invalid OHLC at {c.timestamp.isoformat()}")


def convert_minutes(candles: Iterable[Candle], percentage=1) -> dict:
    """Aggregate disjoint whole minutes; reset at session/data boundaries.

    A normal block closes when abs(close-open)*100 >= open*percentage.
    Its open is the first constituent minute's open. H/L are true extrema of
    ALL constituent minutes, C is the last minute's actual close. No rounding,
    clipping, high/low ordering assumptions, or splitting of source minutes.

    At a session boundary, finalize a pending block as `partial`, then emit
    one synthetic bridge from the previous observed minute close to the next
    open (even if smaller than the threshold). The next block starts afresh.
    Missing minute intervals also finalize the pending block and receive a
    `data_gap` marker; their unknown path is never treated as observed OHLC.
    """
    p = parse_percent(percentage)
    bars: list[dict] = []
    active = None
    previous = None
    first = None
    row_count = 0
    sessions: dict[date, list] = {}
    missing_intervals = 0
    normalized_labels = 0
    raw_rows = 0
    merged_buckets = []

    def emit(kind, start, end, known_at, o, h, l, c, count, reason, synthetic=False):
        move = (c - o) / o * HUNDRED
        bars.append({
            "bar_id": len(bars), "kind": kind,
            "start": start.isoformat(), "end": end.isoformat(),
            "known_at": known_at.isoformat(),
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "move_pct": float(move), "range_pct": float((h - l) / o * HUNDRED),
            "direction": "up" if c > o else "down" if c < o else "flat",
            "source_minutes": count, "synthetic_bridge": synthetic,
            "threshold_reached": abs(c - o) * HUNDRED >= o * p,
            "finalized": kind != "partial" or reason != "end_of_input",
            "reason": reason,
        })

    def flush(reason, known_at=None):
        nonlocal active
        if active is not None:
            emit("partial", active["start"], active["end"], known_at or active["end"],
                 active["open"], active["high"], active["low"], active["close"],
                 active["count"], reason)
            active = None

    for c in candles:
        validate_candle(c, previous)
        row_count += 1
        normalized_labels += c.normalized_labels
        raw_rows += c.source_rows
        if c.source_rows > 1:
            merged_buckets.append({"minute": c.timestamp.isoformat(), "source_rows": c.source_rows})
        first = first or c
        session = sessions.setdefault(c.timestamp.date(), [c.timestamp, c.timestamp, 0])
        session[1], session[2] = c.timestamp, session[2] + 1
        if previous is not None:
            day_changed = c.timestamp.date() != previous.timestamp.date()
            missing = not day_changed and c.timestamp - previous.timestamp != MINUTE
            if day_changed or missing:
                flush("session_end" if day_changed else "missing_minutes", c.timestamp)
                if missing:
                    missing_intervals += 1
                # NSE normal-session conventions only, not an exchange calendar.
                normal_endpoints = (previous.timestamp.time() == time(15, 29)
                                    and c.timestamp.time() == time(9, 15))
                kind = "session_gap" if day_changed and normal_endpoints else "data_gap"
                reason = ("observed_session_close_to_open" if kind == "session_gap"
                          else "missing_minutes" if missing else "nonstandard_session_endpoints")
                if kind == "data_gap" or c.open != previous.close:
                    emit(kind, previous.timestamp + MINUTE, c.timestamp, c.timestamp,
                         previous.close, max(previous.close, c.open),
                         min(previous.close, c.open), c.open, 0, reason, True)
        if active is None:
            active = {"start": c.timestamp, "open": c.open, "high": c.high,
                      "low": c.low, "count": 0}
        active["high"] = max(active["high"], c.high)
        active["low"] = min(active["low"], c.low)
        active["close"] = c.close
        active["end"] = c.timestamp + MINUTE
        active["count"] += 1
        if abs(c.close - active["open"]) * HUNDRED >= active["open"] * p:
            emit("move", active["start"], active["end"], active["end"],
                 active["open"], active["high"], active["low"], c.close,
                 active["count"], "minute_close_threshold")
            active = None
        previous = c
    flush("end_of_input")
    if first is None:
        raise ValueError("No minute candles supplied")
    irregular = [{"date": str(day), "first": a.isoformat(), "last": b.isoformat(), "minutes": n}
                 for day, (a, b, n) in sessions.items()
                 if a.time() != time(9, 15) or b.time() != time(15, 29) or n != 375]
    return {"metadata": {
        "percentage": float(p), "mode": "whole_minute_close_confirmed",
        "timezone": "Asia/Kolkata", "timestamp_convention": "minute_start",
        "first_minute": first.timestamp.isoformat(),
        "last_minute": previous.timestamp.isoformat(),
        "source_minutes": row_count, "observed_sessions": len(sessions),
        "source_rows": raw_rows,
        "bar_counts": dict(Counter(b["kind"] for b in bars)),
        "missing_intraday_intervals": missing_intervals,
        "timestamp_labels_floored_to_minute": normalized_labels,
        "merged_minute_buckets": merged_buckets,
        "sessions_outside_normal_375_minute_template": irregular,
        "notes": [
            "1 means 1%, not 0.01. Threshold is absolute open-to-close displacement, not high-low range.",
            "Blocks can overshoot; intraminute ordering is unknown. No synthetic exact-size bricks.",
            "Every session starts a new block; unfinished session blocks remain as partial bars.",
            "Bridges are visualization endpoints only: their high/low do not imply traded prices or known paths.",
            "Session gaps use last observed minute close, not the official calculated daily closing index.",
            "No exchange calendar is applied: entirely missing sessions cannot be distinguished from holidays.",
            "375-minute template flags can include legitimate special sessions or trading halts.",
            "Only completed minute candles may be passed in. The last partial block can grow on new input.",
            "CSV labels are assumed minute-start IST and floored; duplicate minute buckets use first/max/min/last unless error policy is selected.",
        ],
    }, "bars": bars}


def filter_result(result: dict, start: date | None = None, end: date | None = None) -> dict:
    """Filter AFTER conversion so a chart window cannot alter historical blocks.

    Use the event's IST date (end date). Thus a session-gap belongs to its opening
    date. Bars crossing a requested boundary retain their complete OHLC.
    """
    if start and end and start > end:
        raise ValueError("start must be on or before end")
    bars = [b for b in result["bars"]
            if (start is None or date.fromisoformat(b["end"][:10]) >= start)
            and (end is None or date.fromisoformat(b["end"][:10]) <= end)]
    return {"metadata": {**result["metadata"],
                         "display_start": str(start) if start else None,
                         "display_end": str(end) if end else None,
                         "display_bar_count": len(bars)}, "bars": bars}


def make_chart(result: dict):
    """Each output bar gets one x position; gap bodies retain actual price size."""
    import plotly.graph_objects as go
    fig = go.Figure()
    colors = {"move": ("#15803d", "#dc2626"), "session_gap": ("#2563eb", "#9333ea"),
              "partial": ("#94a3b8", "#64748b"), "data_gap": ("#d97706", "#d97706")}
    for kind, (up, down) in colors.items():
        rows = [b for b in result["bars"] if b["kind"] == kind]
        if not rows:
            continue
        hover = [f"{b['kind']} | {b['move_pct']:+.4f}%<br>{b['start']} → {b['end']}"
                 f"<br>O {b['open']} H {b['high']} L {b['low']} C {b['close']}"
                 f"<br>{b['source_minutes']} source minutes; {b['reason']}"
                 for b in rows]
        fig.add_trace(go.Candlestick(
            name=kind, x=[b["bar_id"] for b in rows],
            open=[b["open"] for b in rows], high=[b["high"] for b in rows],
            low=[b["low"] for b in rows], close=[b["close"] for b in rows],
            increasing_line_color=up, increasing_fillcolor=up,
            decreasing_line_color=down, decreasing_fillcolor=down,
            hovertext=hover, hoverinfo="text"))
    rows = result["bars"]
    ticks = rows[::max(1, len(rows) // 8)]
    fig.update_layout(
        title=f"NIFTY 50 • {result['metadata']['percentage']:g}% close-confirmed blocks",
        template="plotly_white", height=680,
        yaxis_title="Index level (actual prices)",
        xaxis_title="Sequential blocks • equal spacing, variable elapsed time",
        xaxis_rangeslider_visible=False,
        xaxis=dict(tickmode="array", tickvals=[b["bar_id"] for b in ticks],
                   ticktext=[b["end"][:16].replace("T", " ") for b in ticks]),
        legend=dict(orientation="h", y=1.08), margin=dict(b=130),
        annotations=[dict(x=0, y=-0.20, xref="paper", yref="paper", showarrow=False,
                         text="Blue/purple = full session gaps; amber = unknown intervals; grey = partial blocks.")])
    return fig


def create_app(csv_path: str | Path = DEFAULT_CSV):
    """Local read-only FastAPI service. File must be downloaded before startup."""
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse
    app = FastAPI(title="NIFTY percentage OHLC blocks", version="1.0.0")
    source = Path(csv_path).resolve()

    @lru_cache(maxsize=4)
    def calculate(p: str, mtime_ns: int, size: int):
        # Cache key includes file state; errors are not cached.
        return convert_minutes(read_minutes(source), p)

    def get_result(percentage, start, end):
        try:
            p = parse_percent(percentage)
            if start and end and start > end:
                raise ValueError("start must be on or before end")
            stat = source.stat()
            data = calculate(str(p.normalize()), stat.st_mtime_ns, stat.st_size)
            return filter_result(data, start, end)
        except FileNotFoundError:
            raise HTTPException(503, "CSV missing. Run python nifty_bars.py --download first.")
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/bars")
    def bars(percentage: str = Query("1", description="1 or 1% means one percent"),
             start: date | None = None, end: date | None = None):
        return get_result(percentage, start, end)

    @app.get("/chart", response_class=HTMLResponse)
    def chart(percentage: str = Query("1"), start: date | None = None, end: date | None = None):
        result = get_result(percentage, start, end)
        return HTMLResponse(make_chart(result).to_html(full_html=True, include_plotlyjs=True))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--download", action="store_true", help="Fetch/refresh the supplied source URL")
    parser.add_argument("--percentage", default="1", help="1 or 1%% means one percent")
    parser.add_argument("--output", type=Path, default=Path("bars.json"))
    parser.add_argument("--html", type=Path, help="Also save an interactive candlestick chart")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--serve", action="store_true", help="Serve /bars, /chart and /docs locally")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.download:
        download_csv(args.csv)
    if args.serve:
        import uvicorn
        uvicorn.run(create_app(args.csv), host="127.0.0.1", port=args.port)
        return
    result = filter_result(convert_minutes(read_minutes(args.csv), args.percentage), args.start, args.end)
    with args.csv.open("rb") as src:
        digest = hashlib.sha256()
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    result["metadata"]["source_sha256"] = digest.hexdigest()
    result["metadata"]["source_url"] = SOURCE_URL
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    if args.html:
        make_chart(result).write_html(args.html, include_plotlyjs=True)
    print(json.dumps({k: v for k, v in result["metadata"].items()
                      if k not in ("sessions_outside_normal_375_minute_template", "notes")}, indent=2))


if __name__ == "__main__":
    main()
