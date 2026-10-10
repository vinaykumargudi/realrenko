"""Download the available public NSE minute archive, without inventing history.

The community dataset's description still mentions five-minute data. Validate
the actual downloaded file's cadence, OHLC and timestamps before publishing it.
Source provenance is community-supplied, not independently exchange-certified.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from stock_data import DEFAULT_DATA, DownloadError, _atomic_write, normalize_symbol

DATASET = "debashis74017/stock-market-data-nifty-50-stocks-1-min-data"
DATASET_URL = "https://www.kaggle.com/datasets/" + DATASET
API = "https://api.kaggle.com/v1/datasets/"
MAX_DOWNLOAD = 200 * 1024 * 1024


def provider_directory(provider: str, cache_dir: Path = DEFAULT_DATA) -> Path:
    if provider not in ("kaggle", "yahoo"):
        raise ValueError("provider must be kaggle (available historical minutes) or yahoo (recent minutes)")
    return Path(cache_dir) / "kaggle" if provider == "kaggle" else Path(cache_dir)


def _request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "RealRenko/2.6 research"}), timeout=45)


def _dataset_metadata():
    try:
        with _request(API + "view/" + DATASET) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise DownloadError("Unexpected archive metadata response size")
        value = json.loads(payload)
        version = value.get("currentVersionNumber")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise DownloadError("Archive did not identify its version")
        return value
    except (OSError, ValueError) as exc:
        raise DownloadError("Cannot read historical archive metadata. Retry later; no cached data was changed.") from exc


def import_history_csv(input_path: Path, symbol: str, cache_dir: Path = DEFAULT_DATA, *, provenance=None):
    """Validate and cache one symbol's public-archive date/open/high/low/close CSV.

    Preserve nonzero-second labels so read_minutes applies its explicit floor /
    collision policy. Sorted raw records are mandatory; daily/5-minute feeds
    are rejected rather than being relabeled or interpolated as minute data.
    """
    from nifty_bars import IST, read_minutes
    symbol = normalize_symbol(symbol)
    cache = provider_directory("kaggle", cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    temporary = None
    raw_rows = 0
    excluded_zero = Counter()
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=cache, delete=False) as f:
            temporary = Path(f.name)
            writer = csv.writer(f)
            writer.writerow(["Instrument", "Date", "Time", "Open", "High", "Low", "Close"])
            with Path(input_path).open(encoding="utf-8-sig", newline="") as src:
                reader = csv.DictReader(src)
                if not {"date", "open", "high", "low", "close"}.issubset(reader.fieldnames or []):
                    raise DownloadError("Archive must contain date, open, high, low and close columns")
                for line, row in enumerate(reader, 2):
                    try:
                        ts = datetime.fromisoformat(row["date"])
                        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
                        if ts.microsecond:
                            raise ValueError("Fractional-second labels are unsupported")
                        if row.get("Instrument") and normalize_symbol(row["Instrument"]) != symbol:
                            raise ValueError("Mixed or incorrect instrument")
                        raw_rows += 1
                        prices = [Decimal(row[k]) for k in ("open", "high", "low", "close")]
                        if all(p == 0 for p in prices) and Decimal(row.get("volume", "-1")) == 0:
                            excluded_zero[str(ts.date())] += 1
                            continue
                        writer.writerow([symbol, ts.strftime("%d-%m-%Y"), ts.strftime("%H:%M:%S"),
                                         *(row[k] for k in ("open", "high", "low", "close"))])
                    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
                        raise DownloadError(f"Invalid archive record {line}: {exc}") from exc
        days, spacing = {}, Counter()
        first = previous = None
        minutes = floored = merged = 0
        for c in read_minutes(temporary, expected_instrument=symbol):
            first = first or c
            minutes += 1
            floored += c.normalized_labels
            merged += c.source_rows - 1
            entry = days.setdefault(str(c.timestamp.date()), [c.timestamp, c.timestamp, 0])
            entry[1], entry[2] = c.timestamp, entry[2] + 1
            if previous and c.timestamp.date() == previous.timestamp.date():
                spacing[int((c.timestamp - previous.timestamp).total_seconds())] += 1
            previous = c
        if not first or not spacing or spacing[60] / sum(spacing.values()) < .9:
            raise DownloadError("Archive failed the one-minute cadence check; daily/5-minute bars cannot supply minute history")
        complete = sum(a.time() == time(9, 15) and b.time() == time(15, 29) and n == 375
                       for a, b, n in days.values())
        digest = hashlib.sha256()
        with temporary.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        metadata = {
            "symbol": symbol, "instrument": symbol, "display_name": symbol,
            "provider": "kaggle", "instrument_type": "equity", "currency": "INR", "exchange": "NSE",
            "source_provider": "Kaggle community minute archive", "source_url": DATASET_URL,
            "interval": "1m", "timezone": "Asia/Kolkata", "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": sha, "downloaded_minutes": minutes, "archive_source_rows": raw_rows,
            "first_minute": first.timestamp.isoformat(), "last_minute": previous.timestamp.isoformat(),
            "history_complete_since_listing": False, "observed_sessions": len(days),
            "complete_regular_sessions": complete, "incomplete_or_special_sessions": len(days)-complete,
            "timestamp_labels_floored_to_minute": floored, "merged_source_rows": merged,
            "excluded_zero_ohlcv_rows": sum(excluded_zero.values()), "excluded_zero_ohlcv_dates": dict(excluded_zero),
            "adjacent_one_minute_fraction": spacing[60] / sum(spacing.values()),
            "price_adjustment": "Archive prices as supplied. Historical corporate-action adjustment basis is not independently verified; no local adjustment or interpolation.",
            "source_limitations": "Available archive history only, not complete listing-to-present coverage. Community prices are not exchange-certified. Missing records and earlier years are not reconstructed. Naive timestamps are interpreted as Asia/Kolkata. ATH* uses this source history only.",
            **(provenance or {}),
        }
        destination = cache / symbol / f"{sha}.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
        _atomic_write(cache / f"{symbol}.json", json.dumps(metadata, indent=2, allow_nan=False).encode())
        return destination, metadata
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_history_stock(symbol: str, cache_dir: Path = DEFAULT_DATA):
    """Fetch this symbol's file, not the many-GB all-stock bundle."""
    symbol = normalize_symbol(symbol)
    if symbol == "NIFTY50":
        raise ValueError("NIFTY50 uses the original NIFTY CSV")
    dataset = _dataset_metadata()
    version = dataset["currentVersionNumber"]
    filename = symbol + "_minute.csv"
    # Pin the version obtained above; never combine two archive versions.
    url = API + "download/" + DATASET + "/" + urllib.parse.quote(filename, safe="")
    url += "?" + urllib.parse.urlencode({"datasetVersionNumber": version})
    cache = provider_directory("kaggle", cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=cache) as temp:
            raw = Path(temp) / "download"
            digest, length = hashlib.sha256(), 0
            with _request(url) as response, raw.open("wb") as f:
                while chunk := response.read(1024 * 1024):
                    length += len(chunk)
                    if length > MAX_DOWNLOAD:
                        raise DownloadError("Archive file exceeds the 200 MiB safety limit")
                    digest.update(chunk)
                    f.write(chunk)
            if zipfile.is_zipfile(raw):
                with zipfile.ZipFile(raw) as z:
                    # Read one exact member without extracting arbitrary paths.
                    if z.namelist() != [filename] or z.getinfo(filename).file_size > MAX_DOWNLOAD:
                        raise DownloadError("Unexpected contents in the archive download")
                    csv_path = Path(temp) / "source.csv"
                    with z.open(filename) as src, csv_path.open("wb") as f:
                        while chunk := src.read(1024 * 1024):
                            f.write(chunk)
            else:
                csv_path = raw
            return import_history_csv(csv_path, symbol, cache_dir, provenance={
                "dataset_version": version, "dataset_updated_at": dataset.get("lastUpdated"),
                "download_url": url, "download_sha256": digest.hexdigest(),
                "download_bytes": length,
                "dataset_declared_license": dataset.get("licenseName"),
            })
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DownloadError(f"The archive has no file for {symbol}. Use another provider or supply a local minute CSV.") from exc
        raise DownloadError(f"Historical source returned HTTP {exc.code}. Retry later; existing snapshots are unchanged.") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise DownloadError(f"Historical download rejected: {exc}. Existing snapshots are unchanged.") from exc
