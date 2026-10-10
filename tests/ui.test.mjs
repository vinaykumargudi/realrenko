// DOM interaction smoke test. The rendering engine is stubbed: this does not
// replace a real browser screenshot or test canvas pixels / physical gestures.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Window } from 'happy-dom';

const settle = () => new Promise(resolve => setImmediate(resolve));
const bar = (id, kind, open, close, end, extra={}) => ({bar_id:id,kind,open,close,
  high:Math.max(open,close),low:Math.min(open,close),start:end,end,known_at:end,
  move_pct:(close/open-1)*100,source_minutes:1,synthetic_bridge:kind==='session_gap',...extra});
const renko=[bar(0,'move',100,101,'2019-01-01T15:30:00+05:30'),
  bar(1,'session_gap',101,104.03,'2019-01-02T09:15:00+05:30'),
  bar(2,'move',104.03,105.0703,'2020-12-31T15:30:00+05:30'),
  bar(3,'partial',105.0703,105.2,'2020-12-31T15:30:00+05:30')];
renko[1].ath_breakout={previous_high:102,high:104.03,count:1,first_at:renko[1].end,last_at:renko[1].end,known_at:renko[1].end};
const daily=[bar(0,'price',8000,8200,'2015-01-09T15:30:00+05:30'),
  bar(1,'price',8100,7900,'2015-01-12T15:30:00+05:30'),
  bar(2,'price',13900,14000,'2020-12-31T15:30:00+05:30')];
const atrRows = period => {
  const prices=[100,100+period,110+period,110+2*period,111+2*period];
  return renko.map((b,i)=>bar(i,b.kind,prices[i],prices[i+1],b.end,{atr_period:period,atr_value:period,brick_size_points:period,atr_as_of:'2018-12-31',atr_status:'ready'}));
};
const payload = (rows, mode) => ({metadata:{mode,percentage:1,first_minute:'2015-01-09T09:15:00+05:30',last_minute:'2020-12-31T15:29:00+05:30'},bars:rows});

function engine() {
  const chart={series:[],options:{},priceOptions:{},range:null,crosshair:null,click:null};
  const scale={applyOptions:o=>Object.assign(chart.priceOptions,o)};
  const time={getVisibleLogicalRange:()=>chart.range,
    setVisibleLogicalRange:r=>{chart.range=r;chart.rangeCallback?.(r);},
    fitContent:()=>time.setVisibleLogicalRange({from:0,to:Math.max(0,(chart.series[0]?.data.length||1)-1)}),
    subscribeVisibleLogicalRangeChange:f=>{chart.rangeCallback=f;}};
  Object.assign(chart,{priceScale:()=>scale,timeScale:()=>time,
    applyOptions:o=>Object.assign(chart.options,o),
    addSeries:(type,options)=>{
      const series={type,options,data:[],lines:[],setData(d){this.data=d;},
        coordinateToPrice:y=>100+y/10,createPriceLine(line){this.lines.push(line);return line;},
        removePriceLine(line){this.lines=this.lines.filter(x=>x!==line);}};
      chart.series.push(series);return series;
    },addCustomSeries:(view,options)=>{const s=chart.addSeries('renko',options);s.view=view;return s;},removeSeries:s=>{chart.series=chart.series.filter(x=>x!==s);},
    subscribeCrosshairMove:f=>{chart.crosshair=f;},subscribeClick:f=>{chart.click=f;}});
  return {chart,library:{createSeriesMarkers:(series,markers)=>{const plugin={items:markers,detached:false,setMarkers(items){this.items=items;},detach(){this.detached=true;}};series.markers=plugin;return plugin;},createChart:(element,options)=>{chart.options=options;return chart;},
    ColorType:{Solid:'solid'},LineStyle:{Dashed:2},CrosshairMode:{Magnet:1,Normal:0},
    PriceScaleMode:{Logarithmic:1,Normal:0},LineType:{WithSteps:1,Simple:0},
    customSeriesDefaultOptions:{},CandlestickSeries:'candle',AreaSeries:'area',LineSeries:'line'}};
}

