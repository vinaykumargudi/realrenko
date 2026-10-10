"""Strict new-high events from observed source prices, never synthetic bricks."""


def merge_breakouts(first, latest):
    if latest is None:
        return first
    if first is None:
        return latest
    return {**first, 'high': latest['high'], 'last_at': latest['last_at'],
            'known_at': latest['known_at'], 'count': first['count'] + latest['count']}


class RecordHighs:
    def __init__(self):
        self.high = None
        self.count = 0

    def observe(self, price, timestamp, known_at):
        if self.high is None:
            self.high = price  # Establish a baseline; do not invent a breakout.
            return None
        if price <= self.high:
            return None
        previous, self.high = self.high, price
        self.count += 1
        return {'previous_high': float(previous), 'high': float(price),
                'first_at': timestamp.isoformat(), 'last_at': timestamp.isoformat(),
                'known_at': known_at.isoformat(), 'count': 1}

    def metadata(self, first_timestamp):
        return {'ath_reference': 'available_source_history',
                'ath_history_start': first_timestamp.isoformat(),
                'ath_latest_high': float(self.high) if self.high is not None else None,
                'ath_breakout_observations': self.count}
