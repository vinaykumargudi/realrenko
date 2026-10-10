// Built-in indicators, not a Pine Script interpreter. No price data is mutated.
function validatePeriod(period) {
  if (!Number.isInteger(period) || period < 1 || period > 1000) throw new Error('Indicator length must be an integer from 1 to 1000.');
}

export function sma(bars, period = 50) {
  validatePeriod(period);
  let sum = 0;
  return bars.map((bar, i) => {
    sum += bar.close;
    if (i >= period) sum -= bars[i - period].close;
    return i >= period - 1 ? sum / period : null;
  });
}

export function ema(bars, period = 20) {
  validatePeriod(period);
  const alpha = 2 / (period + 1);
  let value = null;
  // First-close seed, matching the standard recursive EMA convention.
  return bars.map(bar => { value = value === null ? bar.close : alpha * bar.close + (1 - alpha) * value; return value; });
}

export function atr(bars, period = 10) {
  validatePeriod(period);
  let total = 0, previous = null;
  return bars.map((bar, i) => {
    const tr = i === 0 ? bar.high - bar.low : Math.max(bar.high - bar.low,
      Math.abs(bar.high - bars[i - 1].close), Math.abs(bar.low - bars[i - 1].close));
    if (i < period) total += tr;
    if (i < period - 1) return null;
    previous = previous === null ? total / period : (previous * (period - 1) + tr) / period;
    return previous;
  });
}

// Formula: tradingview.com/support/solutions/43000634738-supertrend/
// Direction +1 is bullish here, -1 bearish (our API naming, not Pine's sign).
export function supertrend(bars, period = 10, multiplier = 3) {
  if (!Number.isFinite(multiplier) || multiplier <= 0) throw new Error('Supertrend multiplier must be positive.');
  const volatility = atr(bars, period);
  let upper = null, lower = null, direction = -1;
  return bars.map((bar, i) => {
    if (volatility[i] === null) return null;
    const middle = (bar.high + bar.low) / 2;
    const basicUpper = middle + multiplier * volatility[i];
    const basicLower = middle - multiplier * volatility[i];
    const prevClose = i ? bars[i - 1].close : bar.close;
    const first = upper === null;
    upper = first || basicUpper < upper || prevClose > upper ? basicUpper : upper;
    lower = first || basicLower > lower || prevClose < lower ? basicLower : lower;
    if (first) direction = -1;
    else if (direction === -1) direction = bar.close > upper ? 1 : -1;
    else direction = bar.close < lower ? -1 : 1;
    return { value: direction === 1 ? lower : upper, direction, atr: volatility[i] };
  });
}
