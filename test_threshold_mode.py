"""Regression examples for the user's threshold-only / large-gap exception."""
import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D

from nifty_bars import Candle, IST, convert_minutes


def candle(ts, o, close=None, high=None):
    o, c = D(str(o)), D(str(o if close is None else close))
    return Candle(datetime.fromisoformat(ts).replace(tzinfo=IST), o,
                  D(str(high)) if high is not None else max(o, c), min(o, c), c)


class ThresholdTests(unittest.TestCase):
    def check(self, result, percent=2):
        bars = result['bars']
        for b in bars:
            if b['kind'] == 'move':
                self.assertAlmostEqual(abs(b['close'] / b['open'] - 1) * 100, percent, places=10)
            elif b['kind'] == 'partial':
                self.assertEqual(b['reason'], 'before_large_gap')
                self.assertNotEqual(b['open'], b['close'])
            else:
                self.assertGreaterEqual(abs(b['move_pct']) + 1e-10, percent)
        for left, right in zip(bars, bars[1:]):
            self.assertEqual(left['close'], right['open'])
        pending = result['metadata']['pending_move']
        self.assertEqual(sum(b['source_minutes'] for b in bars) + (pending or {}).get('source_minutes', 0),
                         result['metadata']['source_minutes'])
        return bars

    def test_one_percent_is_pending_not_a_block(self):
        r = convert_minutes([candle('2024-01-01T15:29', 100, 101)], 2)
        self.assertEqual(self.check(r), [])
        self.assertEqual(r['metadata']['pending_move']['move_pct'], 1)

    def test_small_gap_completes_brick_at_open_no_current_high_lookahead(self):
        r = convert_minutes([candle('2024-01-01T15:29', 100, 101),
                             candle('2024-01-02T09:15', 103, 102.5, high=110)], 2)
        bars = self.check(r)
        self.assertEqual([(b['kind'], b['open'], b['close']) for b in bars], [('move', 100, 102)])
        b = bars[0]
        self.assertEqual(b['known_at'], '2024-01-02T09:15:00+05:30')
        self.assertEqual(b['confirmation'], 'session_open')
        self.assertIsNone(b['trigger_minute'])
        self.assertEqual(b['observed_ohlc']['high'], 103)
        self.assertEqual(b['ath_breakout']['high'], 103)
        self.assertEqual(r['metadata']['pending_move']['ath_breakout']['high'], 110)

    def test_true_three_percent_gap_is_not_reduced_to_two(self):
        r = convert_minutes([candle('2024-01-01T15:29', 100, 101),
                             candle('2024-01-02T09:15', '104.03')], 2)
        bars = self.check(r)
        self.assertEqual([(b['kind'], b['open'], b['close'], b['move_pct']) for b in bars],
                         [('partial', 100, 101, 1), ('session_gap', 101, 104.03, 3)])

    def test_gap_exactly_threshold_and_downward_case(self):
        for prior, next_open, expected in [(101, '103.02', 2), (99, '97.02', -2)]:
            r = convert_minutes([candle('2024-01-01T15:29', 100, prior),
                                 candle('2024-01-02T09:15', next_open)], 2)
            bars = self.check(r)
            self.assertEqual([b['kind'] for b in bars], ['partial', 'session_gap'])
            self.assertEqual(bars[1]['move_pct'], expected)

    def test_small_gap_down_completes_full_down_brick(self):
        r = convert_minutes([candle('2024-01-01T15:29', 100, 99),
                             candle('2024-01-02T09:15', 98)], 2)
        bars = self.check(r)
        self.assertEqual([(b['open'], b['close']) for b in bars], [(100, 98)])

    def test_opposite_small_gap_offsets_pending_without_partial(self):
        r = convert_minutes([candle('2024-01-01T15:29', 100, 101),
                             candle('2024-01-02T09:15', 100)], 2)
        self.assertEqual(self.check(r), [])
        self.assertEqual(r['metadata']['pending_move']['move_pct'], 0)

    def test_flat_pre_gap_does_not_print_zero_brick(self):
        r = convert_minutes([candle('2024-01-01T15:29', 100),
                             candle('2024-01-02T09:15', 103)], 2)
        self.assertEqual([b['kind'] for b in self.check(r)], ['session_gap'])

    def test_missing_interval_is_explicit_not_called_overnight_gap(self):
        r = convert_minutes([candle('2024-01-01T15:15', 100, 101),
                             candle('2024-01-02T09:15', 103)], 2)
        bars = self.check(r)
        self.assertEqual(bars[0]['confirmation'], 'observed_open_after_missing_data')
        self.assertEqual(r['metadata']['absorbed_unknown_intervals'], 1)
        r = convert_minutes([candle('2024-01-01T15:15', 100, 101),
                             candle('2024-01-02T09:15', 105)], 2)
        self.assertEqual(self.check(r)[-1]['kind'], 'data_gap')

    def test_large_close_move_splits_and_carries_geometrically(self):
        r = convert_minutes([candle('2024-01-01T09:15', 100, 107)], 2)
        bars = self.check(r)
        self.assertEqual([b['close'] for b in bars], [102, 104.04, 106.1208])
        self.assertAlmostEqual(r['metadata']['pending_move']['last_price'], 107)

    def test_prefix_bars_never_use_future_prices(self):
        rows = [candle('2024-01-01T15:29', 100, 101),
                candle('2024-01-02T09:15', 103, 104),
                candle('2024-01-02T09:16', 104, 99),
                candle('2024-01-03T09:15', 110, 114)]
        result = convert_minutes(rows, 2)
        self.check(result)
        for count in range(1, len(rows)):
            prefix = convert_minutes(rows[:count], 2)
            self.check(prefix)
            self.assertEqual(prefix['bars'], result['bars'][:len(prefix['bars'])])

    def test_atr_waits_for_complete_daily_history_and_uses_prior_size(self):
        from test_atr_and_highs import day
        rows = (day(1, 100, 104, 98, 102) + day(2, 110, 114, 109, 112)
                + day(3, 112, 130, 110, 130) + day(4, 140, 151, 138, 150))
        warmup = convert_minutes(rows[:750], sizing='atr', atr_period=2)
        self.assertEqual(warmup['bars'], [])
        self.assertEqual(warmup['metadata']['pending_move']['source_minutes'], 750)
        result = convert_minutes(rows, sizing='atr', atr_period=2)
        for bar in result['bars']:
            self.assertGreaterEqual(bar['known_at'][:10], '2024-01-03')
            self.assertEqual(bar['atr_status'], 'ready')
            if bar['kind'] == 'move':
                expected = 9 if bar['known_at'][:10] == '2024-01-03' else 14.5
                self.assertAlmostEqual(abs(bar['close']-bar['open']), expected)
        self.assertEqual(sum(b['source_minutes'] for b in result['bars'])
                         + (result['metadata']['pending_move'] or {}).get('source_minutes', 0), len(rows))


if __name__ == '__main__':
    unittest.main()
