"""Recent NSE equity minute downloads; no credentials or third-party packages.

Yahoo's chart endpoint is an unofficial, best-effort source, not an exchange
feed. Request at most seven days per call, within a rolling 28-day window.
Never resample daily prices into minutes, fill missing bars, or erase gaps.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DATA = Path(__file__).with_name("data")
IST = ZoneInfo("Asia/Kolkata")
ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/"


class DownloadError(ValueError):
    """A provider failure leaves the previous complete snapshot untouched."""


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if symbol.endswith(".NS"):
        symbol = symbol[:-3]
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&-]{0,29}", symbol):
        raise ValueError("Use an NSE symbol such as TCS, INFY, M&M or RELIANCE (optional .NS suffix).")
    return symbol


def _atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            f.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cached_stock(symbol: str, cache_dir: Path = DEFAULT_DATA) -> tuple[Path, dict]:
    symbol = normalize_symbol(symbol)
    metadata = json.loads((Path(cache_dir) / f"{symbol}.json").read_text(encoding="utf-8"))
    digest = metadata.get("source_sha256", "")
    if metadata.get("symbol") != symbol or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid stock cache manifest. Download this symbol again.")
    path = Path(cache_dir) / symbol / f"{digest}.csv"
    if not path.is_file():
        raise FileNotFoundError(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Stock cache checksum mismatch. Download this symbol again.")
    return path, metadata


def _fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "RealRenko/2.5 personal-research", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            # Bound a provider response rather than downloading arbitrary-sized input.
            body = response.read(16 * 1024 * 1024 + 1)
        if len(body) > 16 * 1024 * 1024:
            raise DownloadError("Minute provider returned an oversized response.")
        return json.loads(body, parse_float=Decimal)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"Minute download failed: {exc}. Retry later; existing data is unchanged.") from exc


def download_stock(symbol: str, days: int = 28, cache_dir: Path = DEFAULT_DATA, *, now: datetime | None = None) -> tuple[Path, dict]:
    """Save a validated snapshot and atomically publish its manifest.

    Refresh replaces the selected recent window, rather than appending prices
    across possible provider adjustments. Prior snapshot files remain intact.
    Only completed, regular-session 09:15–15:29 IST bars are accepted. Yahoo's
    occasional 15:30 closing quote is not a regular one-minute candle.
    """
    symbol = normalize_symbol(symbol)
    if symbol == "NIFTY50":
        raise ValueError("NIFTY50 uses the original CSV. Stock downloads require an NSE equity symbol.")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 28:
        raise ValueError("days must be a whole number between 1 and 28; older minute history is unavailable from this downloader.")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    end = int(now.timestamp()) // 60 * 60
    start = end - days * 86400
    rows, skipped, requests, events = {}, Counter(), [], {}
    cursor = start
    while cursor < end:
        until = min(cursor + 7 * 86400, end)
        query = urllib.parse.urlencode({"interval": "1m", "period1": cursor, "period2": until,
                                       "includePrePost": "false", "events": "div,splits"})
        url = ENDPOINT + urllib.parse.quote(symbol + ".NS", safe="") + "?" + query
        payload = _fetch(url)
        if not isinstance(payload, dict) or not isinstance(payload.get("chart"), dict):
            raise DownloadError("Provider did not return a chart result.")
        chart = payload["chart"]
        if chart.get("error"):
            raise DownloadError(f"Yahoo could not download {symbol}: {chart['error'].get('description', 'provider error')}")
        results = chart.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise DownloadError(f"Yahoo returned no result for {symbol}. Check the NSE symbol or retry later.")
        result = results[0]
        meta = result.get("meta", {})
        if (meta.get("symbol", "").upper() != symbol + ".NS" or meta.get("dataGranularity") != "1m"
                or meta.get("currency") != "INR" or meta.get("instrumentType") != "EQUITY"
                or meta.get("exchangeTimezoneName") not in ("Asia/Kolkata", "Asia/Calcutta")):
            raise DownloadError("Provider returned a different instrument, interval, currency or exchange timezone.")
        timestamps = result.get("timestamp") or []
        quotes = result.get("indicators", {}).get("quote") or [{}]
        quote = quotes[0]
        if any(len(quote.get(k) or []) != len(timestamps) for k in ("open", "high", "low", "close")):
            raise DownloadError("Provider OHLC arrays do not match their timestamps.")
        for i, stamp in enumerate(timestamps):
            if isinstance(stamp, bool) or not isinstance(stamp, int):
                raise DownloadError("Invalid minute timestamp from provider.")
            # Half-open request bounds also remove the provider's trailing quote.
            if not cursor <= stamp < until or stamp + 60 > end:
                skipped["outside_request_or_incomplete"] += 1
                continue
            ts = datetime.fromtimestamp(stamp, IST)
            if stamp % 60:
                raise DownloadError("Provider returned a non-minute timestamp within the requested interval.")
            if not (9 * 60 + 15 <= ts.hour * 60 + ts.minute < 15 * 60 + 30):
                skipped["outside_regular_session"] += 1
                continue
            values = [quote[k][i] for k in ("open", "high", "low", "close")]
            if any(v is None for v in values):
                skipped["missing_ohlc"] += 1
                continue
            try:
                o, h, l, c = (Decimal(str(v)) for v in values)
                valid = all(v.is_finite() and v > 0 for v in (o, h, l, c)) and l <= min(o, c) <= max(o, c) <= h
            except Exception as exc:
                raise DownloadError(f"Invalid OHLC at {ts.isoformat()}") from exc
            if not valid:
                raise DownloadError(f"Invalid OHLC at {ts.isoformat()}; refusing to change prices.")
            value = (o, h, l, c)
            if stamp in rows:
                if rows[stamp] != value:
                    raise DownloadError("Conflicting duplicate minute prices from provider.")
                skipped["identical_duplicate"] += 1
            rows[stamp] = value
        requests.append({"start": datetime.fromtimestamp(cursor, timezone.utc).isoformat(),
                         "end_exclusive": datetime.fromtimestamp(until, timezone.utc).isoformat(),
                         "returned_timestamps": len(timestamps)})
        for kind, entries in (result.get("events") or {}).items():
            for key, event in entries.items():
                if start <= int(event.get("date", 0)) < end:
                    events[f"{kind}:{key}"] = {"type": kind, **event}
        cursor = until
    if not rows:
        raise DownloadError(f"No completed regular-session 1-minute candles for {symbol} in the last {days} days. Try a larger window (up to 28 days).")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Instrument", "Date", "Time", "Open", "High", "Low", "Close"])
    ordered = sorted(rows)
    for stamp in ordered:
        ts = datetime.fromtimestamp(stamp, IST)
        writer.writerow([symbol, ts.strftime("%d-%m-%Y"), ts.strftime("%H:%M:%S"), *rows[stamp]])
    content = output.getvalue().encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    metadata = {
        "symbol": symbol, "instrument": symbol, "display_name": symbol, "instrument_type": "equity",
        "currency": "INR", "exchange": "NSE", "provider_symbol": symbol + ".NS",
        "source_provider": "Yahoo Finance (unofficial chart endpoint)",
        "source_url": "https://finance.yahoo.com/quote/" + urllib.parse.quote(symbol + ".NS", safe="") + "/history/",
        "interval": "1m", "timezone": "Asia/Kolkata", "requested_days": days,
        "downloaded_at": now.isoformat(), "source_sha256": digest, "downloaded_minutes": len(rows),
        "first_minute": datetime.fromtimestamp(ordered[0], IST).isoformat(),
        "last_minute": datetime.fromtimestamp(ordered[-1], IST).isoformat(),
        "skipped_rows": dict(skipped), "download_requests": requests,
        "corporate_actions": list(events.values()),
        "price_adjustment": "Provider OHLC as returned; no local dividend/split adjustment, tick rounding or gap removal.",
        "source_limitations": "Recent minute history only (requested at most 28 days); availability and completeness are not guaranteed. Regular session only. Missing bars are not filled. Stocks can gap. ATH* is a record within downloaded history, not lifetime ATH. Splits/dividends can affect comparisons.",
    }
    path = Path(cache_dir) / symbol / f"{digest}.csv"
    _atomic_write(path, content)
    # Manifest is the commit point: readers see either old or new complete data.
    _atomic_write(Path(cache_dir) / f"{symbol}.json", json.dumps(metadata, indent=2, default=str, allow_nan=False).encode("utf-8"))
    return path, metadata