test('Chart controls, data views, indicators, pinning and pan configuration are wired', async () => {
  const w=new Window({url:'http://localhost/chart?percentage=1&include_partials=false&gaps=false',settings:{disableJavaScriptEvaluation:true,
    disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
  w.document.write(await readFile(new URL('../static/chart.html',import.meta.url),'utf8'));
  for(const k of ['window','document','history','location','localStorage']) globalThis[k]= k==='window'?w:w[k];
  globalThis.getComputedStyle=w.getComputedStyle.bind(w);
  globalThis.matchMedia=()=>({matches:false});
  for(const [k,v] of Object.entries({chart:'#101319',text:'#e6eaf2',muted:'#8993a7',border:'#282e3b',green:'#26c59c',red:'#ee6072',blue:'#629aff',purple:'#b490ff',amber:'#edac55',accent:'#628bff'})) w.document.documentElement.style.setProperty(`--${k}`,v);
  const {chart,library}=engine();w.LightweightCharts=library;
  const requests=[];
  let failDownload=false;
  globalThis.fetch=async (url,options={})=>{
    requests.push(String(url));const q=new URL(String(url),'http://localhost').searchParams;
    const period=Number(q.get('atr_period'));
    if(String(url).startsWith('/download')) {
      assert.equal(options.method,'POST');
      return failDownload ? {ok:false,status:502,json:async()=>({detail:'Provider temporarily unavailable'})}
        : {ok:true,json:async()=>({metadata:{downloaded_minutes:6495}})};
    }
    const data=String(url).startsWith('/history')?payload(daily,'observed_daily_price')
      :q.get('sizing')==='atr'?payload(atrRows(period),'prior_daily_atr_close'):payload(renko,'exact_percentage_close');
    if(q.get('sizing')==='atr')Object.assign(data.metadata,{sizing:'atr',atr_period:period,percentage:null,last_atr_points:period});
    Object.assign(data.metadata,{symbol:q.get('symbol')||'NIFTY50',session_mode:q.get('session_mode')||'threshold'});
    if(q.get('symbol')==='TCS') Object.assign(data.metadata,{source_provider:'Yahoo Finance (unofficial chart endpoint)',downloaded_minutes:6495,
      pending_move:{open:102,last_price:103,move_pct:100/102,source_minutes:2},downloaded_at:'2026-10-10T12:00:00Z'});
    return {ok:true,json:async()=>data};
  };
  const $=id=>w.document.getElementById(id);
  const change=id=>$(id).dispatchEvent(new w.Event('change',{bubbles:true}));
  const submit=id=>$(id).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  try {
    await import(`../static/chart.mjs?smoke=${Date.now()}`);await settle();
    assert.equal($('chart-overlay').hidden,true);
    assert.equal(chart.series[0].type,'renko');assert.equal(chart.series[0].data.length,4);
    assert.equal($('count-partials').textContent,'1');
    assert.ok(requests[0].includes('include_partials=true'));
    assert.ok(requests[0].includes('session_mode=threshold'));
    assert.equal($('session-mode').value,'threshold');
    $('session-mode').value='daily';change('session-mode');await settle();
    assert.ok(requests.at(-1).includes('session_mode=daily'));
    $('session-mode').value='threshold';change('session-mode');await settle();
    for(let i=1;i<chart.series[0].data.length;i++) assert.equal(chart.series[0].data[i-1].close,chart.series[0].data[i].open);
    assert.equal(chart.options.handleScroll.pressedMouseMove,true);
    assert.equal(chart.options.handleScroll.vertTouchDrag,true);
    assert.equal(chart.options.handleScale.axisPressedMouseMove.price,true);
    const first=chart.series[0].data[0];
    assert.ok(chart.options.localization.timeFormatter(first.time).includes('2019'));
    assert.ok(!chart.options.localization.timeFormatter(first.time).includes('1970'));
    $('chart').dispatchEvent(new w.PointerEvent('pointerdown',{button:0,bubbles:true}));
    assert.equal(chart.priceOptions.autoScale,false);
    $('auto-scale-button').click();assert.equal(chart.priceOptions.autoScale,true);
    const width=chart.range.to-chart.range.from;$('zoom-in').click();
    assert.ok(chart.range.to-chart.range.from<width);
    chart.click({time:first.time});
    assert.equal($('selection-state').textContent,'Pinned');
    chart.crosshair({time:chart.series[0].data.at(-1).time});assert.equal($('block-id').textContent,'#1');
    $('unpin-button').click();assert.equal($('unpin-button').hidden,true);
    assert.equal($('show-gaps'),null);assert.equal($('show-partials'),null);
    assert.equal(chart.series[0].markers.items.length,1);
    assert.equal(chart.series[0].markers.items[0].text,'ATH*');
    chart.crosshair({time:chart.series[0].data[1].time});
    assert.equal($('breakout-detail').hidden,false);
    assert.ok($('breakout-detail').textContent.includes('104.03'));
    assert.ok($('ath-history-note').textContent.includes('2015'));
    $('show-ath').checked=false;change('show-ath');assert.equal(chart.series[0].markers.items.length,0);
    $('show-ath').checked=true;change('show-ath');assert.equal(chart.series[0].markers.items.length,1);
    $('sizing-mode').value='atr';change('sizing-mode');await settle();
    assert.equal($('percentage').disabled,true);assert.equal($('brick-atr-period').disabled,false);
    assert.equal($('atr-control').hidden,false);assert.equal($('percentage-control').hidden,true);
    assert.ok($('chart-mode').textContent.includes('ATR(14)'));
    assert.equal(chart.series[0].data[0].close,114);
    assert.ok($('detail-size').textContent.includes('14.00 points'));
    for(const period of [7,10,14]){
      w.document.querySelector(`[data-atr="${period}"]`).click();await settle();
      assert.ok(requests.at(-1).includes(`atr_period=${period}`));
      assert.ok($('chart-mode').textContent.includes(`ATR(${period})`));
      assert.equal(chart.series[0].data[0].close,100+period);
    }
    $('brick-atr-period').value='21';submit('controls');await settle();
    assert.ok(requests.at(-1).includes('atr_period=21'));
    assert.ok(w.location.search.includes('sizing=atr'));
    assert.ok($('json-link').href.includes('atr_period=21'));
    const beforeInvalid=requests.length;
    $('brick-atr-period').value='1.5';submit('controls');await settle();assert.equal(requests.length,beforeInvalid);
    $('brick-atr-period').value='21';
    $('sizing-mode').value='percentage';change('sizing-mode');await settle();
    assert.equal($('percentage').disabled,false);assert.equal($('brick-atr-period').disabled,true);
    assert.equal(chart.series[0].data[0].close,101);

    $('supertrend-enabled').checked=true;$('atr-period').value='1';$('atr-factor').value='1';
    $('ema-enabled').checked=true;$('ema-period').value='1';
    submit('indicator-form');
    assert.equal(chart.series.filter(s=>s.type==='line').length,2);
    assert.equal($('indicator-count').textContent,'2');
    assert.deepEqual(chart.series[2].data.map(p=>p.value),renko.map(b=>b.close));
    assert.ok(chart.series[1].data.at(-1).value!=null);
    w.document.querySelector('[data-range="2015-2020"]').click();await settle();
    assert.equal($('view-mode').value,'renko');assert.equal(chart.series[0].type,'renko');
    assert.equal(chart.series[0].data.length,4);
    $('view-mode').value='daily-line';change('view-mode');await settle();
    assert.ok(requests.some(x=>x.startsWith('/history')));
    assert.equal($('view-mode').value,'daily-line');assert.equal(chart.series[0].type,'area');
    assert.equal(chart.series[0].data[0].value,8200);assert.equal(chart.series[0].data.at(-1).value,14000);
    assert.equal($('percentage').disabled,true);
    assert.ok($('window-return').textContent.includes('+75.00%'));
    const requestCount=requests.length;
    $('view-mode').value='daily-candles';change('view-mode');await settle();
    assert.equal(chart.series[0].type,'candle');assert.equal(requests.length,requestCount);
    $('log-button').click();assert.equal(chart.priceOptions.mode,1);
    $('theme-button').click();assert.equal(w.document.documentElement.dataset.theme,'light');
    $('level-button').click();chart.click({point:{y:100}});
    assert.equal(chart.series[0].lines.length,1);
    $('clear-levels-button').click();assert.equal(chart.series[0].lines.length,0);
    $('start-date').value='2010-01-01';$('end-date').value='2010-12-31';submit('controls');await settle();
    assert.equal($('chart-overlay').hidden,false);assert.equal($('overlay-title').textContent,'No prices in this window');
    // Switching instruments clears the former date window and user price levels.
    $('symbol-input').value='TCS.NS';submit('instrument-form');await settle();
    assert.equal($('symbol-title').textContent,'TCS');assert.equal($('chart-symbol').textContent,'TCS');
    assert.equal($('watermark-symbol').textContent,'TCS');assert.equal($('start-date').value,'');
    assert.ok(requests.at(-1).includes('symbol=TCS'));assert.ok(w.location.search.includes('symbol=TCS'));
    assert.ok($('json-link').href.includes('symbol=TCS'));assert.equal(chart.series[0].lines.length,0);
    assert.equal(w.document.querySelector('[data-range="2015-2020"]').hidden,true);
    $('view-mode').value='renko';change('view-mode');await settle();
    assert.equal($('pending-note').hidden,false);assert.ok($('pending-note').textContent.includes('no brick drawn'));
    $('download-button').click();await settle();await settle();
    assert.ok(requests.some(x=>x.startsWith('/download?')&&x.includes('symbol=TCS')));
    assert.equal($('symbol-input').disabled,false);assert.equal($('chart-overlay').hidden,true);
    assert.ok($('download-hint').textContent.includes('6,495'));
    failDownload=true;$('download-button').click();await settle();await settle();
    assert.equal($('chart-overlay').hidden,false);assert.equal($('overlay-title').textContent,'Download unavailable');
    assert.equal(chart.series.length,0);assert.equal($('download-button').disabled,false);
    submit('instrument-form');await settle();assert.equal($('chart-overlay').hidden,true);
    $('symbol-input').value='INFY';submit('instrument-form');await settle();
    assert.equal($('chart-symbol').textContent,'INFY');assert.ok(requests.at(-1).includes('symbol=INFY'));
  } finally { await w.happyDOM.close(); }
});
