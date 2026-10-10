// Exercise the actual vendored engine/custom-series contract in a DOM emulator.
// Canvas calls are recorded; this is not a physical-browser/pixel test.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Window } from 'happy-dom';
import { RenkoSeries } from '../static/renko-series.mjs';

test('vendored chart engine renders and replaces exact percentage Renko bodies', async () => {
  const w = new Window({url:'http://localhost', settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
  const computed = w.getComputedStyle.bind(w);
  // Browsers serialize computed colors as rgb; Happy DOM retains hex strings.
  w.getComputedStyle = el => new Proxy(computed(el), {get(t, k) {
    const v = Reflect.get(t, k);
    if (k === 'color' && typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v))
      return 'rgb('+[1,3,5].map(i => parseInt(v.slice(i,i+2),16)).join(', ')+')';
    return typeof v === 'function' ? v.bind(t) : v;
  }});
  const rectangles = [];
  w.HTMLCanvasElement.prototype.getContext = function() {
    return new Proxy({canvas:this,
      measureText:t => ({width:String(t).length*7,actualBoundingBoxAscent:10,actualBoundingBoxDescent:2}),
      fillRect(...args) { if (this.fillStyle === '#26c59c') rectangles.push(args); },
      getLineDash:() => [], createLinearGradient:() => ({addColorStop(){}}),
    }, {get:(o,k) => k in o ? o[k] : () => {}, set:(o,k,v) => (o[k]=v,true)});
  };
  let chart;
  try {
    w.document.body.innerHTML = '<div id="chart"></div>';
    w.eval(await readFile(new URL('../static/vendor/lightweight-charts-5.2.1.js',import.meta.url),'utf8'));
    const L = w.LightweightCharts;
    chart = L.createChart(w.document.getElementById('chart'), {width:800,height:400,layout:{attributionLogo:false}});
    const series = chart.addCustomSeries(new RenkoSeries(L.customSeriesDefaultOptions));
    const point = (time,open,close) => ({time,open,close,high:close,low:open,color:'#26c59c'});
    for (const points of [[point(1,100,102),point(2,102,104.04)],
                          [point(1,100,101),point(2,101,102.01),point(3,102.01,103.0301)]]) {
      rectangles.length = 0;
      series.setData(points);
      chart.timeScale().fitContent();
      await new Promise(resolve => setTimeout(resolve,100));
      assert.deepEqual(series.data().map(b => b.close),points.map(b => b.close));
      assert.ok(rectangles.length >= points.length);
      assert.ok(rectangles.every(r => r.every(Number.isFinite) && r[2] > 0 && r[3] > 0));
    }
  } finally {
    chart?.remove();
    await w.happyDOM.close();
  }
});
