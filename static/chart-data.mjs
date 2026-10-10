// Unique ordinal coordinates preserve every brick, including several at one time.
// All labels/tooltips map back to the real IST timestamp; these keys are not dates.
export const AXIS_BASE = 1577836800;
export const AXIS_STEP = 60;

export function selectBars(bars, { start = '', end = '', gaps = true, partials = false } = {}) {
  if (start && end && start > end) throw new Error('The start date must be before the end date.');
  return bars.filter(b => (!start || b.end.slice(0, 10) >= start)
    && (!end || b.end.slice(0, 10) <= end)
    && (partials || b.kind !== 'partial') && (gaps || !b.synthetic_bridge));
}

export function chartPoints(bars, colors) {
  return bars.map((b, i) => {
    if (![b.open, b.high, b.low, b.close].every(Number.isFinite)) throw new Error('The dataset contains an invalid price.');
    const color = b.kind === 'data_gap' ? colors.amber : b.kind === 'partial' ? colors.muted
      : b.kind === 'session_gap' ? (b.close >= b.open ? colors.blue : colors.purple)
      : (b.close >= b.open ? colors.green : colors.red);
    return { time: AXIS_BASE + i * AXIS_STEP, open: b.open, high: b.high, low: b.low,
      close: b.close, color, borderColor: color, wickColor: color };
  });
}

export function indexForTime(time) {
  return typeof time === 'number' ? Math.round((time - AXIS_BASE) / AXIS_STEP) : -1;
}

export function earlierMonth(date, months) {
  const d = new Date(`${date}T00:00:00Z`), day = d.getUTCDate();
  d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - months);
  const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return d.toISOString().slice(0, 10);
}

export function priceChange(bars) {
  return bars.length ? (bars.at(-1).close / bars[0].open - 1) * 100 : null;
}
