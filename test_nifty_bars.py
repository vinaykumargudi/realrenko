import tempfile
import unittest
import subprocess
import sys
import json
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from nifty_bars import Candle, IST, convert_minutes, create_app, filter_result, make_chart, parse_percent, read_minutes

# These established fixtures exercise the retained daily-remainder policy.
# Default threshold-mode behavior is covered separately in test_threshold_mode.py.
from functools import partial as bind_policy
convert_minutes = bind_policy(convert_minutes, session_mode="daily")


def c(ts, o, h=None, l=None, close=None):
    o, close = D(str(o)), D(str(o if close is None else close))
    return Candle(datetime.fromisoformat(ts).replace(tzinfo=IST), o,
                  max(o, close) if h is None else D(str(h)),
                  min(o, close) if l is None else D(str(l)), close)


def full(rows, percentage=1):
    return convert_minutes(rows, percentage, include_partials=True)


class ConversionTests(unittest.TestCase):
    def assert_exact(self, bars, percentage=1):
        moves = [b for b in bars if b['kind'] == 'move']
        for b in moves:
            # Inspect actual plotted OHLC, not just a formatted percentage label.
            self.assertAlmostEqual(abs(b['close'] / b['open'] - 1) * 100, percentage, places=10)
            self.assertAlmostEqual((b['high'] - b['low']) / b['open'] * 100, percentage, places=10)
            self.assertEqual(b['high'], max(b['open'], b['close']))
            self.assertEqual(b['low'], min(b['open'], b['close']))
            self.assertEqual(abs(b['move_pct']), percentage)
        return moves

    def test_reported_overshoots_split_both_directions(self):
        for move, expected in ((1.3, 1), (1.4, 1), (2.18, 2), (-1.3, 1), (-1.4, 1), (-2.18, 2)):
            with self.subTest(move=move):
                result = full([c('2024-01-01T09:15', 100, close=D(100) + D(str(move)))])
                self.assertEqual(len(self.assert_exact(result['bars'])), expected)
                tail = result['bars'][-1]
                self.assertEqual(tail['kind'], 'partial')
                self.assertLess(abs(tail['move_pct']), 1)
                self.assertEqual(tail['close'], 100 + move)

    def test_actual_wicks_are_separate_from_exact_bricks(self):
        rows = [c('2024-01-01T09:15', '100', '100.9', '99', '100.5'),
                c('2024-01-01T09:16', '100.5', '101.3', '100.4', '101')]
        b = convert_minutes(rows, '1%')['bars'][0]
        self.assertEqual((b['open'], b['high'], b['low'], b['close']), (100, 101, 100, 101))
        self.assertEqual(b['observed_ohlc'], {'open': 100, 'high': 101.3, 'low': 99, 'close': 101})
        self.assertEqual(b['source_minutes'], 2)
        self.assertEqual(b['known_at'], '2024-01-01T09:17:00+05:30')
        self.assertTrue(b['synthetic_ohlc'])
        self.assertEqual(convert_minutes([c('2024-01-01T09:15', '0.1', close='0.101')])['bars'][0]['kind'], 'move')

    def test_no_premature_rounding_and_default_shows_partial(self):
        rows = [c('2024-01-01T09:15', 100, close='100.999999')]
        self.assertEqual(convert_minutes(rows, include_partials=False)['bars'], [])
        self.assertEqual(convert_minutes(rows)['bars'][0]['kind'], 'partial')

    def test_multiple_compounded_bricks_from_one_close(self):
        result = full([c('2024-01-01T09:15', 100, close=103.2)])
        moves = self.assert_exact(result['bars'])
        self.assertEqual([b['close'] for b in moves], [101, 102.01, 103.0301])
        self.assertEqual([b['source_minutes'] for b in moves], [1, 0, 0])
        self.assertEqual(len({b['known_at'] for b in moves}), 1)
        self.assertEqual(result['bars'][-1]['close'], 103.2)
        self.assertEqual(result['bars'][-1]['open'], 103.0301)

    def test_residual_carries_to_next_minute_not_its_open_or_prior_close(self):
        rows = [c('2024-01-01T09:15', 100, close=101.4),
                c('2024-01-01T09:16', 101.5, close=102.02)]
        bars = full(rows)['bars']
        moves = self.assert_exact(bars)
        self.assertEqual([(b['open'], b['close']) for b in moves], [(100, 101), (101, 102.01)])
        self.assertEqual(bars[-1]['close'], 102.02)

    def test_reversal_from_last_brick_anchor(self):
        rows = [c('2024-01-01T09:15', 100, close=101.4),
                c('2024-01-01T09:16', 101.3, close=99.8)]
        moves = self.assert_exact(convert_minutes(rows)['bars'])
        self.assertEqual([(b['open'], b['close']) for b in moves], [(100, 101), (101, 99.99)])

    def test_gap_up_and_down_are_single_full_bridges(self):
        for next_open, pct in ((103, 3), (97, -3)):
            with self.subTest(next_open=next_open):
                rows = [c('2024-01-01T15:29', 100), c('2024-01-02T09:15', next_open)]
                bars = full(rows)['bars']
                gap = [b for b in bars if b['kind'] == 'session_gap']
                self.assertEqual(len(gap), 1)
                self.assertEqual(gap[0]['move_pct'], pct)
                self.assertEqual((gap[0]['open'], gap[0]['close']), (100, next_open))
                self.assertTrue(gap[0]['synthetic_bridge'])
                self.assertEqual(sum(b['source_minutes'] for b in bars), 2)
                self.assertEqual(len(convert_minutes(rows)['bars']), 3)

    def test_gap_uses_actual_close_after_residual_and_resets_anchor(self):
        rows = [c('2024-01-01T15:29', 99.5, close=100),
                c('2024-01-02T09:15', 103, close=104.4)]
        bars = full(rows)['bars']
        gap = next(b for b in bars if b['kind'] == 'session_gap')
        self.assertEqual((gap['open'], gap['close'], gap['move_pct']), (100, 103, 3))
        self.assertEqual([(b['open'], b['close']) for b in bars if b['kind'] == 'move'], [(103, 104.03)])

    def test_same_minute_both_thresholds_uses_close_only(self):
        rows = [c('2024-01-01T09:15', 100, 102, 98, 100.1)]
        self.assertEqual(convert_minutes(rows, include_partials=False)['bars'], [])
        b = full(rows)['bars'][0]
        self.assertEqual(b['observed_ohlc']['high'], 102)
        self.assertEqual(b['observed_ohlc']['low'], 98)

    def test_missing_minutes_are_not_market_gaps(self):
        rows = [c('2024-01-01T09:15', 100), c('2024-01-01T09:18', 105)]
        bars = full(rows)['bars']
        self.assertEqual([b['kind'] for b in bars], ['partial', 'data_gap', 'partial'])
        self.assertEqual(bars[1]['move_pct'], 5)
        self.assertEqual(bars[0]['known_at'], '2024-01-01T09:18:00+05:30')

    def test_incomplete_session_endpoints_flag_unknown_gap(self):
        rows = [c('2024-01-01T14:00', 100), c('2024-01-02T09:15', 103)]
        self.assertEqual(convert_minutes(rows)['bars'][1]['kind'], 'data_gap')

    def test_small_session_gap_preserved_and_zero_gap_omitted(self):
        for price, expected in ((100.1, 1), (100, 0)):
            rows = [c('2024-01-01T15:29', 100), c('2024-01-02T09:15', price)]
            self.assertEqual(sum(b['kind'] == 'session_gap' for b in convert_minutes(rows)['bars']), expected)

    def test_custom_percent_both_directions(self):
        for percentage in (0.5, 2):
            for close in (90, 110):
                bars = convert_minutes([c('2024-01-01T09:15', 100, close=close)], percentage)['bars']
                self.assertGreater(len(self.assert_exact(bars, percentage)), 1)

    def test_three_percent_daily_remainders_then_full_opening_gap(self):
        for change in (2, 4, -2, -4):
            for gap_pct in (1, -1, 8, -8):
                with self.subTest(change=change, gap_pct=gap_pct):
                    start = datetime(2024, 1, 1, 9, 15, tzinfo=IST)
                    rows, price = [], D(100)
                    for i in range(375):
                        close = D(100) + D(change) * D(i + 1) / D(375)
                        rows.append(Candle(start + timedelta(minutes=i), price,
                                           max(price, close), min(price, close), close))
                        price = close
                    next_open = price * (1 + D(gap_pct) / D(100))
                    rows.append(c('2024-01-02T09:15', next_open))
                    result = convert_minutes(rows, 3)
                    bars = result['bars']
                    kinds = (['move'] if abs(change) == 4 else []) + ['partial', 'session_gap', 'partial']
                    self.assertEqual([b['kind'] for b in bars], kinds)
                    self.assert_exact(bars, 3)
                    for a, b in zip(bars, bars[1:]):
                        self.assertEqual(a['close'], b['open'])
                    remainder, gap = bars[-3:-1]
                    self.assertEqual(remainder['close'], 100 + change)
                    self.assertTrue(remainder['finalized'])
                    if abs(change) == 4:
                        anchor = 103 if change > 0 else 97
                        self.assertEqual(remainder['open'], anchor)
                        self.assertAlmostEqual(remainder['move_pct'], (100 + change - anchor) / anchor * 100)
                    else:
                        self.assertEqual(remainder['move_pct'], change)
                    self.assertEqual(gap['move_pct'], gap_pct)
                    self.assertEqual(gap['close'], float(next_open))
                    self.assertEqual(sum(b['source_minutes'] for b in bars), 376)
                    self.assertEqual(filter_result(result)['bars'], bars)

    def test_flat_days_remain_visible_and_exact_threshold_needs_no_extra_remainder(self):
        bars = convert_minutes([c('2024-01-01T15:29', 100), c('2024-01-02T09:15', 100)], 3)['bars']
        self.assertEqual([b['kind'] for b in bars], ['partial', 'partial'])
        self.assertTrue(all(b['open'] == b['close'] == 100 for b in bars))
        exact = convert_minutes([c('2024-01-01T15:29', 100, close=103), c('2024-01-02T09:15', '104.03')], 3)['bars']
        self.assertEqual([b['kind'] for b in exact], ['move', 'session_gap', 'partial'])
        self.assertEqual(exact[0]['close'], exact[1]['open'])

    def test_reject_invalid_inputs(self):
        for p in (0, -1, 'NaN', 'Infinity', 100, 'bad'):
            with self.subTest(p=p), self.assertRaises(ValueError):
                parse_percent(p)
        candle = c('2024-01-01T09:15', 100)
        for rows in ([], [candle, candle], [c('2024-01-01T09:15', 100, 99, 98, 100)],
                     [c('2024-01-01T09:15', 'NaN', 100, 100, 100)]):
            with self.assertRaises(ValueError):
                convert_minutes(rows)
        with self.assertRaises(ValueError):
            convert_minutes([candle], '1e-100')

    def test_window_filter_does_not_recompute_blocks(self):
        rows = [c('2024-01-01T15:29', 100), c('2024-01-02T09:15', 103)]
        result = full(rows)
        window = filter_result(result, date(2024, 1, 2), date(2024, 1, 2), include_partials=True)
        self.assertEqual(window['bars'], result['bars'][1:])

    def test_csv_dayfirst_seconds_and_collision_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.csv'
            header = 'Instrument,Date,Time,Open,High,Low,Close\n'
            line = 'Nifty50,09-01-2015,9:15:01,100,101,100,101\n'
            path.write_text(header + line)
            result = convert_minutes(read_minutes(path))
            self.assertEqual(result['metadata']['first_minute'], '2015-01-09T09:15:00+05:30')
            self.assertEqual(result['metadata']['timestamp_labels_floored_to_minute'], 1)
            path.write_text(header + line + line.replace('9:15:01', '9:15:02'))
            with self.assertRaises(ValueError):
                convert_minutes(read_minutes(path, duplicate_policy='error'))
            merged = full(read_minutes(path))
            self.assertEqual(merged['metadata']['source_rows'], 2)
            self.assertEqual(merged['metadata']['source_minutes'], 1)
            self.assertEqual(merged['metadata']['timestamp_labels_floored_to_minute'], 2)
            self.assertEqual(len(merged['metadata']['merged_minute_buckets']), 1)


