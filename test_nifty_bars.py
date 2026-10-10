import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path

from nifty_bars import Candle, IST, convert_minutes, create_app, filter_result, make_chart, parse_percent, read_minutes


def c(ts, o, h=None, l=None, close=None):
    o, close = D(str(o)), D(str(o if close is None else close))
    return Candle(datetime.fromisoformat(ts).replace(tzinfo=IST), o,
                  max(o, close) if h is None else D(str(h)),
                  min(o, close) if l is None else D(str(l)), close)


class ConversionTests(unittest.TestCase):
    def test_decimal_exact_boundary_and_true_wicks(self):
        rows = [c('2024-01-01T09:15', '100', '100.9', '99', '100.5'),
                c('2024-01-01T09:16', '100.5', '101.3', '100.4', '101')]
        b = convert_minutes(rows, '1%')['bars'][0]
        self.assertEqual((b['kind'], b['open'], b['high'], b['low'], b['close']),
                         ('move', 100, 101.3, 99, 101))
        self.assertEqual(b['source_minutes'], 2)
        self.assertEqual(b['known_at'], '2024-01-01T09:17:00+05:30')
        self.assertEqual(convert_minutes([c('2024-01-01T09:15', '0.1', close='0.101')])['bars'][0]['kind'], 'move')

    def test_no_premature_rounding(self):
        b = convert_minutes([c('2024-01-01T09:15', 100, close='100.999999')])['bars'][0]
        self.assertEqual(b['kind'], 'partial')

    def test_large_overshoot_not_split(self):
        bars = convert_minutes([c('2024-01-01T09:15', 100, close=103.2)])['bars']
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0]['move_pct'], 3.2)
        self.assertEqual(bars[0]['close'], 103.2)

    def test_gap_up_and_down_are_single_full_bridges(self):
        for next_open, pct in ((103, 3), (97, -3)):
            with self.subTest(next_open=next_open):
                rows = [c('2024-01-01T15:29', 100), c('2024-01-02T09:15', next_open)]
                bars = convert_minutes(rows)['bars']
                gap = [b for b in bars if b['kind'] == 'session_gap']
                self.assertEqual(len(gap), 1)
                self.assertEqual(gap[0]['move_pct'], pct)
                self.assertEqual((gap[0]['open'], gap[0]['close']), (100, next_open))
                self.assertTrue(gap[0]['synthetic_bridge'])
                self.assertEqual(sum(b['source_minutes'] for b in bars), 2)

    def test_gap_uses_previous_actual_close_not_block_open(self):
        rows = [c('2024-01-01T15:29', 99.5, close=100), c('2024-01-02T09:15', 103)]
        bars = convert_minutes(rows)['bars']
        self.assertEqual(bars[0]['kind'], 'partial')
        self.assertEqual(bars[1]['move_pct'], 3)
        self.assertEqual(bars[2]['open'], 103)

    def test_same_minute_both_thresholds_does_not_invent_path(self):
        b = convert_minutes([c('2024-01-01T09:15', 100, 102, 98, 100.1)])['bars'][0]
        self.assertEqual((b['kind'], b['high'], b['low']), ('partial', 102, 98))

    def test_missing_minutes_are_not_market_gaps(self):
        rows = [c('2024-01-01T09:15', 100), c('2024-01-01T09:18', 105)]
        bars = convert_minutes(rows)['bars']
        self.assertEqual([b['kind'] for b in bars], ['partial', 'data_gap', 'partial'])
        self.assertEqual(bars[1]['move_pct'], 5)
        self.assertEqual(bars[0]['known_at'], '2024-01-01T09:18:00+05:30')

    def test_incomplete_session_endpoints_flag_unknown_gap(self):
        rows = [c('2024-01-01T14:00', 100), c('2024-01-02T09:15', 103)]
        self.assertEqual(convert_minutes(rows)['bars'][1]['kind'], 'data_gap')

    def test_downward_threshold_and_next_bar_actual_open(self):
        rows = [c('2024-01-01T09:15', 100, close=99), c('2024-01-01T09:16', 99.1)]
        bars = convert_minutes(rows)['bars']
        self.assertEqual((bars[0]['direction'], bars[0]['move_pct']), ('down', -1))
        self.assertEqual(bars[1]['open'], 99.1)
        self.assertFalse(bars[1]['finalized'])

    def test_small_session_gap_preserved_and_zero_gap_omitted(self):
        for price, expected in ((100.1, 1), (100, 0)):
            rows = [c('2024-01-01T15:29', 100), c('2024-01-02T09:15', price)]
            self.assertEqual(sum(b['kind'] == 'session_gap' for b in convert_minutes(rows)['bars']), expected)

    def test_reject_invalid_inputs(self):
        for p in (0, -1, 'NaN', 'Infinity', 100, 'bad'):
            with self.subTest(p=p), self.assertRaises(ValueError):
                parse_percent(p)
        candle = c('2024-01-01T09:15', 100)
        for rows in ([], [candle, candle], [c('2024-01-01T09:15', 100, 99, 98, 100)],
                     [c('2024-01-01T09:15', 'NaN', 100, 100, 100)]):
            with self.assertRaises(ValueError):
                convert_minutes(rows)

    def test_window_filter_does_not_recompute_blocks(self):
        rows = [c('2024-01-01T15:29', 100), c('2024-01-02T09:15', 103)]
        full = convert_minutes(rows)
        window = filter_result(full, date(2024, 1, 2), date(2024, 1, 2))
        self.assertEqual(window['bars'], full['bars'][1:])

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
            merged = convert_minutes(read_minutes(path))
            self.assertEqual(merged['metadata']['source_rows'], 2)
            self.assertEqual(merged['metadata']['source_minutes'], 1)
            self.assertEqual(merged['metadata']['timestamp_labels_floored_to_minute'], 2)
            self.assertEqual(len(merged['metadata']['merged_minute_buckets']), 1)


class IntegrationTests(unittest.TestCase):
    def test_api_json_chart_and_validation(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.csv'
            path.write_text('Instrument,Date,Time,Open,High,Low,Close\n'
                            'Nifty50,01-01-2024,15:29:00,100,100,100,100\n'
                            'Nifty50,02-01-2024,09:15:00,103,103,103,103\n')
            client = TestClient(create_app(path))
            r = client.get('/bars', params={'percentage': '1%', 'start': '2024-01-02'})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['bars'][0]['move_pct'], 3)
            self.assertEqual(client.get('/bars?percentage=NaN').status_code, 422)
            self.assertEqual(client.get('/bars?start=2024-02-01&end=2024-01-01').status_code, 422)
            html = client.get('/chart?percentage=1')
            self.assertEqual(html.status_code, 200, html.text[:200])
            self.assertIn('plotly', html.text.lower())
            fig = make_chart(convert_minutes(read_minutes(path)))
            gap = next(trace for trace in fig.data if trace.name == 'session_gap')
            self.assertEqual((list(gap.open), list(gap.close)), ([100], [103]))
            path.unlink()
            self.assertEqual(client.get('/bars').status_code, 503)


if __name__ == '__main__':
    unittest.main()
