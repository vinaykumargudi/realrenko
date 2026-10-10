import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from atr_sizing import parse_atr_period
from daily_history import daily_history
from nifty_bars import Candle, IST, convert_minutes, create_app, filter_result, make_chart
from test_nifty_bars import c

# These established fixtures exercise the retained daily-remainder policy.
# Default threshold-mode behavior is covered separately in test_threshold_mode.py.
from functools import partial as bind_policy
convert_minutes = bind_policy(convert_minutes, session_mode="daily")


def day(day_number, o, h, l, close):
    """375 valid minutes with the day's H/L observed in the first minute."""
    start = datetime(2024, 1, 1, 9, 15, tzinfo=IST) + timedelta(days=day_number - 1)
    o, h, l, close = map(lambda x: D(str(x)), (o, h, l, close))
    rows, previous = [], o
    for i in range(375):
        current = o + (close - o) * (i + 1) / 375
        rows.append(Candle(start + timedelta(minutes=i), previous,
                           h if i == 0 else max(previous, current),
                           l if i == 0 else min(previous, current), current))
        previous = current
    return rows


def write_csv(path, rows):
    path.write_text('Instrument,Date,Time,Open,High,Low,Close\n' + ''.join(
        f'Nifty50,{r.timestamp:%d-%m-%Y},{r.timestamp:%H:%M:%S},{r.open},{r.high},{r.low},{r.close}\n'
        for r in rows))


class ATRTests(unittest.TestCase):
    def test_wilder_seed_gap_true_range_and_session_lag(self):
        rows = day(1,100,104,98,102) + day(2,110,114,109,112) + day(3,112,130,110,130) + day(4,140,151,138,150)
        result = convert_minutes(rows, sizing='atr', atr_period=2)
        bars = result['bars']
        # TRs 6, 12 => seed 9; third TR 20 => next session ATR 14.5.
        for b in bars:
            if b['end'][:10] in ('2024-01-01','2024-01-02'):
                self.assertEqual(b['atr_status'],'warmup')
                self.assertIsNone(b['brick_size_points'])
                self.assertNotEqual(b['kind'],'move')
            elif b['end'][:10] == '2024-01-03':
                self.assertEqual(b['brick_size_points'],9)
                self.assertEqual(b['atr_as_of'],'2024-01-02')
            else:
                self.assertEqual(b['brick_size_points'],14.5)
                self.assertEqual(b['atr_as_of'],'2024-01-03')
        moves = [b for b in bars if b['kind']=='move']
        self.assertEqual([(b['open'],b['close']) for b in moves],[(112,121),(121,130)])
        self.assertNotEqual(moves[0]['move_pct'],moves[1]['move_pct'])
        self.assertEqual(sum(b['source_minutes'] for b in bars),len(rows))
        self.assertTrue(all(a['close']==b['open'] for a,b in zip(bars,bars[1:])))
        self.assertEqual(result['metadata']['atr_eligible_prior_sessions'],3)

    def test_presets_and_custom_periods_need_full_daily_warmup(self):
        for period in (1,7,10,14,21):
            with self.subTest(period=period):
                rows = [r for n in range(1,period+1) for r in day(n,100,101,99,100)]
                rows += day(period+1,100,105,100,105)
                result = convert_minutes(rows, sizing='atr', atr_period=period)
                moves = [b for b in result['bars'] if b['kind']=='move']
                self.assertEqual([(b['open'],b['close']) for b in moves],[(100,102),(102,104)])
                self.assertTrue(all(b['brick_size_points']==2 for b in moves))
                self.assertEqual(result['bars'][-1]['close'],105)
                self.assertEqual(len([b for b in result['bars'] if b['atr_status']=='warmup']),period)

    def test_future_prices_and_date_filters_do_not_resize_old_atr_bricks(self):
        rows = day(1,100,104,98,102) + day(2,110,114,109,112) + day(3,112,130,110,130)
        rows += day(4,140,151,138,150)
        before = convert_minutes(rows[:3*375+20], sizing='atr', atr_period=2)
        after = convert_minutes(rows + day(5,150,1000,1,200), sizing='atr', atr_period=2)
        # Only the last unfinished remainder is expected to change on more input.
        stable = [b for b in before['bars'] if b['finalized']]
        self.assertEqual(stable, after['bars'][:len(stable)])
        window = filter_result(after,date(2024,1,3),date(2024,1,3))
        self.assertEqual(window['bars'],[b for b in after['bars'] if b['end'].startswith('2024-01-03')])

    def test_incomplete_daily_ranges_are_excluded_and_zero_atr_never_loops(self):
        rows = day(1,100,101,99,100) + [c('2024-01-02T09:15',100,1000,1,100)]
        rows += day(3,100,102,100,102) + [c('2024-01-04T09:15',102)]
        result = convert_minutes(rows,sizing='atr',atr_period=1)
        self.assertEqual(result['metadata']['atr_skipped_prior_sessions'],['2024-01-02'])
        self.assertEqual(result['metadata']['last_atr_points'],2)
        self.assertTrue(all(b['brick_size_points']==2 for b in result['bars'] if b['end'].startswith('2024-01-03')))
        zero = convert_minutes(day(1,100,100,100,100)+day(2,100,104,100,104),sizing='atr',atr_period=1)
        self.assertFalse(any(b['kind']=='move' for b in zero['bars']))
        self.assertEqual(zero['bars'][-1]['atr_status'],'zero_atr')
        self.assertEqual(zero['bars'][-1]['close'],104)

    def test_api_cli_period_validation_and_atr_plot(self):
        from fastapi.testclient import TestClient
        for invalid in (0,-1,1.5,'1.5','nan',True):
            with self.assertRaises(ValueError): parse_atr_period(invalid)
        with self.assertRaises(ValueError): convert_minutes([c('2024-01-01T09:15',100)],sizing='unknown')
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp)/'source.csv', Path(temp)/'bars.json'
            write_csv(source, day(1,100,101,99,100)+day(2,100,105,100,105))
            client = TestClient(create_app(source))
            response = client.get('/bars?session_mode=daily&sizing=atr&atr_period=1')
            self.assertEqual(response.status_code,200,response.text)
            result = response.json()
            self.assertEqual(result['metadata']['mode'],'prior_daily_atr_close')
            self.assertEqual(result['metadata']['atr_period'],1)
            fig=make_chart(result)
            self.assertIn('ATR(1)',fig.layout.title.text)
            self.assertIn('ATH*',[trace.name for trace in fig.data])
            for query in ('atr_period=0','atr_period=-1','atr_period=1.5','atr_period=abc','sizing=bad'):
                self.assertEqual(client.get('/bars?session_mode=daily&sizing=atr&'+query).status_code,422)
            subprocess.run([sys.executable,str(Path(__file__).with_name('nifty_bars.py')), '--session-mode', 'daily','--csv',str(source),
                            '--sizing','atr','--atr-period','1','--output',str(output)],check=True,capture_output=True)
            self.assertEqual(json.loads(output.read_text())['bars'],result['bars'])
            # Cache keys must not confuse distinct modes/periods.
            warmup = client.get('/bars?session_mode=daily&sizing=atr&atr_period=7').json()
            self.assertFalse(any(b['kind']=='move' for b in warmup['bars']))
            self.assertEqual(client.get('/bars?session_mode=daily&percentage=3').json()['metadata']['sizing'],'percentage')