class IntegrationTests(unittest.TestCase):
    assert_exact = ConversionTests.assert_exact

    def test_cli_includes_remainders_without_extra_flags(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'source.csv', Path(temp) / 'bars.json'
            source.write_text('Instrument,Date,Time,Open,High,Low,Close\n'
                              'Nifty50,01-01-2024,15:29:00,100,104,100,104\n'
                              'Nifty50,02-01-2024,09:15:00,105.04,105.04,105.04,105.04\n')
            command = [sys.executable, str(Path(__file__).with_name('nifty_bars.py')), '--session-mode', 'daily',
                       '--csv', str(source), '--output', str(output), '--percentage', '3']
            subprocess.run(command, check=True, capture_output=True, text=True)
            result = json.loads(output.read_text())
            self.assertEqual([b['kind'] for b in result['bars']], ['move', 'partial', 'session_gap', 'partial'])
            client_result = TestClient(create_app(source)).get('/bars?session_mode=daily&percentage=3').json()
            self.assertEqual(client_result['bars'], result['bars'])
            subprocess.run(command + ['--no-include-partials'], check=True, capture_output=True, text=True)
            self.assertEqual([b['kind'] for b in json.loads(output.read_text())['bars']], ['move', 'session_gap'])

    def test_api_and_plot_actual_bodies_not_only_labels(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.csv'
            path.write_text('Instrument,Date,Time,Open,High,Low,Close\n'
                            'Nifty50,01-01-2024,15:29:00,100,101.4,100,101.4\n'
                            'Nifty50,02-01-2024,09:15:00,104.442,109,103,106.7188356\n')
            client = TestClient(create_app(path))
            r = client.get('/bars', params={'percentage': '1%'})
            self.assertEqual(r.status_code, 200, r.text)
            result = r.json()
            self.assertEqual(result['metadata']['mode'], 'exact_percentage_close')
            self.assertTrue(self.assert_exact(result['bars']))
            self.assertIn('partial', [b['kind'] for b in result['bars']])
            sparse = client.get('/bars?session_mode=daily&percentage=1&include_partials=false').json()
            self.assertNotIn('partial', [b['kind'] for b in sparse['bars']])
            gap = next(b for b in result['bars'] if b['kind'] == 'session_gap')
            self.assertEqual(gap['move_pct'], 3)
            self.assertEqual(client.get('/bars?session_mode=daily&percentage=NaN').status_code, 422)
            self.assertEqual(client.get('/bars?session_mode=daily&start=2024-02-01&end=2024-01-01').status_code, 422)
            full_result = client.get('/bars?session_mode=daily&percentage=1&include_partials=true').json()
            self.assertIn('partial', [b['kind'] for b in full_result['bars']])
            self.assertEqual(sum(b['source_minutes'] for b in full_result['bars']), 2)
            html = client.get('/chart?percentage=1')
            self.assertEqual(html.status_code, 200, html.text[:200])
            self.assertIn('/assets/chart.mjs', html.text)
            self.assertIn('Daily price trend', html.text)
            self.assertEqual(client.get('/assets/chart.css').status_code, 200)
            self.assertEqual(client.get('/assets/vendor/lightweight-charts-5.2.1.js').status_code, 200)
            self.assertEqual(client.get('/').status_code, 200)
            fig = make_chart(result)
            normal = next(t for t in fig.data if t.name == 'move')
            for o, h, l, close in zip(normal.open, normal.high, normal.low, normal.close):
                self.assertAlmostEqual(abs(close / o - 1) * 100, 1, places=10)
                self.assertAlmostEqual((h - l) / o * 100, 1, places=10)
            self.assertIn('partial', [t.name for t in fig.data])
            path.unlink()
            self.assertEqual(client.get('/bars').status_code, 503)


if __name__ == '__main__':
    unittest.main()
