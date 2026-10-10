"""Audit real historical data through the API, including independent daily ATR.

python validate_history_source.py --symbol TCS
Requires the dev requirements and a previously downloaded historical snapshot.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from historical_data import provider_directory
from nifty_bars import create_app, read_minutes
from stock_data import DEFAULT_DATA, cached_stock


def daily_ranges(path, symbol):
    # Batch daily OHLC reference, independent of the streaming ATR class.
    days = {}
    for c in read_minutes(path, expected_instrument=symbol):
        key = str(c.timestamp.date())
        d = days.setdefault(key, [c.timestamp, c.timestamp, c.high, c.low, c.close, 0])
        d[1], d[2], d[3], d[4], d[5] = c.timestamp, max(d[2], c.high), min(d[3], c.low), c.close, d[5]+1
    return {key: d for key, d in days.items()
            if d[0].strftime('%H:%M') == '09:15' and d[1].strftime('%H:%M') == '15:29' and d[5] == 375}


def reference_atr(days, period):
    ranges, result, previous, value = [], {}, None, None
    for key, (_, _, high, low, close, _) in days.items():
        tr = high-low if previous is None else max(high-low, abs(high-previous), abs(low-previous))
        ranges.append(tr)
        previous = close
        if len(ranges) == period:
            value = sum(ranges, Decimal(0))/period
        elif len(ranges) > period:
            value = (value*(period-1)+tr)/period
        if value is not None:
            result[key] = float(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', default='TCS')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output', type=Path, default=Path('examples/tcs_history_validation.json'))
    args = parser.parse_args()
    path, source = cached_stock(args.symbol, provider_directory('kaggle', args.data_dir))
    symbol = source['symbol']
    days = daily_ranges(path, symbol)
    report = {'source': source, 'checks': [], 'independent_exchange_price_verification': False}
    with TestClient(create_app(stock_dir=args.data_dir, default_symbol=symbol)) as client:
        for mode, size in [('percentage', 1), ('percentage', 2), ('percentage', 3),
                           ('atr', 7), ('atr', 10), ('atr', 14), ('atr', 21)]:
            query = {'symbol': symbol, 'provider': 'kaggle', 'sizing': mode,
                     'percentage' if mode == 'percentage' else 'atr_period': size}
            response = client.get('/bars', params=query)
            assert response.status_code == 200, response.text[:500]
            data = response.json()
            meta, bars = data['metadata'], data['bars']
            expected_atr = reference_atr(days, size) if mode == 'atr' else {}
            moves = [b for b in bars if b['kind'] == 'move']
            assert moves and meta['sizing'] == mode
            assert meta['percentage' if mode == 'percentage' else 'atr_period'] == size
            for b in bars:
                assert b['high'] == max(b['open'], b['close'])
                assert b['low'] == min(b['open'], b['close'])
                if b['kind'] == 'move':
                    if mode == 'percentage':
                        assert abs(abs(b['close']/b['open']-1)*100-size) < 1e-9
                    else:
                        assert b['atr_as_of'] < b['known_at'][:10]
                        expected = expected_atr[b['atr_as_of']]
                        assert abs(b['brick_size_points']-expected) < 1e-9
                        assert abs(abs(b['close']-b['open'])-expected) < 1e-9
                elif b['kind'] == 'partial':
                    assert b['reason'] == 'before_large_gap' and b['open'] != b['close']
                elif mode == 'percentage':
                    assert abs(b['move_pct']) >= size-1e-9
                else:
                    assert abs(b['close']-b['open']) >= b['brick_size_points']-1e-9
            for a, b in zip(bars, bars[1:]):
                assert a['close'] == b['open']
                if a['kind'] == 'partial':
                    assert b['kind'] in ('session_gap', 'data_gap')
            pending = meta['pending_move'] or {}
            assert sum(b['source_minutes'] for b in bars)+pending.get('source_minutes', 0) == meta['source_minutes']
            assert sum((b.get('ath_breakout') or {}).get('count', 0) for b in bars)+(pending.get('ath_breakout') or {}).get('count', 0) == meta['ath_breakout_observations']
            check = {'request': query, 'bar_counts': meta['bar_counts'],
                     'exact_full_bricks': True, 'connected_boundaries': True,
                     'all_source_minutes_accounted_for': True, 'all_high_observations_accounted_for': True,
                     'no_ordinary_partials': True, 'pending_move': meta['pending_move']}
            if mode == 'atr':
                check.update(independent_daily_atr_matches=True, no_current_day_atr=True,
                             last_atr_points=meta['last_atr_points'], first_full_brick=moves[0]['known_at'])
            report['checks'].append(check)
            print(json.dumps({k:v for k,v in check.items() if k != 'pending_move'}), flush=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
