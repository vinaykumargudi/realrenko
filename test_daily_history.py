import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from daily_history import daily_history
from nifty_bars import create_app


class DailyHistoryTests(unittest.TestCase):
    def test_daily_ohlc_and_history_api_keep_real_levels(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'prices.csv'
            path.write_text('Instrument,Date,Time,Open,High,Low,Close\n'
                            'Nifty50,09-01-2015,09:15:00,8000,8100,7900,8050\n'
                            'Nifty50,09-01-2015,09:16:00,8050,8200,8000,8150\n'
                            'Nifty50,31-12-2020,09:15:00,13900,14050,13800,14000\n')
            data = daily_history(path)
            first, last = data['bars']
            self.assertEqual((first['open'],first['high'],first['low'],first['close']), (8000,8200,7900,8150))
            self.assertEqual(first['source_minutes'],2)
            self.assertFalse(first['synthetic_ohlc'])
            self.assertFalse(first['complete_normal_session'])
            self.assertEqual(last['close'],14000)
            client=TestClient(create_app(path))
            response=client.get('/history?start=2020-01-01&end=2020-12-31')
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.json()['bars']),1)
            self.assertEqual(response.json()['bars'][0]['close'],14000)
            self.assertEqual(client.get('/history?start=2024-01-01&end=2020-01-01').status_code,422)
            self.assertEqual(client.get('/chart?view=daily-line&start=2015-01-01&end=2020-12-31').status_code,200)
            path.unlink()
            self.assertEqual(client.get('/history').status_code,503)
