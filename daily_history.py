"""Observed daily OHLC from the supplied minute history (not official NSE closes)."""
from collections import Counter
from datetime import time

from nifty_bars import HUNDRED, MINUTE, read_minutes


def daily_history(path):
    bars = []
    active = None
    first = last = None
    minutes = raw_rows = 0

    def finish():
        if active is None:
            return
        o, h, l, close = (active[k] for k in ('open', 'high', 'low', 'close'))
        complete = (active['start'].time() == time(9, 15)
                    and active['last'].time() == time(15, 29) and active['count'] == 375)
        bars.append({
            'bar_id': len(bars), 'kind': 'price',
            'start': active['start'].isoformat(), 'end': (active['last'] + MINUTE).isoformat(),
            'known_at': (active['last'] + MINUTE).isoformat(),
            'open': float(o), 'high': float(h), 'low': float(l), 'close': float(close),
            'move_pct': float((close - o) / o * HUNDRED),
            'range_pct': float((h - l) / o * HUNDRED),
            'source_minutes': active['count'], 'synthetic_ohlc': False, 'synthetic_bridge': False,
            'complete_normal_session': complete, 'finalized': complete,
            'direction': 'up' if close > o else 'down' if close < o else 'flat',
            'reason': 'observed_daily_ohlc',
        })

    for candle in read_minutes(path):
        first = first or candle
        last = candle
        minutes += 1
        raw_rows += candle.source_rows
        if active is None or candle.timestamp.date() != active['start'].date():
            finish()
            active = {'start': candle.timestamp, 'open': candle.open, 'high': candle.high,
                      'low': candle.low, 'count': 0}
        active['high'] = max(active['high'], candle.high)
        active['low'] = min(active['low'], candle.low)
        active['close'], active['last'] = candle.close, candle.timestamp
        active['count'] += 1
    if first is None:
        raise ValueError('No minute candles supplied')
    finish()
    return {'metadata': {
        'mode': 'observed_daily_price', 'timezone': 'Asia/Kolkata',
        'first_minute': first.timestamp.isoformat(), 'last_minute': last.timestamp.isoformat(),
        'source_minutes': minutes, 'source_rows': raw_rows, 'observed_sessions': len(bars),
        'bar_counts': dict(Counter(b['kind'] for b in bars)),
        'notes': ['Daily OHLC aggregates observed minute prices; it is not the official calculated closing index.',
                  'Incomplete/nonstandard sessions are flagged; missing prices are never forward filled.'],
    }, 'bars': bars}
