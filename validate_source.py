"""Audit actual brick geometry, carried remainder, source coverage and full gaps."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from nifty_bars import MINUTE, SOURCE_URL, convert_minutes, parse_percent, read_minutes

# These established fixtures exercise the retained daily-remainder policy.
# Default threshold-mode behavior is covered separately in test_threshold_mode.py.
from functools import partial as bind_policy
convert_minutes = bind_policy(convert_minutes, session_mode="daily")


def audit(path: Path, percentage='1', bars_output: Path | None = None):
    p = parse_percent(percentage)
    result = convert_minutes(read_minutes(path), p)
    minutes = iter(read_minutes(path))
    checked = moves = partials = gaps = 0
    last = None
    max_body_error = 0.0
    for i, b in enumerate(result['bars']):
        if i:
            assert result['bars'][i-1]['close'] == b['open'], ('disconnected bricks', i)
        assert b['high'] == max(b['open'], b['close'])
        assert b['low'] == min(b['open'], b['close'])
        assert datetime.fromisoformat(b['known_at']) >= datetime.fromisoformat(b['end'])
        if b['synthetic_bridge']:
            assert b['open'] == result['bars'][i-1]['close']
            assert b['close'] == result['bars'][i+1]['open']
            assert b['source_minutes'] == 0
            gaps += 1
            continue
        rows = [next(minutes) for _ in range(b['source_minutes'])]
        if rows:
            assert all(a.timestamp + MINUTE == z.timestamp for a, z in zip(rows, rows[1:]))
            assert rows[0].timestamp.date() == rows[-1].timestamp.date()
            expected = (rows[0].open, max(r.high for r in rows), min(r.low for r in rows), rows[-1].close)
            assert tuple(float(v) for v in expected) == tuple(b['observed_ohlc'][k] for k in ('open', 'high', 'low', 'close'))
            last = rows[-1]
        else:
            assert b['observed_ohlc'] is None
        assert last is not None
        assert (last.timestamp + MINUTE).isoformat() == b['end']
        assert b['trigger_minute']['timestamp'] == last.timestamp.isoformat()
        assert b['trigger_minute']['close'] == float(last.close)
        if b['kind'] == 'move':
            # Verify the actual returned prices, not the declared move_pct label.
            body = abs(b['close'] / b['open'] - 1) * 100
            error = abs(body - float(p))
            assert error < 1e-9, b
            max_body_error = max(max_body_error, error)
            assert abs(b['move_pct']) == float(p)
            if b['direction'] == 'up':
                assert float(last.close) >= b['close'] - 1e-9
            else:
                assert float(last.close) <= b['close'] + 1e-9
            # Earlier closes must not already have reached this brick boundary.
            for row in rows[:-1]:
                assert abs(float(row.close) / b['open'] - 1) * 100 < float(p) + 1e-9
            if i and not result['bars'][i-1]['synthetic_bridge'] and result['bars'][i-1]['kind'] == 'move':
                assert b['open'] == result['bars'][i-1]['close']
            moves += 1
        else:
            assert abs(b['move_pct']) < float(p)
            assert b['close'] == float(last.close)
            if i and result['bars'][i-1]['kind'] == 'move':
                assert b['open'] == result['bars'][i-1]['close']
            partials += 1
        checked += len(rows)
    assert next(minutes, None) is None
    assert checked == result['metadata']['source_minutes']
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    meta = {**result['metadata'], 'source_url': SOURCE_URL, 'source_sha256': digest.hexdigest(),
            'validation': {'all_assertions_passed': True, 'checked_normal_bricks': moves,
                           'checked_partial_bars': partials, 'checked_bridge_bars': gaps,
                           'exactly_once_minute_coverage_including_partials': checked,
                           'checked_adjacent_price_connections': len(result['bars']) - 1,
                           'maximum_normal_body_error_percentage_points': max_body_error,
                           'checks': ['actual normal OHLC body/range equals requested percentage',
                                      'every adjacent brick connects, including daily remainders and gaps',
                                      'partial remainder strictly below threshold',
                                      'source OHLC separately reconstructed without assigning wicks',
                                      'minute close confirms each brick; no earlier close already crossed',
                                      'every minute counted exactly once with remainders shown by default',
                                      'gap endpoints and full size preserved']}}
    if bars_output:
        bars_output.write_text(json.dumps({'metadata': meta, 'bars': result['bars']}, indent=2, allow_nan=False))
    return meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--percentage', default='1')
    parser.add_argument('--output', type=Path, default=Path('validation.json'))
    parser.add_argument('--bars-output', type=Path)
    args = parser.parse_args()
    report = audit(args.csv, args.percentage, args.bars_output)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report['validation'], indent=2))
