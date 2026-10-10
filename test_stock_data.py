import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from nifty_bars import create_app, read_minutes
from stock_data import DownloadError, cached_stock, download_stock, normalize_symbol

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def response(symbol='TCS', timestamps=None, prices=None):
    stamps = timestamps if timestamps is not None else [int(datetime(2026, 10, 9, 3, 45, tzinfo=timezone.utc).timestamp())]
    values = prices or [100] * len(stamps)
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol': symbol + '.NS', 'currency': 'INR', 'instrumentType': 'EQUITY',
                 'exchangeTimezoneName': 'Asia/Kolkata', 'dataGranularity': '1m'},
        'timestamp': stamps, 'indicators': {'quote': [{k: list(values) for k in ('open', 'high', 'low', 'close')}]}
    }]}}


class StockTests(unittest.TestCase):
    def test_symbol_validation_and_no_paths_or_other_exchanges(self):
        self.assertEqual(normalize_symbol(' tcs.ns '), 'TCS')
        self.assertEqual(normalize_symbol('M&M'), 'M&M')
        for value in ['../TCS', 'TCS/NS', 'AAPL.US', '^NSEI', '', 'https://example.com']:
            with self.assertRaises(ValueError): normalize_symbol(value)

    def test_chunk_boundaries_ist_csv_decimal_and_atomic_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            def fetch(url):
                q = parse_qs(urlsplit(url).query)
                start, end = int(q['period1'][0]), int(q['period2'][0])
                self.assertLessEqual(end - start, 7 * 86400)
                return response() if start <= response()['chart']['result'][0]['timestamp'][0] < end else response(timestamps=[])
            with patch('stock_data._fetch', side_effect=fetch) as mock:
                path, meta = download_stock('TCS.NS', 28, Path(directory), now=NOW)
            self.assertEqual(mock.call_count, 4)
            self.assertEqual(meta['downloaded_minutes'], 1)
            self.assertEqual(list(read_minutes(path, expected_instrument='TCS'))[0].timestamp.hour, 9)
            self.assertEqual(cached_stock('TCS', Path(directory))[0], path)
            with patch('stock_data._fetch', side_effect=DownloadError('offline')):
                with self.assertRaises(DownloadError): download_stock('TCS', 7, Path(directory), now=NOW)
            self.assertEqual(cached_stock('TCS', Path(directory))[0], path)

    def test_nulls_incomplete_and_post_session_quotes_are_not_filled(self):
        stamps = [int(datetime(2026, 10, 9, 3, 45, tzinfo=timezone.utc).timestamp()) + 60 * x for x in (0, 1, 375)]
        payload = response(timestamps=stamps, prices=[100, None, 101])
        with tempfile.TemporaryDirectory() as directory, patch('stock_data._fetch', return_value=payload):
            path, meta = download_stock('TCS', 7, Path(directory), now=NOW)
            self.assertEqual(len(list(read_minutes(path))), 1)
            self.assertEqual(meta['skipped_rows'], {'missing_ohlc': 1, 'outside_regular_session': 1})
            mid_minute = datetime(2026, 10, 9, 3, 45, 30, tzinfo=timezone.utc)
            with self.assertRaises(DownloadError): download_stock('TCS', 7, Path(directory), now=mid_minute)

    def test_wrong_instrument_invalid_ohlc_duplicates_and_limits_fail(self):
        for modifier in ('symbol', 'granularity', 'high', 'duplicate'):
            payload = response()
            item = payload['chart']['result'][0]
            if modifier == 'symbol': item['meta']['symbol'] = 'INFY.NS'
            if modifier == 'granularity': item['meta']['dataGranularity'] = '1d'
            if modifier == 'high': item['indicators']['quote'][0]['high'] = [99]
            if modifier == 'duplicate':
                item['timestamp'] *= 2
                item['indicators']['quote'][0] = {k: [100, 101] for k in ('open', 'high', 'low', 'close')}
            with tempfile.TemporaryDirectory() as directory, patch('stock_data._fetch', return_value=payload):
                with self.assertRaises(DownloadError): download_stock('TCS', 7, Path(directory), now=NOW)
                self.assertFalse((Path(directory) / 'TCS.json').exists())
        for days in (0, 29, True, 1.5):
            with self.assertRaises(ValueError): download_stock('TCS', days)

    def test_csv_rejects_mixed_and_mislabeled_instruments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mixed.csv'
            path.write_text('Instrument,Date,Time,Open,High,Low,Close\nTCS,01-01-2024,09:15:00,100,100,100,100\nINFY,01-01-2024,09:16:00,100,100,100,100\n')
            with self.assertRaises(ValueError): list(read_minutes(path))
            with self.assertRaises(ValueError): list(read_minutes(path, expected_instrument='NIFTY50'))

    def test_api_symbols_caches_download_failures_and_default_policy(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'stocks'
            for symbol in ('TCS', 'INFY'):
                with patch('stock_data._fetch', return_value=response(symbol=symbol, prices=[100 if symbol == 'TCS' else 200])):
                    download_stock(symbol, 7, cache, now=NOW)
            client = TestClient(create_app(Path(directory) / 'missing.csv', stock_dir=cache, default_symbol='TCS', default_provider='yahoo'))
            for symbol, price in [('TCS', 100), ('INFY', 200)]:
                r = client.get('/bars', params={'symbol': symbol, 'percentage': 2}).json()
                self.assertEqual(r['metadata']['symbol'], symbol)
                self.assertEqual(r['metadata']['pending_move']['last_price'], price)
                self.assertEqual(r['bars'], [])
                self.assertEqual(client.get('/history', params={'symbol': symbol}).json()['bars'][0]['close'], price)
            self.assertEqual(client.get('/bars?symbol=RELIANCE').status_code, 503)
            self.assertEqual(client.get('/bars?symbol=../TCS').status_code, 422)
            self.assertEqual(client.get('/bars?session_mode=wrong').status_code, 422)
            self.assertEqual(client.post('/download?symbol=TCS&days=29').status_code, 422)
            with patch('stock_data._fetch', side_effect=DownloadError('rate limited')):
                self.assertEqual(client.post('/download?symbol=TCS').status_code, 502)
            self.assertEqual(client.get('/bars?symbol=TCS').status_code, 200)
            self.assertIn('data-default-symbol="TCS"', client.get('/chart').text)


if __name__ == '__main__': unittest.main()
