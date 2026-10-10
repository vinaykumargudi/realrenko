"""Independent daily ATR and source-high audit against all supplied minutes."""
import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import time
from decimal import Decimal as D
from pathlib import Path

from nifty_bars import MINUTE, convert_minutes, read_minutes


def reference_history(path):
    days, events = {}, []
    record = previous = first = None
    minutes = 0
    for c in read_minutes(path):
        first = first or c
        minutes += 1
        d = days.setdefault(str(c.timestamp.date()), {'first': c.timestamp, 'high': c.high, 'low': c.low, 'count': 0})
        d.update(high=max(d['high'],c.high), low=min(d['low'],c.low), close=c.close, last=c.timestamp, count=d['count']+1)
        if record is None:
            record = c.high
        else:
            # An observed opening jump is available before this minute's high.
            prices = [(c.high,c.timestamp+MINUTE)]
            if previous is not None and (previous.timestamp.date()!=c.timestamp.date() or c.timestamp-previous.timestamp!=MINUTE):
                prices.insert(0,(c.open,c.timestamp))
            for price, known in prices:
                if price > record:
                    events.append({'previous_high':float(record), 'high':float(price),
                                   'at':c.timestamp.isoformat(),'known_at':known.isoformat()})
                    record = price
        previous = c
    return days, events, minutes, float(first.open), float(previous.close), float(record)


def daily_reference(days, period):
    history, previous_close, value, as_of = [], None, None, None
    result = {}
    for name, d in days.items():
        result[name] = (value,as_of) # Only earlier sessions can size today's bricks.
        if not (d['first'].time()==time(9,15) and d['last'].time()==time(15,29) and d['count']==375):
            continue
        tr=d['high']-d['low']
        if previous_close is not None:
            tr=max(tr,abs(d['high']-previous_close),abs(d['low']-previous_close))
        previous_close=d['close']; history.append(tr)
        if len(history)==period:
            value=sum(history,D(0))/period
        elif len(history)>period:
            value+=(tr-value)/period # Algebraically equivalent independent recurrence.
        if value is not None:
            as_of=name
    return result


def audit(path):
    days, events, minutes, first_open, last_close, record = reference_history(path)
    reports=[]
    for sizing,period in [('percentage',None),('atr',7),('atr',10),('atr',14)]:
        result=convert_minutes(read_minutes(path),1,sizing=sizing,atr_period=period or 14)
        bars=result['bars']; reference=daily_reference(days,period) if period else {}
        consumed_events=0; largest_size_error=0.0
        assert bars[0]['open']==first_open and bars[-1]['close']==last_close
        assert sum(b['source_minutes'] for b in bars)==minutes
        assert set(b['end'][:10] for b in bars if not b['synthetic_bridge'])==set(days)
        for i,b in enumerate(bars):
            if i: assert bars[i-1]['close']==b['open']
            if period:
                expected,as_of=reference[b['end'][:10]]
                assert b['atr_as_of']==as_of
                if expected is None or expected==0:
                    assert b['brick_size_points'] is None and b['kind']!='move'
                else:
                    assert as_of < b['end'][:10]
                    assert math.isclose(b['brick_size_points'],float(expected),rel_tol=1e-12)
                    if b['kind']=='move':
                        error=abs(abs(b['close']-b['open'])-float(expected))
                        largest_size_error=max(largest_size_error,error)
                        assert error<1e-8
                    elif b['kind']=='partial':
                        assert abs(b['close']-b['open'])<float(expected)+1e-8
            elif b['kind']=='move':
                assert abs(abs(b['close']/b['open']-1)*100-1)<1e-9
            event=b['ath_breakout']
            if event:
                batch=events[consumed_events:consumed_events+event['count']]
                assert batch
                assert event['previous_high']==batch[0]['previous_high']
                assert event['high']==batch[-1]['high']
                assert event['first_at']==batch[0]['at'] and event['last_at']==batch[-1]['at']
                assert event['known_at']==batch[-1]['known_at']
                assert event['known_at']<=b['known_at']
                consumed_events+=event['count']
        assert consumed_events==len(events)==result['metadata']['ath_breakout_observations']
        assert result['metadata']['ath_latest_high']==record
        report={'sizing':sizing,'atr_period':period,'bar_counts':dict(Counter(b['kind'] for b in bars)),
                'source_minutes_accounted_for':minutes,'sessions_represented':len(days),
                'connected_adjacent_bricks':len(bars)-1,'source_high_events_verified':consumed_events,
                'marked_bricks':sum(bool(b['ath_breakout']) for b in bars),
                'maximum_atr_brick_error_points':largest_size_error if period else None,
                'all_assertions_passed':True}
        reports.append(report)
        print(json.dumps(report),flush=True)
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return {'source_sha256':digest.hexdigest(),'first_date':next(iter(days)),'last_date':next(reversed(days)),
            'source_record_high':record,'atr_policy':'prior fully observed 375-minute daily sessions; Wilder RMA with SMA seed',
            'high_policy':'strictly greater source high, including gap opens; first observation is the baseline',
            'checks':reports}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('validation_atr_and_highs.json'))
    args=parser.parse_args()
    args.output.write_text(json.dumps(audit(args.csv),indent=2,allow_nan=False)+'\n')
