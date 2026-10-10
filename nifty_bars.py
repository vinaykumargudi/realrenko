"""Percentage or daily ATR close-based Renko bricks with unsplit gap bridges.

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
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from atr_sizing import DailyATR, parse_atr_period
from record_highs import RecordHighs, merge_breakouts

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


def read_minutes(path: str | Path, duplicate_policy: str = "merge", *, expected_instrument: str | None = None) -> Iterable[Candle]:
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
    instrument = expected_instrument.upper().removesuffix(".NS") if expected_instrument else None
    with Path(path).open(encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        required = {"Instrument", "Date", "Time", "Open", "High", "Low", "Close"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Missing CSV columns: {sorted(required - set(reader.fieldnames or []))}")
        for line, row in enumerate(reader, 2):
            try:
                current_instrument = row["Instrument"].strip().upper().removesuffix(".NS")
                if not current_instrument or (instrument is not None and current_instrument != instrument):
                    raise ValueError(f"expected only {instrument or 'one instrument'}; do not mix instruments")
                instrument = current_instrument
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


def convert_minutes(candles: Iterable[Candle], percentage=1, *, include_partials=True,
                    sizing="percentage", atr_period=14, session_mode="threshold") -> dict:
    """Build percentage or daily ATR close-based bricks, carrying excess forward.

    Normal OHLC is synthetic: O=anchor, C=anchor*(1 +/- p/100), H=max(O,C),
    L=min(O,C). Consume all thresholds crossed by a minute CLOSE, then retain
    the final anchor for the next minute. Never round a 1.4% source move into
    a 1.4% normal brick. Actual source OHLC is separate audit metadata.

    Default threshold mode carries unfinished displacement across sessions.
    Small boundary jumps can confirm full bricks at the observed open. Jumps
    at least p% of the previous close (or current daily ATR points) print the
    preceding nonzero remainder and one full-sized gap; missing intervals stay
    labeled data_gap. Final unfinished movement is metadata, not a bar.
    session_mode='daily' retains daily/end-of-input partials and all gap bars.

    sizing='atr' uses prior eligible daily Wilder ATR fixed for each session.
    Current/future daily ranges never resize earlier bricks. Warm-up/zero ATR
    produces no full bricks; threshold mode keeps pending metadata only.
    """
    if session_mode not in ("threshold", "daily"):
        raise ValueError("session_mode must be threshold or daily")
    if sizing not in ("percentage", "atr"):
        raise ValueError("sizing must be 'percentage' or 'atr'")
    p = parse_percent(percentage) if sizing == "percentage" else None
    tracker = DailyATR(atr_period) if sizing == "atr" else None
    brick_size = atr_value = atr_as_of = None
    atr_status = None
    if p is not None:
        fraction = p / HUNDRED
        up_factor, down_factor = Decimal(1) + fraction, Decimal(1) - fraction
        if up_factor == 1 or down_factor == 1:
            raise ValueError("percentage is too small for Decimal precision")
    bars = []
    highs, pending_breakout = RecordHighs(), None
    anchor = span_start = observed = previous = first = None
    pending_count = row_count = missing_intervals = normalized_labels = raw_rows = 0
    sessions = {}
    merged_buckets = []
    absorbed_gaps = absorbed_unknown = 0

    def ohlc_dict(c):
        return {k: float(getattr(c, k)) for k in ("open", "high", "low", "close")}

    def emit(kind, start, end, known_at, o, close, count, reason, observation=None, trigger=None, breakout=None):
        if len(bars) >= 1_000_000:
            raise ValueError("Output exceeds 1,000,000 bars; choose a larger brick size or less source history")
        # For normal bricks this is defined by the exact percentage construction.
        # JSON float price ratios can differ by ordinary floating-point epsilon.
        move = (p if close > o else -p) if kind == "move" and p is not None else (close - o) / o * HUNDRED
        bars.append({
            "bar_id": len(bars), "kind": kind,
            "start": start.isoformat(), "end": end.isoformat(), "known_at": known_at.isoformat(),
            "open": float(o), "high": float(max(o, close)),
            "low": float(min(o, close)), "close": float(close),
            "move_pct": float(move), "range_pct": float(abs(move)),
            "direction": "up" if close > o else "down" if close < o else "flat",
            "source_minutes": count,
            "synthetic_ohlc": True, "synthetic_bridge": kind in ("session_gap", "data_gap"),
            "threshold_reached": kind == "move" or (abs(move) >= p if p is not None else
                                  brick_size is not None and abs(close - o) >= brick_size),
            "finalized": kind != "partial" or reason != "end_of_input",
            "reason": reason,
            "ath_breakout": breakout,
            "observed_ohlc": {k: float(v) for k, v in observation.items()} if observation else None,
            "trigger_minute": ({"timestamp": trigger.timestamp.isoformat(), **ohlc_dict(trigger)}
                               if trigger is not None else None),
        })
        if tracker is not None:
            bars[-1].update({"brick_size_points": float(brick_size) if brick_size is not None else None,
                             "atr_value": float(atr_value) if atr_value is not None else None,
                             "atr_period": tracker.period, "atr_as_of": atr_as_of,
                             "atr_status": atr_status})

    def flush(reason, known_at=None):
        nonlocal anchor, span_start, pending_count, observed, pending_breakout
        if anchor is not None and (pending_count or previous.close != anchor):
            emit("partial", span_start, previous.timestamp + MINUTE,
                 known_at or previous.timestamp + MINUTE, anchor, previous.close,
                 pending_count, reason, observed, previous, pending_breakout)
        anchor = span_start = observed = None
        pending_count = 0
        pending_breakout = None

    def consume(price, end, known_at, trigger=None, confirmation="minute_close"):
        nonlocal anchor, span_start, pending_count, observed, pending_breakout
        while p is not None or brick_size is not None:
            upper, lower = ((anchor * up_factor, anchor * down_factor) if p is not None
                            else (anchor + brick_size, anchor - brick_size))
            if upper == anchor or lower == anchor:
                raise ValueError("brick size is too small for Decimal precision at this price")
            if price >= upper:
                target = upper
            elif price <= lower:
                target = lower
            else:
                break
            emit("move", span_start, end, known_at, anchor, target, pending_count,
                 ("exact_percentage_" if p is not None else "prior_daily_atr_") + confirmation + "_projection",
                 observed, trigger, pending_breakout)
            bars[-1].update({"confirmation": confirmation, "confirmation_price": float(price)})
            anchor, span_start = target, end
            pending_count, observed, pending_breakout = 0, None, None

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
        day_changed = previous is not None and c.timestamp.date() != previous.timestamp.date()
        missing = previous is not None and not day_changed and c.timestamp - previous.timestamp != MINUTE
        if day_changed or missing:
            if session_mode == "daily":
                flush("session_end" if day_changed else "missing_minutes", c.timestamp)
            missing_intervals += int(missing)
        if tracker is not None:
            tracker.observe(c)
            atr_value, atr_as_of = tracker.value, tracker.as_of
            brick_size = atr_value if atr_value is not None and atr_value > 0 else None
            atr_status = "warmup" if atr_value is None else "ready" if brick_size is not None else "zero_atr"
        if day_changed or missing:
            normal_endpoints = (previous.timestamp.time() == time(15, 29)
                                and c.timestamp.time() == time(9, 15))
            kind = "session_gap" if day_changed and normal_endpoints else "data_gap"
            reason = ("observed_session_close_to_open" if kind == "session_gap"
                      else "missing_minutes" if missing else "nonstandard_session_endpoints")
            displacement = abs(c.open - previous.close)
            large_gap = (displacement >= previous.close * p / HUNDRED if p is not None
                         else brick_size is not None and displacement >= brick_size)
            separate = session_mode == "daily" or large_gap
            if separate:
                if session_mode == "threshold":
                    if anchor != previous.close:
                        flush("before_large_gap", c.timestamp)
                    # A flat pending span is not a brick; keep its observations
                    # for the next brick (or pending metadata) without a zero bar.
                    anchor, span_start = c.open, c.timestamp
                if kind == "data_gap" or c.open != previous.close:
                    gap_breakout = highs.observe(c.open, c.timestamp, c.timestamp)
                    emit(kind, previous.timestamp + MINUTE, c.timestamp, c.timestamp,
                         previous.close, c.open, 0, reason, breakout=gap_breakout)
            else:
                absorbed_gaps += int(kind == "session_gap" and displacement != 0)
                absorbed_unknown += int(kind == "data_gap")
                pending_breakout = merge_breakouts(pending_breakout, highs.observe(c.open, c.timestamp, c.timestamp))
                if observed is not None:
                    observed["high"] = max(observed["high"], c.open)
                    observed["low"] = min(observed["low"], c.open)
                    observed["close"] = c.open
                consume(c.open, c.timestamp, c.timestamp,
                        confirmation="session_open" if kind == "session_gap" else "observed_open_after_missing_data")
        if anchor is None:
            anchor, span_start = c.open, c.timestamp
        pending_count += 1
        pending_breakout = merge_breakouts(pending_breakout, highs.observe(c.high, c.timestamp, c.timestamp + MINUTE))
        if observed is None:
            observed = {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
        else:
            observed["high"] = max(observed["high"], c.high)
            observed["low"] = min(observed["low"], c.low)
            observed["close"] = c.close
        consume(c.close, c.timestamp + MINUTE, c.timestamp + MINUTE, c)
        previous = c
    if first is None:
        raise ValueError("No minute candles supplied")
    pending = None
    if session_mode == "daily":
        flush("end_of_input")
    elif pending_count or anchor != previous.close:
        pending = {"open": float(anchor), "last_price": float(previous.close),
                   "move_pct": float((previous.close - anchor) / anchor * HUNDRED),
                   "source_minutes": pending_count, "start": span_start.isoformat(),
                   "last_minute": previous.timestamp.isoformat(), "ath_breakout": pending_breakout,
                   "observed_ohlc": {k: float(v) for k, v in observed.items()} if observed else None}
    irregular = [{"date": str(day), "first": a.isoformat(), "last": b.isoformat(), "minutes": n}
                 for day, (a, b, n) in sessions.items()
                 if a.time() != time(9, 15) or b.time() != time(15, 29) or n != 375]
    visible = bars if include_partials else [b for b in bars if b["kind"] != "partial"]
    result = {"metadata": {
        "algorithm_version": "2.5.0", "percentage": float(p) if p is not None else None,
        "mode": "exact_percentage_close" if p is not None else "prior_daily_atr_close",
        "sizing": sizing, "session_mode": session_mode, "pending_move": pending,
        "absorbed_small_session_gaps": absorbed_gaps, "absorbed_unknown_intervals": absorbed_unknown,
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
            ("1 means 1%. Each normal move brick has exactly +/-p% body and p% high-low range relative to its own open, within numerical precision."
             if p is not None else "Full ATR bricks have a point size equal to prior daily ATR; their percentage size varies with price."),
            "Normal OHLC is a synthetic close-based projection without wicks; observed OHLC is separate and never assigned as a brick's high/low.",
            "Each crossed sizing threshold emits a brick. Reversal uses one brick. Threshold mode carries unfinished displacement across sessions; daily mode flushes every session.",
            "Minute closes confirm ordinary bricks; threshold mode also processes observed session/post-missing-data opens at their opening timestamp. No current minute high/close is used for an opening-confirmed brick.",
            "source_minutes counts newly consumed observations; extra bricks from one observation count zero. Sum displayed counts plus pending_move counts, with partials included.",
            "Threshold mode: only gaps at or above the chosen percent of the previous observed close (or current ATR point size) split out. A nonzero preceding remainder is emitted immediately before that full gap. No day-end or final partial is plotted. Daily mode retains the earlier daily remainder behavior.",
            "Every displayed percentage uses its own brick's open. Remainders preserve actual price displacement without rounding to a full brick.",
            "Small missing-interval endpoint changes can complete normal bricks in threshold mode; their confirmation is explicitly labeled and the missing path remains unknown. Large unknown-interval bridges stay classified data_gap. Bridges preserve the full endpoint gap. Gap high/low do not imply a traded path. Session gaps use last observed minute close, not official daily close.",
            "No exchange calendar is applied: missing whole sessions, special sessions and halts cannot be definitively classified.",
            "Pass only completed source minutes. The final end_of_input partial can change as more input arrives.",
            "CSV minute-start IST labels are floored; minute collisions use first/max/min/last unless error policy is selected.",
            "ATH markers mean strictly new highs within available source history, not verified lifetime highs. The first source high seeds the baseline; ties do not break out.",
            "Breakouts use observed minute highs (or a gap's opening price), not Renko highs or closing-price confirmation. Events are grouped once onto the brick consuming their observations.",
        ],
    }, "bars": visible}
    result["metadata"].update(highs.metadata(first.timestamp))
    if tracker is not None:
        result["metadata"].update({
            "atr_period": tracker.period, "atr_timeframe": "1D", "atr_smoothing": "Wilder RMA; SMA seed",
            "atr_size_policy": "prior eligible session ATR, fixed throughout the next observed session",
            "atr_eligible_prior_sessions": tracker.used_sessions,
            "atr_skipped_prior_sessions": tracker.skipped_sessions,
            "last_atr_points": float(atr_value) if atr_value is not None else None,
            "last_atr_as_of": atr_as_of,
        })
        result["metadata"]["notes"].extend([
            "ATR uses observed daily H/L/C, not synthetic Renko bricks. Only 375-minute 09:15-15:29 regular sessions enter the ATR calculation.",
            "TR=max(H-L, abs(H-previous eligible close), abs(L-previous eligible close)); first TR=H-L. Seed with N TRs, then Wilder smoothing.",
            "Incomplete/nonstandard prior sessions are excluded from ATR; the next eligible TR references the last eligible close. Existing ATR carries across skipped sessions.",
            "The first N eligible daily candles are warm-up; no invented ATR or full bricks. Threshold mode retains only pending metadata during warm-up. Daily mode shows remainders and gaps. Zero ATR also emits no full bricks.",
        ])
    return result


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
    marked = [(i,b) for i,b in enumerate(rows) if b.get('ath_breakout')]
    if marked:
        fig.add_trace(go.Scatter(
            name="ATH*", mode="markers+text", x=[i for i,b in marked], y=[b['high'] for i,b in marked],
            marker=dict(color="#eab84f", size=8, symbol="diamond"),
            text=['' if i and rows[i-1].get('ath_breakout') else 'ATH*' for i,b in marked],
            textposition="top center",
            hovertext=[f"Source-history high {b['ath_breakout']['high']} > prior {b['ath_breakout']['previous_high']}"
                       f"<br>First break {b['ath_breakout']['first_at']}<br>Available source history only"
                       for i,b in marked], hoverinfo="text"))
    ticks = rows[::max(1, len(rows) // 8)]
    label = (f"daily ATR({result['metadata']['atr_period']})" if result['metadata'].get('sizing') == 'atr'
             else f"{result['metadata']['percentage']:g}%")
    fig.update_layout(
        title=f"{result['metadata'].get('display_name', 'Instrument')} • {label} close-based bricks",
        template="plotly_white", height=680,
        yaxis_title="Price / index level (calculated brick boundaries)",
        xaxis_title="Sequential blocks • equal spacing, variable elapsed time",
        xaxis_rangeslider_visible=False,
        xaxis=dict(tickmode="array", tickvals=[positions[b["bar_id"]] for b in ticks],
                   ticktext=[b["end"][:16].replace("T", " ") for b in ticks]),
        legend=dict(orientation="h", y=1.08), margin=dict(b=130),
        annotations=[dict(x=0, y=-0.20, xref="paper", yref="paper", showarrow=False,
                         text="Blue/purple = full gaps; amber = unknown intervals; lighter bricks = remainders; gold ATH* = new source-history high.")])
    return fig


def local_metadata(path: Path, expected_symbol: str | None = None) -> dict:
    from stock_data import normalize_symbol
    with path.open(encoding="utf-8-sig", newline="") as f:
        row = next(csv.DictReader(f), None)
    if not row or not row.get("Instrument"):
        raise ValueError("CSV requires a nonempty Instrument column")
    symbol = normalize_symbol(row["Instrument"])
    if expected_symbol and symbol != normalize_symbol(expected_symbol):
        raise ValueError(f"CSV is for {symbol}, not {expected_symbol}")
    original = symbol == "NIFTY50" and path.resolve() == DEFAULT_CSV.resolve()
    return {"symbol": symbol, "instrument": symbol,
            "display_name": "NIFTY 50" if symbol == "NIFTY50" else symbol,
            "instrument_type": "index" if symbol == "NIFTY50" else "equity",
            "source_provider": "Original NIFTY CSV" if original else "Local CSV",
            "source_url": SOURCE_URL if original else None,
            "source_limitations": "Observed source history only; missing intervals and gaps are preserved. ATH* is not a verified lifetime high."}


def create_app(csv_path: str | Path = DEFAULT_CSV, *, stock_dir=None,
               default_symbol="NIFTY50", source_symbol="NIFTY50"):
    """Local API with explicit stock downloads and immutable per-symbol snapshots."""
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from stock_data import DEFAULT_DATA, DownloadError, cached_stock, download_stock, normalize_symbol
    app = FastAPI(title="RealRenko stock and index OHLC blocks", version="2.5.0")
    source = Path(csv_path).resolve()
    stock_dir = Path(stock_dir) if stock_dir is not None else DEFAULT_DATA
    default_symbol, source_symbol = normalize_symbol(default_symbol), normalize_symbol(source_symbol)
    static = Path(__file__).with_name("static")
    app.mount("/assets", StaticFiles(directory=static), name="assets")

    def resolve_source(symbol):
        symbol = normalize_symbol(symbol)
        if symbol == source_symbol:
            return source, local_metadata(source, symbol)
        return cached_stock(symbol, stock_dir)

    @lru_cache(maxsize=4)
    def calculate(path: str, symbol: str, sizing: str, p: str, atr_period: int, session_mode: str, mtime_ns: int, size: int):
        return convert_minutes(read_minutes(path, expected_instrument=symbol), p, include_partials=True,
                               sizing=sizing, atr_period=atr_period, session_mode=session_mode)

    def missing(symbol):
        return HTTPException(503, f"No data for {symbol}. Use Download & plot for a stock, or run python nifty_bars.py --download for the original NIFTY CSV.")

    @app.post("/download")
    def download(symbol: str = Query(...), days: int = Query(28, ge=1, le=28)):
        try:
            _, metadata = download_stock(symbol, days, stock_dir)
            return {"metadata": metadata}
        except DownloadError as exc:
            raise HTTPException(502, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "Cannot save the stock download. Check server data-directory permissions.") from exc

    @app.get("/bars")
    def bars(percentage: str = Query("1", description="1 or 1% means one percent"),
             start: date | None = None, end: date | None = None, include_partials: bool = True,
             sizing: str = Query("percentage", description="percentage or atr"),
             atr_period: int = Query(14, ge=1, description="Daily Wilder ATR length; positive whole number"),
             symbol: str = default_symbol, session_mode: str = "threshold"):
        try:
            if sizing not in ("percentage", "atr"):
                raise ValueError("sizing must be 'percentage' or 'atr'")
            p = str(parse_percent(percentage).normalize()) if sizing == "percentage" else "1"
            period = parse_atr_period(atr_period) if sizing == "atr" else 14
            if start and end and start > end:
                raise ValueError("start must be on or before end")
            path, metadata = resolve_source(symbol)
            stat = path.stat()
            data = calculate(str(path), metadata["symbol"], sizing, p, period, session_mode, stat.st_mtime_ns, stat.st_size)
            result = filter_result(data, start, end, include_partials=include_partials)
            result["metadata"] = {**result["metadata"], **metadata}
            return result
        except FileNotFoundError:
            raise missing(symbol)
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(422, str(exc)) from exc

    @lru_cache(maxsize=4)
    def calculate_history(path: str, symbol: str, mtime_ns: int, size: int):
        from daily_history import daily_history
        return daily_history(path, expected_instrument=symbol)

    @app.get("/history")
    def history(start: date | None = None, end: date | None = None, symbol: str = default_symbol):
        try:
            path, metadata = resolve_source(symbol)
            stat = path.stat()
            result = filter_result(calculate_history(str(path), metadata["symbol"], stat.st_mtime_ns, stat.st_size), start, end)
            result["metadata"] = {**result["metadata"], **metadata}
            return result
        except FileNotFoundError:
            raise missing(symbol)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/chart?" + urllib.parse.urlencode({"symbol": default_symbol}))

    @app.get("/chart", response_class=HTMLResponse)
    def chart():
        import html
        shell = static.joinpath("chart.html").read_text(encoding="utf-8")
        shell = shell.replace('data-default-symbol="NIFTY50"', f'data-default-symbol="{html.escape(default_symbol)}"')
        return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="Local CSV in the documented schema; use --symbol to check its identity")
    parser.add_argument("--symbol", help="NSE equity symbol, e.g. TCS or RELIANCE; default NIFTY50")
    parser.add_argument("--days", type=int, default=28, help="Recent calendar days to download (1–28)")
    parser.add_argument("--data-dir", type=Path, help="Directory for per-stock download snapshots")
    parser.add_argument("--download", action="store_true", help="Download recent stock minutes, or the original NIFTY CSV")
    parser.add_argument("--percentage", default="1", help="1 or 1%% means one percent")
    parser.add_argument("--session-mode", choices=("threshold", "daily"), default="threshold", help="Carry unfinished moves (default), or show a remainder every session")
    parser.add_argument("--sizing", choices=("percentage", "atr"), default="percentage", help="Renko brick sizing method")
    parser.add_argument("--atr-period", type=parse_atr_period, default=14, help="Daily Wilder ATR length (7, 10, 14 or any positive integer)")
    parser.add_argument("--output", type=Path, default=Path("bars.json"))
    parser.add_argument("--html", type=Path, help="Also save an interactive candlestick chart")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--include-partials", action=argparse.BooleanOptionalAction, default=True,
                        help="Include daily/final remainders (default); --no-include-partials creates a sparse export")
    parser.add_argument("--serve", action="store_true", help="Serve /bars, /chart and /docs locally")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    from stock_data import DEFAULT_DATA, cached_stock, download_stock, normalize_symbol
    stock_dir = args.data_dir or DEFAULT_DATA
    symbol = normalize_symbol(args.symbol or "NIFTY50")
    custom_csv = args.csv is not None
    if custom_csv and args.download and symbol != "NIFTY50":
        parser.error("Use --data-dir for stock downloads; --csv is for an existing local file.")
    if args.download:
        if symbol == "NIFTY50":
            download_csv(args.csv or DEFAULT_CSV)
        else:
            download_stock(symbol, args.days, stock_dir)
    if custom_csv or symbol == "NIFTY50":
        args.csv = args.csv or DEFAULT_CSV
        metadata = local_metadata(args.csv, args.symbol) if args.csv.exists() else {}
        symbol = metadata.get("symbol", symbol)
    else:
        args.csv, metadata = cached_stock(symbol, stock_dir)
    if args.serve:
        import uvicorn
        uvicorn.run(create_app(args.csv if custom_csv else DEFAULT_CSV, stock_dir=stock_dir,
                               default_symbol=symbol, source_symbol=symbol if custom_csv else "NIFTY50"),
                    host="127.0.0.1", port=args.port)
        return
    result = filter_result(convert_minutes(read_minutes(args.csv, expected_instrument=symbol), args.percentage, include_partials=True,
                                          sizing=args.sizing, atr_period=args.atr_period, session_mode=args.session_mode),
                           args.start, args.end, include_partials=args.include_partials)
    with args.csv.open("rb") as src:
        digest = hashlib.sha256()
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    result["metadata"]["source_sha256"] = digest.hexdigest()
    result["metadata"].update(metadata)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    if args.html:
        make_chart(result).write_html(args.html, include_plotlyjs=True)
    print(json.dumps({k: v for k, v in result["metadata"].items()
                      if k not in ("sessions_outside_normal_375_minute_template", "notes")}, indent=2))


if __name__ == "__main__":
    main()
