import { chartPoints, selectBars, indexForTime, earlierMonth, priceChange } from './chart-data.mjs';
import { supertrend, ema, sma } from './indicators.mjs';
import { RenkoSeries } from './renko-series.mjs';

const $ = id => document.getElementById(id);
const L = window.LightweightCharts;
const number = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const dateLabel = iso => iso ? new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' }).format(new Date(iso)) : '—';
const timeLabel = iso => iso ? new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date(iso)) : '—';
const format = value => Number.isFinite(value) ? number.format(value) : '—';
const percent = value => Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
const state = { raw: [], rows: [], points: [], metadata: null, type: '', key: '', series: null, overlays: [],
  controller: null, sequence: 0, selected: null, pinned: false, levels: [], priceLines: [], placing: false,
  auto: true, studyValues: new Map(), studies: { supertrend: false, atrPeriod: 10, factor: 3, ema: false, emaPeriod: 20, sma: false, smaPeriod: 50 } };
let chart, toastTimer;
const colors = () => {
  const css = getComputedStyle(document.documentElement);
  return Object.fromEntries(['chart','text','muted','border','green','red','blue','purple','amber','accent'].map(k => [k, css.getPropertyValue(`--${k}`).trim()]));
};
const text = (id, value) => { $(id).textContent = value; };
function paintChange(element, value) {
  element.classList.remove('positive','negative','neutral');
  element.classList.add(value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral');
}
function toast(message) {
  const element = $('toast'); element.textContent = message; element.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { element.hidden = true; }, 4000);
}
function overlay(title, description, { loading = false, retry = false } = {}) {
  $('chart-overlay').hidden = false; text('overlay-title', title); text('overlay-description', description);
  $('loading-ring').hidden = !loading; $('retry-button').hidden = !retry;
}
function setAuto(enabled) {
  state.auto = enabled;
  chart.priceScale('right').applyOptions({ autoScale: enabled });
  $('auto-scale-button').setAttribute('aria-pressed', String(enabled));
  $('auto-scale-button').title = enabled ? 'Price autoscaling enabled. Drag to pan freely.' : 'Restore automatic price scaling';
}
function themeOptions() {
  const c = colors();
  return { layout: { background: { type: L.ColorType.Solid, color: c.chart }, textColor: c.muted,
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif', fontSize: 11, attributionLogo: true },
    grid: { vertLines: { color: c.border + '48' }, horzLines: { color: c.border + '60' } },
    crosshair: { vertLine: { color: c.muted, labelBackgroundColor: '#435171', style: L.LineStyle.Dashed },
      horzLine: { color: c.muted, labelBackgroundColor: '#435171', style: L.LineStyle.Dashed } },
    rightPriceScale: { borderColor: c.border }, timeScale: { borderColor: c.border } };
}
function actualTime(time, withTime = true) {
  const bar = state.rows[indexForTime(time)];
  return bar ? `${dateLabel(bar.end)}${withTime && state.type === 'renko' ? '  ' + timeLabel(bar.end) : ''}` : '';
}
function initializeChart() {
  chart = L.createChart($('chart'), {
    autoSize: true, ...themeOptions(),
    rightPriceScale: { autoScale: true, borderColor: colors().border, scaleMargins: { top: .14, bottom: .14 }, minimumWidth: 76 },
    timeScale: { borderColor: colors().border, timeVisible: true, secondsVisible: false, rightOffset: 7,
      barSpacing: 10, minBarSpacing: .4, tickMarkFormatter: time => actualTime(time, false) },
    localization: { priceFormatter: format, timeFormatter: time => actualTime(time, true) },
    crosshair: { ...themeOptions().crosshair, mode: L.CrosshairMode.Magnet },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: true },
      axisDoubleClickReset: { time: true, price: true } },
    kineticScroll: { touch: true, mouse: true },
  });
  chart.subscribeCrosshairMove(param => {
    if (state.pinned || state.placing) return;
    const b = state.rows[indexForTime(param.time)];
    inspect(b || state.rows.at(-1));
  });
  chart.subscribeClick(param => {
    if (state.placing && param.point && state.series) {
      const price = state.series.coordinateToPrice(param.point.y);
      if (Number.isFinite(price)) { state.levels.push(price); addLevel(price); }
      setPlacing(false); return;
    }
    const b = state.rows[indexForTime(param.time)];
    if (b) { state.pinned = true; inspect(b); }
  });
  chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (!range || !state.rows.length) return;
    const first = state.rows[Math.max(0, Math.floor(range.from))];
    const last = state.rows[Math.min(state.rows.length - 1, Math.ceil(range.to))];
    if (first && last) text('visible-range', `${dateLabel(first.end)} — ${dateLabel(last.end)}`);
  });
  // Switch off price autoscaling at the start of a drag, so the library can pan
  // vertically as well as horizontally. The Auto button restores fitting.
  $('chart').addEventListener('pointerdown', event => {
    if (event.button !== 0 || state.placing || !state.rows.length) return;
    setAuto(false); $('chart').classList.add('panning');
  }, true);
  window.addEventListener('pointerup', () => $('chart').classList.remove('panning'));
}
function inspect(b) {
  if (!b) {
    for (const k of ['legend-open','legend-high','legend-low','legend-close','legend-move','detail-price','detail-change','detail-open','detail-high','detail-low','detail-close','detail-percent','detail-confirmed','detail-minutes','detail-date','block-id']) text(k, '—');
    text('block-kind', 'No block selected'); return;
  }
  state.selected = b;
  const kinds = { move: 'Full brick', session_gap: 'Session gap', data_gap: 'Unknown interval', partial: b.finalized === false ? 'Current remainder' : 'Session remainder', price: 'Daily price' };
  for (const k of ['open','high','low','close']) { text(`legend-${k}`, format(b[k])); text(`detail-${k}`, format(b[k])); }
  text('legend-move', percent(b.move_pct)); paintChange($('legend-move'), b.move_pct);
  text('detail-price', format(b.close));
  text('detail-change', `${b.close >= b.open ? '+' : ''}${format(b.close - b.open)}  (${percent(b.move_pct)})`);
  paintChange($('detail-change'), b.move_pct);
  text('detail-percent', percent(b.move_pct)); text('detail-date', `${dateLabel(b.end)} · ${timeLabel(b.end)} IST`);
  text('detail-confirmed', `${dateLabel(b.known_at)} ${timeLabel(b.known_at)}`);
  text('detail-minutes', b.source_minutes.toLocaleString('en-IN'));
  text('block-id', `#${b.bar_id + 1}`); text('block-kind', kinds[b.kind] || b.kind);
  text('selection-state', state.pinned ? 'Pinned' : 'Hover to inspect'); $('unpin-button').hidden = !state.pinned;
  text('block-explanation', b.kind === 'move' ? 'An exact percentage block. Excess movement continues into the next block.'
    : b.kind === 'session_gap' ? 'Full gap from the previous observed close to the next session open. It is not split into smaller blocks.'
    : b.kind === 'data_gap' ? 'An interval is missing or has nonstandard session times. This bar connects its known endpoints.'
    : b.kind === 'partial' ? `The remaining move from the last brick boundary to the observed close. Shown at its exact size; the percentage uses this brick’s own open.${b.finalized === false ? ' This last remainder can change as more minutes arrive.' : ''}${b.open === b.close ? ' Price is unchanged, so this brick is a horizontal tick.' : ''}`
    : b.complete_normal_session ? 'Observed daily OHLC, aggregated from the source minute candles.' : 'Observed daily OHLC. This date has incomplete or nonstandard session coverage.');
  updateIndicatorLegend(b);
}
function updateIndicatorLegend(bar) {
  const values = state.studyValues.get(bar?.bar_id) || {};
  const host = $('indicator-legend'); host.replaceChildren();
  for (const [name, value, color] of [
    [`ST ${state.studies.atrPeriod}, ${state.studies.factor}`, values.supertrend?.value, values.supertrend?.direction === 1 ? colors().green : colors().red],
    [`EMA ${state.studies.emaPeriod}`, values.ema, '#efb760'],
    [`SMA ${state.studies.smaPeriod}`, values.sma, '#aa91f5'],
  ]) {
    const enabled = name.startsWith('ST') ? state.studies.supertrend : name.startsWith('EMA') ? state.studies.ema : state.studies.sma;
    if (!enabled) continue;
    const span = document.createElement('span'); span.textContent = `${name}  ${format(value)}`; span.style.color = color; host.append(span);
  }
}
function prepareStudies() {
  const input = state.raw; // Include all bricks, daily remainders and unsplit gaps.
  const s = state.studies;
  const st = s.supertrend ? supertrend(input, s.atrPeriod, s.factor) : [];
  const e = s.ema ? ema(input, s.emaPeriod) : [];
  const m = s.sma ? sma(input, s.smaPeriod) : [];
  state.studyValues = new Map(input.map((b, i) => [b.bar_id, { supertrend: st[i], ema: e[i], sma: m[i] }]));
}
function drawStudies() {
  const s = state.studies;
  for (const [name, enabled, color] of [['supertrend',s.supertrend,colors().green], ['ema',s.ema,'#efb760'], ['sma',s.sma,'#aa91f5']]) {
    if (!enabled) continue;
    const overlaySeries = chart.addSeries(L.LineSeries, { color, lineWidth: 2,
      lineType: name === 'supertrend' ? L.LineType.WithSteps : L.LineType.Simple,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    overlaySeries.setData(state.rows.map((b, i) => {
      const data = state.studyValues.get(b.bar_id)?.[name];
      const value = name === 'supertrend' ? data?.value : data;
      return value == null ? { time: state.points[i].time } : { time: state.points[i].time, value,
        color: name === 'supertrend' ? (data.direction === 1 ? colors().green : colors().red) : color };
    }));
    state.overlays.push(overlaySeries);
  }
  text('indicator-count', String(Number(s.supertrend) + Number(s.ema) + Number(s.sma)));
}
function addLevel(price) {
  if (!state.series) return;
  state.priceLines.push(state.series.createPriceLine({ price, color: colors().accent, lineWidth: 1,
    lineStyle: L.LineStyle.Dashed, axisLabelVisible: true, title: '' }));
}
function setPlacing(on) {
  state.placing = on; $('level-button').classList.toggle('active', on);
  $('level-button').setAttribute('aria-pressed', String(on)); $('chart').classList.toggle('placing-level', on);
  if (on) toast('Click on the chart to place a price level. Escape cancels.');
}
function fitAll() { if (state.rows.length) { setAuto(true); chart.timeScale().fitContent(); } }
function latest() {
  if (!state.rows.length) return;
  const count = state.type === 'renko' ? 120 : 250;
  setAuto(true); chart.timeScale().setVisibleLogicalRange({ from: Math.max(-1, state.rows.length - count), to: state.rows.length + 6 });
}
function zoom(factor) {
  const r = chart.timeScale().getVisibleLogicalRange(); if (!r) return;
  const middle = (r.from + r.to) / 2, half = Math.max(4, (r.to - r.from) * factor / 2);
  chart.timeScale().setVisibleLogicalRange({ from: middle - half, to: middle + half });
}
function shareURL() {
  const q = new URLSearchParams(); q.set('percentage', $('percentage').value || '1');
  if ($('start-date').value) q.set('start', $('start-date').value);
  if ($('end-date').value) q.set('end', $('end-date').value);
  if ($('view-mode').value !== 'renko') q.set('view', $('view-mode').value);
  history.replaceState(null, '', `/chart?${q}`);
  const dataQuery = new URLSearchParams(q); dataQuery.delete('view');
  if (state.type === 'renko') dataQuery.set('include_partials', 'true');
  if (state.type !== 'renko') { dataQuery.delete('percentage'); dataQuery.delete('include_partials'); }
  $('json-link').href = `${state.type === 'renko' ? '/bars' : '/history'}?${dataQuery}`;
}
function render(reset = true) {
  if (!state.metadata) return;
  const previousRange = chart.timeScale().getVisibleLogicalRange();
  state.rows = selectBars(state.raw, { start: $('start-date').value, end: $('end-date').value });
  for (const s of state.overlays) chart.removeSeries(s); state.overlays = [];
  if (state.series) chart.removeSeries(state.series);
  state.points = chartPoints(state.rows, colors());
  state.series = state.type === 'renko'
    ? chart.addCustomSeries(new RenkoSeries(L.customSeriesDefaultOptions), { priceLineVisible: true, priceFormat: { type: 'price', precision: 2, minMove: .01 } })
    : $('view-mode').value === 'daily-line'
    ? chart.addSeries(L.AreaSeries, { lineColor: colors().blue, topColor: colors().blue + '35', bottomColor: colors().blue + '00', lineWidth: 2, priceLineVisible: true })
    : chart.addSeries(L.CandlestickSeries, { upColor: colors().green, downColor: colors().red, borderVisible: false, priceLineVisible: true, priceFormat: { type: 'price', precision: 2, minMove: .01 } });
  state.series.setData($('view-mode').value === 'daily-line'
    ? state.points.map(p => ({ time: p.time, value: p.close })) : state.points);
  state.priceLines = []; state.levels.forEach(addLevel);
  prepareStudies(); drawStudies();
  state.pinned = false; $('unpin-button').hidden = true;
  const counts = state.rows.reduce((a, b) => { a[b.kind] = (a[b.kind] || 0) + 1; return a; }, {});
  text('chart-mode', state.type === 'renko' ? `${state.metadata.percentage}% Renko` : $('view-mode').value === 'daily-line' ? 'Daily price trend' : 'Daily candles');
  document.querySelector('.mode-tag').textContent = state.type === 'renko' ? 'Remainders + gaps' : 'Observed OHLC';
  text('count-moves', String(counts.move || counts.price || 0)); $('count-moves').previousElementSibling.textContent = state.type === 'renko' ? 'Normal blocks' : 'Trading dates';
  text('count-gaps', String(counts.session_gap || 0)); text('count-unknown', String(counts.data_gap || 0));
  text('count-partials', String(counts.partial || 0));
  text('dataset-dates', `${dateLabel(state.metadata.first_minute)} — ${dateLabel(state.metadata.last_minute)}`);
  const change = priceChange(state.rows), changeElement = $('window-return').querySelector('b');
  changeElement.textContent = percent(change); paintChange(changeElement, change);
  $('window-return').title = 'First open to last close across the loaded date window; not strategy returns.';
  $('export-button').disabled = !state.rows.length;
  if (!state.rows.length) {
    overlay('No prices in this window', 'Choose a date range within the available dataset dates shown in the inspector.');
    inspect(null); text('status-text', 'No matching data'); $('status-dot').classList.remove('ready');
  } else {
    $('chart-overlay').hidden = true;
    if (reset) { if ($('start-date').value || $('end-date').value) fitAll(); else latest(); }
    else if (previousRange) chart.timeScale().setVisibleLogicalRange(previousRange);
    inspect(state.rows.at(-1));
    text('status-text', `${state.rows.length.toLocaleString('en-IN')} ${state.type === 'renko' ? 'blocks' : 'daily prices'} · Historical data`);
    $('status-dot').classList.add('ready');
  }
  for (const b of document.querySelectorAll('[data-percent]')) b.classList.toggle('active', Number(b.dataset.percent) === Number($('percentage').value));
  shareURL();
}
async function loadData() {
  const type = $('view-mode').value === 'renko' ? 'renko' : 'daily';
  updateModeControls(type);
  if (!$('controls').reportValidity()) return;
  if ($('start-date').value && $('end-date').value && $('start-date').value > $('end-date').value) { toast('The start date must be before the end date.'); return; }
  const query = type === 'renko' ? new URLSearchParams({ percentage: $('percentage').value, include_partials: 'true' }) : new URLSearchParams();
  const key = `${type}:${type === 'renko' ? $('percentage').value : ''}`;
  state.controller?.abort();
  const sequence = ++state.sequence;
  if (key === state.key && state.metadata) { state.type = type; $('apply-button').disabled = false; render(true); return; }
  state.controller = new AbortController();
  $('apply-button').disabled = true; $('export-button').disabled = true;
  overlay('Preparing your chart', 'Loading historical prices. The first request processes the source dataset and can take a few seconds.', { loading: true });
  $('status-dot').classList.remove('ready'); text('status-text', 'Loading dataset…');
  try {
    const response = await fetch(`${type === 'renko' ? '/bars' : '/history'}?${query}`, { signal: state.controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Could not load chart data (${response.status}).`);
    if (sequence !== state.sequence) return;
    state.type = type; state.raw = result.bars; state.metadata = result.metadata; state.key = key;
    render(true);
  } catch (error) {
    if (error.name === 'AbortError' || sequence !== state.sequence) return;
    overlay('Could not load the chart', error.message, { retry: true }); text('status-text', 'Data unavailable');
  } finally { if (sequence === state.sequence) $('apply-button').disabled = false; }
}
function updateModeControls(type) {
  for (const element of [$('percentage'), ...document.querySelectorAll('[data-percent]')]) element.disabled = type !== 'renko';
  text('indicator-basis', type === 'renko' ? 'All Renko bricks, including daily remainders and gap bars. Values on the current remainder can change with new minutes.' : 'Observed daily OHLC, with all available prior dates used for warm-up.');
}
function changeTheme() {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  try { localStorage.setItem('realrenko-theme', dark ? 'dark' : 'light'); } catch {}
  chart.applyOptions(themeOptions()); if (state.metadata) render(false);
}
function events() {
  $('controls').addEventListener('submit', event => { event.preventDefault(); loadData(); });
  $('retry-button').addEventListener('click', loadData);
  $('view-mode').addEventListener('change', loadData);
  for (const b of document.querySelectorAll('[data-percent]')) b.addEventListener('click', () => { $('percentage').value = b.dataset.percent; loadData(); });
  for (const button of document.querySelectorAll('[data-range]')) button.addEventListener('click', async () => {
    if (!state.metadata) return;
    for (const b of document.querySelectorAll('[data-range]')) b.classList.toggle('active', b === button);
    if (button.dataset.range === '2015-2020') {
      $('start-date').value = '2015-01-01'; $('end-date').value = '2020-12-31'; await loadData();
    } else {
      const end = state.metadata.last_minute.slice(0, 10);
      $('start-date').value = button.dataset.range === 'all' ? '' : earlierMonth(end, Number(button.dataset.range));
      $('end-date').value = button.dataset.range === 'all' ? '' : end;
      render(true); if (button.dataset.range === 'all') fitAll();
    }
  });
  $('indicator-form').addEventListener('submit', event => {
    event.preventDefault(); if (!$('indicator-form').reportValidity()) return;
    const next = { supertrend: $('supertrend-enabled').checked, atrPeriod: Number($('atr-period').value), factor: Number($('atr-factor').value),
      ema: $('ema-enabled').checked, emaPeriod: Number($('ema-period').value), sma: $('sma-enabled').checked, smaPeriod: Number($('sma-period').value) };
    if (![next.atrPeriod,next.emaPeriod,next.smaPeriod].every(Number.isInteger)) { toast('Indicator lengths must be whole numbers.'); return; }
    state.studies = next; $('indicator-menu').open = false;
    try { if (state.metadata) render(false); } catch (error) { toast(error.message); }
  });
  $('zoom-in').addEventListener('click', () => zoom(.75)); $('zoom-out').addEventListener('click', () => zoom(1.33));
  $('fit-button').addEventListener('click', fitAll); $('latest-button').addEventListener('click', latest);
  $('auto-scale-button').addEventListener('click', () => setAuto(true));
  $('log-button').addEventListener('click', () => {
    const enabled = $('log-button').getAttribute('aria-pressed') !== 'true';
    $('log-button').setAttribute('aria-pressed', String(enabled));
    chart.priceScale('right').applyOptions({ mode: enabled ? L.PriceScaleMode.Logarithmic : L.PriceScaleMode.Normal }); setAuto(true);
  });
  $('crosshair-button').addEventListener('click', () => {
    const enabled = $('crosshair-button').getAttribute('aria-pressed') !== 'true';
    $('crosshair-button').setAttribute('aria-pressed', String(enabled)); $('crosshair-button').classList.toggle('active', enabled);
    chart.applyOptions({ crosshair: { mode: enabled ? L.CrosshairMode.Magnet : L.CrosshairMode.Normal } });
  });
  $('level-button').addEventListener('click', () => setPlacing(!state.placing));
  $('clear-levels-button').addEventListener('click', () => { state.priceLines.forEach(line => state.series?.removePriceLine(line)); state.levels = []; state.priceLines = []; });
  $('unpin-button').addEventListener('click', () => { state.pinned = false; inspect(state.rows.at(-1)); });
  $('details-toggle').addEventListener('click', () => {
    const narrow = matchMedia('(max-width:900px)').matches;
    $('app').classList.toggle(narrow ? 'details-shown' : 'details-hidden');
    const visible = narrow ? $('app').classList.contains('details-shown') : !$('app').classList.contains('details-hidden');
    $('details-toggle').setAttribute('aria-pressed', String(visible));
  });
  $('theme-button').addEventListener('click', changeTheme);
  $('fullscreen-button').addEventListener('click', async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else if ($('app').requestFullscreen) await $('app').requestFullscreen(); else toast('Fullscreen is unavailable in this browser.'); }
    catch { toast('Your browser did not allow fullscreen.'); }
  });
  $('export-button').addEventListener('click', () => {
    try { const a = document.createElement('a'); a.href = chart.takeScreenshot().toDataURL('image/png'); a.download = 'realrenko-nifty50.png'; a.click(); }
    catch { toast('Could not export the chart in this browser.'); }
  });
  $('help-button').addEventListener('click', () => $('help-dialog').showModal());
  $('close-help').addEventListener('click', () => $('help-dialog').close());
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape') { setPlacing(false); state.pinned = false; $('indicator-menu').open = false; inspect(state.rows.at(-1)); return; }
    if (['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName) || $('help-dialog').open) return;
    if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(.75); }
    if (event.key === '-') { event.preventDefault(); zoom(1.33); }
    if (event.key === 'Home') { event.preventDefault(); fitAll(); }
    if (event.key === 'End') { event.preventDefault(); latest(); }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const r = chart.timeScale().getVisibleLogicalRange(); if (!r) return; event.preventDefault();
      const shift = (r.to - r.from) * .15 * (event.key === 'ArrowLeft' ? -1 : 1);
      chart.timeScale().setVisibleLogicalRange({ from: r.from + shift, to: r.to + shift });
    }
  });
}
try {
  if (!L) throw new Error('Chart library did not load. Pull the complete repository, including static/vendor, and restart the server.');
  try { document.documentElement.dataset.theme = localStorage.getItem('realrenko-theme') === 'light' ? 'light' : 'dark'; } catch {}
  const q = new URLSearchParams(location.search);
  const p = Number((q.get('percentage') || '1').replace('%', ''));
  if (Number.isFinite(p) && p > 0 && p < 100) $('percentage').value = String(p);
  $('start-date').value = q.get('start') || ''; $('end-date').value = q.get('end') || '';
  if (['daily-line','daily-candles'].includes(q.get('view'))) $('view-mode').value = q.get('view');
  $('details-toggle').setAttribute('aria-pressed', String(!matchMedia('(max-width:900px)').matches));
  initializeChart(); events(); setAuto(true); loadData();
} catch (error) { overlay('Chart unavailable', error.message); }
