import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from historical_data import download_history_stock, import_history_csv, provider_directory
from nifty_bars import create_app, read_minutes
from stock_data import DownloadError, cached_stock


HEADER = 'date,open,high,low,close,volume\n'


class HistoryTests(unittest.TestCase):
    def fixture(self, directory, text):
        path = Path(directory) / 'source.csv'
        path.write_text(HEADER+text)
        return path

    def test_minute_archive_zero_placeholders_normalization_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory,
                '2015-02-02 09:15:01,100,101,99,100,1\n'
                '2015-02-02 09:16:00,100,102,100,102,2\n'
                '2015-02-02 09:17:00,0,0,0,0,0\n'
                '2015-02-03 09:15:00,104,104,104,104,1\n')
            normalized, m = import_history_csv(path, 'TCS', Path(directory))
            self.assertEqual(m['downloaded_minutes'], 3)
            self.assertEqual(m['excluded_zero_ohlcv_rows'], 1)
            self.assertEqual(m['timestamp_labels_floored_to_minute'], 1)
            self.assertFalse(m['history_complete_since_listing'])
            self.assertEqual(len(list(read_minutes(normalized))), 3)
            self.assertEqual(cached_stock('TCS', provider_directory('kaggle', Path(directory)))[0], normalized)
            self.assertFalse((Path(directory)/'TCS.json').exists())  # recent cache is separate

    def test_coarse_daily_or_five_minute_data_is_rejected(self):
        for second in ('2015-02-02 09:20:00', '2015-02-03 09:15:00'):
            with tempfile.TemporaryDirectory() as directory:
                path = self.fixture(directory, '2015-02-02 09:15:00,100,100,100,100,1\n'
                                    +second+',100,100,100,100,1\n')
                with self.assertRaisesRegex(DownloadError, 'cadence'):
                    import_history_csv(path, 'TCS', Path(directory))
                self.assertFalse((Path(directory)/'kaggle/TCS.json').exists())

    def test_invalid_prices_do_not_replace_a_good_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, '2015-02-02 09:15:00,100,101,99,100,1\n'
                                '2015-02-02 09:16:00,100,101,99,100,1\n')
            old, _ = import_history_csv(path, 'TCS', Path(directory))
            path.write_text(path.read_text().replace(',100,101,99,100,1', ',0,101,99,100,1', 1))
            with self.assertRaises(ValueError): import_history_csv(path, 'TCS', Path(directory))
            self.assertEqual(cached_stock('TCS', provider_directory('kaggle', Path(directory)))[0], old)

    def test_downloader_pins_version_and_downloads_only_symbol(self):
        fixture = (HEADER+'2015-02-02 09:15:00,100,101,99,100,1\n'
                   '2015-02-02 09:16:00,100,101,99,100,1\n').encode()
        urls = []
        def request(url):
            urls.append(url)
            if '/view/' in url: return io.BytesIO(json.dumps({'currentVersionNumber':15}).encode())
            return io.BytesIO(fixture)
        with tempfile.TemporaryDirectory() as directory, patch('historical_data._request', side_effect=request):
            _, meta = download_history_stock('TCS', Path(directory))
            self.assertEqual(meta['dataset_version'], 15)
            self.assertIn('/TCS_minute.csv?datasetVersionNumber=15', urls[-1])
            self.assertEqual(meta['download_bytes'], len(fixture))

    def test_unavailable_symbol_does_not_fall_back_to_recent_prices(self):
        with tempfile.TemporaryDirectory() as directory, patch('historical_data._dataset_metadata', return_value={'currentVersionNumber':15}), patch('historical_data._request', side_effect=HTTPError('url',404,'missing',{},None)):
            with self.assertRaisesRegex(DownloadError, 'no file'):
                download_history_stock('NOTREAL', Path(directory))
            self.assertFalse((Path(directory)/'kaggle/NOTREAL.json').exists())

    def test_api_provider_isolation_percentages_and_server_chart_defaults(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, '2015-02-02 09:15:00,100,104,100,104,1\n'
                                '2015-02-02 09:16:00,104,104,104,104,1\n')
            import_history_csv(path, 'TCS', Path(directory))
            client = TestClient(create_app(stock_dir=Path(directory), default_symbol='TCS',
                chart_defaults={'percentage':'2','sizing':'atr','atr_period':7,'session_mode':'threshold'}))
            one = client.get('/bars?symbol=TCS&percentage=1').json()
            two = client.get('/bars?symbol=TCS&percentage=2').json()
            self.assertEqual(len(one['bars']), 3)
            self.assertEqual(len(two['bars']), 1)
            self.assertEqual(one['metadata']['provider'], 'kaggle')
            self.assertEqual(client.get('/bars?symbol=TCS&provider=yahoo').status_code, 503)
            self.assertEqual(client.get('/bars?symbol=TCS&provider=../').status_code, 422)
            redirect = client.get('/', follow_redirects=False).headers['location']
            self.assertIn('percentage=2', redirect)
            self.assertIn('atr_period=7', redirect)
            shell = client.get('/chart').text
            self.assertIn('data-default-provider="kaggle"', shell)
            self.assertIn('&quot;atr_period&quot;: 7', shell)


if __name__ == '__main__': unittest.main()
