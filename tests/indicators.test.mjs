import test from 'node:test';
import assert from 'node:assert/strict';
import { sma, ema, atr, supertrend } from '../static/indicators.mjs';
import { chartPoints, indexForTime, selectBars, earlierMonth, priceChange } from '../static/chart-data.mjs';
const closeBars = values => values.map(close => ({ open: close, high: close + 1, low: close - 1, close }));
const almost = (a, b) => assert.ok(Math.abs(a - b) < 1e-10, `${a} vs ${b}`);

test('SMA warm-up and EMA first-close recursion match hand calculations', () => {
  const bars = closeBars([1,2,3,4]);
  assert.deepEqual(sma(bars, 2), [null, 1.5, 2.5, 3.5]);
  const result = ema(bars, 2);
  [1,5/3,23/9,95/27].forEach((v,i) => almost(result[i],v));
});
test('ATR uses true gaps and Wilder smoothing rather than an SMA', () => {
  const bars = [{high:11,low:9,close:10},{high:13,low:10,close:12},
    {high:14,low:11,close:13},{high:20,low:19,close:19.5}];
  assert.deepEqual(atr(bars, 2), [null,2.5,2.75,4.875]);
});
test('Supertrend switches direction with trailing bands: hand-calculated fixture', () => {
  const bars = [{high:101,low:99,close:100},{high:104,low:100,close:104},
    {high:105,low:103,close:104},{high:104,low:98,close:99}];
  const output = supertrend(bars, 1, 1);
  assert.deepEqual(output.map(x => x.value), [102,98,102,106]);
  assert.deepEqual(output.map(x => x.direction), [-1,1,1,-1]);
});
test('Indicators use no future data and leave source prices unchanged', () => {
  const bars = closeBars([100,103,102,108,101,97,104,109,100,98,110,107]);
  const snapshot = JSON.stringify(bars), all = supertrend(bars,3,2);
  for(let i=1;i<=bars.length;i++) assert.deepEqual(supertrend(bars.slice(0,i),3,2), all.slice(0,i));
  assert.equal(JSON.stringify(bars),snapshot);
  assert.throws(() => atr(bars, 0)); assert.throws(() => sma(bars, 1.5));
  assert.throws(() => supertrend(bars, 3, -1));
});
test('Distinct bricks at the same source minute survive with exact OHLC and gap sizes', () => {
  const end='2020-01-01T09:16:00+05:30';
  const bars=[{kind:'move',open:100,high:101,low:100,close:101,end},
    {kind:'move',open:101,high:102.01,low:101,close:102.01,end},
    {kind:'session_gap',open:102.01,high:105.0703,low:102.01,close:105.0703,end}];
  const points=chartPoints(bars,{green:'g',red:'r',blue:'b',purple:'p',amber:'a',muted:'m'});
  assert.equal(new Set(points.map(p=>p.time)).size,3);
  points.forEach((p,i) => assert.equal(indexForTime(p.time),i));
  almost((points[0].close/points[0].open-1)*100,1);
  almost((points[1].close/points[1].open-1)*100,1);
  almost((points[2].close/points[2].open-1)*100,3);
});
test('Date filters, visibility and long-history percentage preserve actual price levels', () => {
  const bars=[{end:'2015-01-09',kind:'price',open:8000,close:8100},
    {end:'2020-12-31',kind:'price',open:13000,close:14000},
    {end:'2024-01-01',kind:'price',open:21000,close:22000}];
  const shown=selectBars(bars,{start:'2015-01-01',end:'2020-12-31'});
  assert.equal(shown.length,2); assert.equal(priceChange(shown),75);
  assert.equal(earlierMonth('2024-03-31',1),'2024-02-29');
  assert.throws(()=>selectBars(bars,{start:'2021-01-01',end:'2020-01-01'}));
  assert.equal(selectBars([{end:'2020-01-01',kind:'partial'}]).length,1);
  assert.equal(selectBars([{end:'2020-01-01',kind:'partial'}],{partials:false}).length,0);
});