class RecordHighTests(unittest.TestCase):
    def test_source_wick_highs_strict_ties_and_first_record_baseline(self):
        rows = [c('2024-01-01T09:15',100,105,99,100), c('2024-01-01T09:16',100,104,99,100),
                c('2024-01-01T09:17',100,105,99,100), c('2024-01-01T09:18',100,106,99,100)]
        result = convert_minutes(rows,50)
        event = result['bars'][0]['ath_breakout']
        self.assertEqual((event['previous_high'],event['high'],event['count']),(105,106,1))
        self.assertEqual(event['known_at'],'2024-01-01T09:19:00+05:30')
        self.assertEqual(result['bars'][0]['high'],100) # Never attach raw wicks to bricks.
        self.assertEqual(result['metadata']['ath_reference'],'available_source_history')
        self.assertIsNone(convert_minutes(rows[:1],50)['bars'][0]['ath_breakout'])

    def test_gap_breakout_is_marked_at_open_and_multiple_bricks_do_not_duplicate_events(self):
        rows = [c('2024-01-01T15:29',100), c('2024-01-02T09:15',103,110,103,110)]
        events = []
        for kwargs in ({'percentage':1},{'percentage':3},{'sizing':'atr','atr_period':7}):
            result = convert_minutes(rows,**kwargs)
            gap = next(b for b in result['bars'] if b['kind']=='session_gap')
            self.assertEqual(gap['ath_breakout']['high'],103)
            self.assertEqual(gap['ath_breakout']['known_at'],gap['end'])
            flagged = [b['ath_breakout'] for b in result['bars'] if b['ath_breakout']]
            self.assertEqual(len(flagged),2)
            self.assertEqual(flagged[-1]['high'],110)
            self.assertEqual(flagged[-1]['previous_high'],103)
            self.assertEqual(flagged[-1]['count'],1)
            events.append(flagged)
        self.assertEqual(events[0],events[1]);self.assertEqual(events[1],events[2])

    def test_prior_history_and_future_extremes_do_not_create_false_breakouts(self):
        rows = [c('2024-01-01T15:29',100,200,100,100),c('2024-01-02T09:15',100,150,100,110)]
        result = convert_minutes(rows,3)
        window = filter_result(result,date(2024,1,2),date(2024,1,2))
        self.assertTrue(window['bars'])
        self.assertTrue(all(b['ath_breakout'] is None for b in window['bars']))
        before = convert_minutes(rows+[c('2024-01-02T09:16',110,210,110,120)],3)
        after = convert_minutes(rows+[c('2024-01-02T09:16',110,210,110,120),c('2024-01-02T09:17',120,1000,120,125)],3)
        self.assertEqual(before['bars'][:-1],after['bars'][:len(before['bars'])-1])

    def test_daily_history_keeps_source_breakout_details_before_filtering(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source.csv'
            write_csv(source,[c('2024-01-01T15:29',100,105,100,100),c('2024-01-02T09:15',100,104,100,103),
                              c('2024-01-02T09:16',103,106,103,104)])
            result=daily_history(source)
            self.assertIsNone(result['bars'][0]['ath_breakout'])
            self.assertEqual(result['bars'][1]['ath_breakout']['previous_high'],105)
            response=TestClient(create_app(source)).get('/history?start=2024-01-02').json()
            self.assertEqual(response['bars'][0]['ath_breakout'],result['bars'][1]['ath_breakout'])


if __name__ == '__main__':
    unittest.main()
