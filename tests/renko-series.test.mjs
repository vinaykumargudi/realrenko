import test from 'node:test';
import assert from 'node:assert/strict';
import { RenkoSeries } from '../static/renko-series.mjs';
import { chartPoints, selectBars } from '../static/chart-data.mjs';

test('Renko renders touching rectangles at exact prices, including remainders, gaps and flat days', () => {
  for (const direction of [1, -1]) {
    const prices = direction === 1 ? [100,103,104,105.04,105.04] : [100,97,96,95.04,95.04];
    const bars = prices.slice(1).map((close,i) => ({ open: prices[i], close,
      high: Math.max(prices[i],close), low: Math.min(prices[i],close),
      kind: ['move','partial','session_gap','partial'][i], end: `2024-01-0${i+1}` }));
    const points = chartPoints(selectBars(bars), {green:'#00ff00',red:'#ff0000',blue:'#0000ff',purple:'#ff00ff',muted:'#888888'});
    assert.equal(points.length,4);
    assert.equal(points[1].color,direction===1?'#00ff00':'#ff0000');
    for (const ratio of [1, 1.25, 2]) {
      const draws = [];
      const ctx = { save(){},restore(){},fillRect(x,y,w,h){draws.push({x,y,w,h,alpha:this.globalAlpha});} };
      const series = new RenkoSeries({color:'#00ff00'});
      assert.equal(series.defaultOptions().color,'#00ff00');
      assert.deepEqual(series.priceValueBuilder(points[1]), [bars[1].high,bars[1].low,bars[1].close]);
      assert.equal(series.isWhitespace({time:1}),true);
      assert.equal(series.isWhitespace(points[3]),false);
      series.update({ bars: points.map((originalData,i) => ({x:10+i*10,originalData})),
        barSpacing:10,visibleRange:{from:0,to:4} });
      series.draw({useBitmapCoordinateSpace:f=>f({context:ctx,horizontalPixelRatio:ratio,verticalPixelRatio:ratio})}, price => 120-price);
      // Each remainder has one fill and four edge strips. No wick or gap filler.
      assert.equal(draws.length,12);
      const bodies = [draws[0],draws[1],draws[6],draws[7]];
      bodies.forEach((r,i) => {
        assert.equal(r.y,Math.round((120-bars[i].high)*ratio));
        assert.equal(r.h,Math.max(1,Math.round((120-bars[i].low)*ratio)-Math.round((120-bars[i].high)*ratio)));
        if(i) assert.equal(bodies[i-1].x+bodies[i-1].w,r.x);
      });
      assert.equal(bodies[1].alpha,.55);
      assert.equal(bodies[3].h,1);
      series.destroy();
      series.draw({useBitmapCoordinateSpace:()=>assert.fail('destroyed view draws nothing')}, p=>p);
    }
  }
});
