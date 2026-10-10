"""Reproduce a full-source conversion audit; no third-party dependencies."""
import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from nifty_bars import MINUTE, SOURCE_URL, convert_minutes, parse_percent, read_minutes


def audit(path: Path, percentage='1'):
    p = parse_percent(percentage)
    result = convert_minutes(read_minutes(path), p)
    minutes = iter(read_minutes(path))
    checked = 0
    regular = [b for b in result['bars'] if not b['synthetic_bridge']]
    for b in regular:
        rows = [next(minutes) for _ in range(b['source_minutes'])]
        assert rows[0].timestamp.isoformat() == b['start']
        assert (rows[-1].timestamp + MINUTE).isoformat() == b['end']
        expected = (rows[0].open, max(r.high for r in rows), min(r.low for r in rows), rows[-1].close)
        assert tuple(float(v) for v in expected) == tuple(b[k] for k in ('open', 'high', 'low', 'close'))
        o = rows[0].open
        # A completed bar must end at the FIRST confirming minute close.
        for row in rows[:-1]:
            assert abs(row.close - o) * Decimal(100) < o * p
        reached = abs(rows[-1].close - o) * Decimal(100) >= o * p
        assert reached == (b['kind'] == 'move')
        assert all(a.timestamp + MINUTE == z.timestamp for a, z in zip(rows, rows[1:]))
        assert rows[0].timestamp.date() == rows[-1].timestamp.date()
        checked += len(rows)
    assert next(minutes, None) is None
    assert checked == result['metadata']['source_minutes']
    checked_gaps = 0
    for i, b in enumerate(result['bars']):
        if not b['synthetic_bridge']:
            continue
        assert b['open'] == result['bars'][i-1]['close']
        assert b['close'] == result['bars'][i+1]['open']
        assert b['high'] == max(b['open'], b['close'])
        assert b['low'] == min(b['open'], b['close'])
        assert b['source_minutes'] == 0
        assert datetime.fromisoformat(b['known_at']) >= datetime.fromisoformat(b['start'])
        checked_gaps += 1
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    meta = {**result['metadata'], 'source_url': SOURCE_URL, 'source_sha256': digest.hexdigest(),
            'validation': {'all_assertions_passed': True, 'checked_normal_bars': len(regular),
                           'checked_bridge_bars': checked_gaps, 'exactly_once_minute_coverage': checked,
                           'checks': ['OHLC reconstruction', 'first close crossing',
                                      'no partial bar crosses threshold', 'contiguous minutes within bars',
                                      'daily reset', 'every minute used exactly once',
                                      'gap endpoints and full size preserved']}}
    return meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--percentage', default='1')
    parser.add_argument('--output', type=Path, default=Path('validation.json'))
    args = parser.parse_args()
    report = audit(args.csv, args.percentage)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report['validation'], indent=2))
