"""Streaming daily Wilder ATR, published only to the following session.

The converter validates candles before passing them here. Only fully observed
regular sessions (09:15–15:29 IST, 375 minutes) contribute daily true ranges.
The first TR is H-L; subsequent TRs reference the last eligible session close.
"""
from datetime import time
from decimal import Decimal


def parse_atr_period(value) -> int:
    if isinstance(value, bool) or not str(value).strip().isdecimal():
        raise ValueError('ATR period must be a positive whole number such as 7, 10 or 14')
    period = int(str(value).strip())
    if period < 1:
        raise ValueError('ATR period must be a positive whole number')
    return period


class DailyATR:
    def __init__(self, period):
        self.period = parse_atr_period(period)
        self.value = self.as_of = self.previous_close = self.active = None
        self.seed_sum = Decimal(0)
        self.used_sessions = 0
        self.skipped_sessions = []

    def _finish(self):
        day = self.active
        complete = (day['first'].time() == time(9, 15)
                    and day['last'].time() == time(15, 29) and day['count'] == 375)
        if not complete:
            self.skipped_sessions.append(str(day['first'].date()))
            return
        tr = day['high'] - day['low']
        if self.previous_close is not None:
            tr = max(tr, abs(day['high'] - self.previous_close), abs(day['low'] - self.previous_close))
        self.previous_close = day['close']
        self.used_sessions += 1
        if self.used_sessions <= self.period:
            self.seed_sum += tr
        if self.used_sessions == self.period:
            self.value = self.seed_sum / self.period
        elif self.used_sessions > self.period:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        if self.value is not None:
            self.as_of = str(day['first'].date())

    def observe(self, candle):
        if self.active is None or candle.timestamp.date() != self.active['first'].date():
            if self.active is not None:
                self._finish()
            self.active = {'first': candle.timestamp, 'high': candle.high, 'low': candle.low, 'count': 0}
        self.active['last'], self.active['close'] = candle.timestamp, candle.close
        self.active['high'] = max(self.active['high'], candle.high)
        self.active['low'] = min(self.active['low'], candle.low)
        self.active['count'] += 1
        # Do not publish this day's range, even on its last minute. The next
        # session can use it; the current session keeps the size it opened with.
