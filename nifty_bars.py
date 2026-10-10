"""Exact-percentage close-based bricks with explicit, unsplit gap bridges.

Python 3.10+. The converter/CLI use only the standard library. API/plotting
dependencies are in requirements.txt. Run `python nifty_bars.py --help`.

Normal OHLC is synthetic Renko-style geometry based on completed minute closes.
Source OHLC stays separate; excess displacement carries into subsequent bricks.
Calculations use Decimal; JSON prices are floats. No tick path is reconstructed.
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


def convert_minutes(candles: Iterable[Candle], percentage=1, *, include_partials=True) -> dict:
    """Build exact-percentage close-based bricks, carrying excess forward.

    Normal OHLC is synthetic: O=anchor, C=anchor*(1 +/- p/100), H=max(O,C),
    L=min(O,C). Consume all thresholds crossed by a minute CLOSE, then retain
    the final anchor for the next minute. Never round a 1.4% source move into
    a 1.4% normal brick. Actual source OHLC is separate audit metadata.

    Session/missing-data gaps stay one unsplit endpoint bridge; reset the
    anchor to the next observed open. Session/end-of-input residuals are
    partials, included by default to connect every price boundary. Flat sessions
    retain a zero-height partial. No intraminute price path is reconstructed.
    """
    p = parse_percent(percentage)
    fraction = p / HUNDRED
    up_factor, down_factor = Decimal(1) + fraction, Decimal(1) - fraction
    if up_factor == 1 or down_factor == 1:
        raise ValueError("percentage is too small for Decimal precision")
    bars = []
    anchor = span_start = observed = previous = first = None
    pending_count = row_count = missing_intervals = normalized_labels = raw_rows = 0
    sessions = {}
    merged_buckets = []

    def ohlc_dict(c):
        return {k: float(getattr(c, k)) for k in ("open", "high", "low", "close")}

    def emit(kind, start, end, known_at, o, close, count, reason, observation=None, trigger=None):
        if len(bars) >= 1_000_000:
            raise ValueError("Output exceeds 1,000,000 bars; choose a larger percentage")
        # For normal bricks this is defined by the exact percentage construction.
        # JSON float price ratios can differ by ordinary floating-point epsilon.
        move = (p if close > o else -p) if kind == "move" else (close - o) / o * HUNDRED
        bars.append({
            "bar_id": len(bars), "kind": kind,
            "start": start.isoformat(), "end": end.isoformat(), "known_at": known_at.isoformat(),
            "open": float(o), "high": float(max(o, close)),
            "low": float(min(o, close)), "close": float(close),
            "move_pct": float(move), "range_pct": float(abs(move)),
            "direction": "up" if close > o else "down" if close < o else "flat",
            "source_minutes": count,
            "synthetic_ohlc": True, "synthetic_bridge": kind in ("session_gap", "data_gap"),
            "threshold_reached": kind == "move" or abs(move) >= p,
            "finalized": kind != "partial" or reason != "end_of_input",
            "reason": reason,
            "observed_ohlc": {k: float(v) for k, v in observation.items()} if observation else None,
            "trigger_minute": ({"timestamp": trigger.timestamp.isoformat(), **ohlc_dict(trigger)}
                               if trigger is not None else None),
        })

    def flush(reason, known_at=None):
        nonlocal anchor, span_start, pending_count, observed
        if anchor is not None and (pending_count or previous.close != anchor):
            emit("partial", span_start, previous.timestamp + MINUTE,
                 known_at or previous.timestamp + MINUTE, anchor, previous.close,
                 pending_count, reason, observed, previous)
        anchor = span_start = observed = None
        pending_count = 0

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
                missing_intervals += int(missing)
                normal_endpoints = (previous.timestamp.time() == time(15, 29)
                                    and c.timestamp.time() == time(9, 15))
                kind = "session_gap" if day_changed and normal_endpoints else "data_gap"
                reason = ("observed_session_close_to_open" if kind == "session_gap"
                          else "missing_minutes" if missing else "nonstandard_session_endpoints")
                if kind == "data_gap" or c.open != previous.close:
                    emit(kind, previous.timestamp + MINUTE, c.timestamp, c.timestamp,
                         previous.close, c.open, 0, reason)
        if anchor is None:
            anchor, span_start = c.open, c.timestamp
        pending_count += 1
        if observed is None:
            observed = {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
        else:
            observed["high"] = max(observed["high"], c.high)
            observed["low"] = min(observed["low"], c.low)
            observed["close"] = c.close
        while True:
            upper, lower = anchor * up_factor, anchor * down_factor
            if upper == anchor or lower == anchor:
                raise ValueError("percentage is too small for Decimal precision at this price")
            if c.close >= upper:
                target = upper
            elif c.close <= lower:
                target = lower
            else:
                break
            emit("move", span_start, c.timestamp + MINUTE, c.timestamp + MINUTE,
                 anchor, target, pending_count, "exact_percentage_close_projection", observed, c)
            anchor = target  # KEEP the unused displacement; never reset to c.close/c.open.
            span_start = c.timestamp + MINUTE
            pending_count, observed = 0, None
        previous = c
    if first is None:
        raise ValueError("No minute candles supplied")
    flush("end_of_input")
    irregular = [{"date": str(day), "first": a.isoformat(), "last": b.isoformat(), "minutes": n}
                 for day, (a, b, n) in sessions.items()
                 if a.time() != time(9, 15) or b.time() != time(15, 29) or n != 375]
    visible = bars if include_partials else [b for b in bars if b["kind"] != "partial"]
    return {"metadata": {
        "algorithm_version": "2.2.0", "percentage": float(p), "mode": "exact_percentage_close",
        "include_partials": include_partials,
        "timezone": "Asia/Kolkata", "timestamp_convention": "minute_start",
        "first_minute": first.timestamp.isoformat(), "last_minute": previous.timestamp.isoformat(),
        "source_minutes": row_count, "observed_sessions": len(sessions), "source_rows": raw_rows,
        "bar_counts": dict(Counter(b["kind"] for b in bars)),
        "returned_bar_counts": dict(Counter(b["kind"] for b in visible)),
        "missing_intraday_intervals": missing_intervals,
        "timestamp_labels_floored_to_minute": normalized_labels,
        "merged_minute_buckets": merged_buckets,
        "sessions_outside_normal_375_minute_template": irregular,
        "notes": [
            "1 means 1%. Each normal move brick has exactly +/-p% body and p% high-low range relative to its own open, within numerical precision.",
            "Normal OHLC is a synthetic close-based projection without wicks; observed OHLC is separate and never assigned as a brick's high/low.",
            "Each crossed percentage threshold emits a brick; the remaining displacement carries within the session. Reversal uses one brick.",
            "Only minute closes confirm bricks. Multiple bricks from one close share known_at; intraminute crossing times are unknown.",
            "source_minutes counts newly consumed observations; extra bricks from the same minute count zero. Sum counts only with partials included.",
            "Every session starts a fresh anchor. Daily/final residuals are included by default, followed by unsplit gaps. A flat session retains a zero-height partial.",
            "Remainder percentages use their own open: 100 to 104 with p=3 gives 100 to 103 (+3%) then 103 to 104 (+0.9708737864%), not a rounded +1% brick.",
            "Bridges preserve the full endpoint gap. Gap high/low do not imply a traded path. Session gaps use last observed minute close, not official daily close.",
            "No exchange calendar is applied: missing whole sessions, special sessions and halts cannot be definitively classified.",
            "Pass only completed source minutes. The final end_of_input partial can change as more input arrives.",
            "CSV minute-start IST labels are floored; minute collisions use first/max/min/last unless error policy is selected.",
        ],
    }, "bars": visible}


def filter_result(result: dict, start: date | None = None, end: date | None = None,
                  *, include_partials=True) -> dict:
    """Filter AFTER conversion so a chart window cannot alter historical blocks.

    Use the event's IST date (end date). Thus a session-gap belongs to its opening
    date. Bars crossing a requested boundary retain their complete OHLC.
    """
    if start and end and start > end:
        raise ValueError("start must be on or before end")
    bars = [b for b in result["bars"]
            if (include_partials or b["kind"] != "partial")
            and (start is None or date.fromisoformat(b["end"][:10]) >= start)
            and (end is None or date.fromisoformat(b["end"][:10]) <= end)]
    return {"metadata": {**result["metadata"],
                         "display_start": str(start) if start else None,
                         "display_end": str(end) if end else None,
                         "display_bar_count": len(bars),
                         "include_partials": include_partials,
                         "returned_bar_counts": dict(Counter(b["kind"] for b in bars))}, "bars": bars}


def make_chart(result: dict):
    """Each output bar gets one x position; gap bodies retain actual price size."""
    import plotly.graph_objects as go
    fig = go.Figure()
    positions = {b["bar_id"]: i for i, b in enumerate(result["bars"])}
    colors = {"move": ("#15803d", "#dc2626"), "session_gap": ("#2563eb", "#9333ea"),
              "partial": ("#86cba0", "#ef9a9a"), "data_gap": ("#d97706", "#d97706")}
    for kind, (up, down) in colors.items():
        rows = [b for b in result["bars"] if b["kind"] == kind]
        if not rows:
            continue
        hover = [f"{b['kind']} | {b['move_pct']:+.4f}%<br>{b['start']} → {b['end']}"
                 f"<br>O {b['open']} H {b['high']} L {b['low']} C {b['close']}"
                 f"<br>{b['source_minutes']} source minutes; {b['reason']}"
                 for b in rows]
        fig.add_trace(go.Candlestick(
            name=kind, x=[positions[b["bar_id"]] for b in rows],
            open=[b["open"] for b in rows], high=[b["high"] for b in rows],
            low=[b["low"] for b in rows], close=[b["close"] for b in rows],
            increasing_line_color=up, increasing_fillcolor=up,
            decreasing_line_color=down, decreasing_fillcolor=down,
            hovertext=hover, hoverinfo="text"))
    rows = result["bars"]
    ticks = rows[::max(1, len(rows) // 8)]
    fig.update_layout(
        title=f"NIFTY 50 • {result['metadata']['percentage']:g}% exact close-based bricks",
        template="plotly_white", height=680,
        yaxis_title="Index level (calculated brick boundaries)",
        xaxis_title="Sequential blocks • equal spacing, variable elapsed time",
        xaxis_rangeslider_visible=False,
        xaxis=dict(tickmode="array", tickvals=[positions[b["bar_id"]] for b in ticks],
                   ticktext=[b["end"][:16].replace("T", " ") for b in ticks]),
        legend=dict(orientation="h", y=1.08), margin=dict(b=130),
        annotations=[dict(x=0, y=-0.20, xref="paper", yref="paper", showarrow=False,
                         text="Blue/purple = full session gaps; amber = unknown intervals; light green/red = remainders.")])
    return fig


def create_app(csv_path: str | Path = DEFAULT_CSV):
    """Local read-only FastAPI service. File must be downloaded before startup."""
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    app = FastAPI(title="NIFTY percentage OHLC blocks", version="2.2.0")
    source = Path(csv_path).resolve()
    static = Path(__file__).with_name("static")
    app.mount("/assets", StaticFiles(directory=static), name="assets")

    @lru_cache(maxsize=4)
    def calculate(p: str, mtime_ns: int, size: int):
        # Cache key includes file state; errors are not cached.
        return convert_minutes(read_minutes(source), p, include_partials=True)

    def get_result(percentage, start, end, include_partials):
        try:
            p = parse_percent(percentage)
            if start and end and start > end:
                raise ValueError("start must be on or before end")
            stat = source.stat()
            data = calculate(str(p.normalize()), stat.st_mtime_ns, stat.st_size)
            return filter_result(data, start, end, include_partials=include_partials)
        except FileNotFoundError:
            raise HTTPException(503, "CSV missing. Run python nifty_bars.py --download first.")
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/bars")
    def bars(percentage: str = Query("1", description="1 or 1% means one percent"),
             start: date | None = None, end: date | None = None, include_partials: bool = True):
        return get_result(percentage, start, end, include_partials)

    @lru_cache(maxsize=1)
    def calculate_history(mtime_ns: int, size: int):
        from daily_history import daily_history
        return daily_history(source)

    @app.get("/history")
    def history(start: date | None = None, end: date | None = None):
        try:
            stat = source.stat()
            return filter_result(calculate_history(stat.st_mtime_ns, stat.st_size), start, end)
        except FileNotFoundError:
            raise HTTPException(503, "CSV missing. Run python nifty_bars.py --download first.")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/chart")

    @app.get("/chart", response_class=HTMLResponse)
    def chart():
        # The lightweight shell loads first; its controls fetch /bars or /history.
        return HTMLResponse(static.joinpath("chart.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-cache"})

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
    parser.add_argument("--include-partials", action=argparse.BooleanOptionalAction, default=True,
                        help="Include daily/final remainders (default); --no-include-partials creates a sparse export")
    parser.add_argument("--serve", action="store_true", help="Serve /bars, /chart and /docs locally")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.download:
        download_csv(args.csv)
    if args.serve:
        import uvicorn
        uvicorn.run(create_app(args.csv), host="127.0.0.1", port=args.port)
        return
    result = filter_result(convert_minutes(read_minutes(args.csv), args.percentage, include_partials=True),
                           args.start, args.end, include_partials=args.include_partials)
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
