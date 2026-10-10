"""Audit a saved real stock snapshot; optionally make a shareable static plot.

python validate_stock_source.py --symbol TCS --percentage 2 --plot examples/tcs_2pct.png
Plotting needs matplotlib; conversion/auditing otherwise uses the standard library.
"""
import argparse
import json
from pathlib import Path

from nifty_bars import convert_minutes, read_minutes
from stock_data import DEFAULT_DATA, cached_stock


def audit(symbol, percentage, data_dir):
    path, source = cached_stock(symbol, data_dir)
    candles = list(read_minutes(path, expected_instrument=source['symbol']))
    result = convert_minutes(candles, percentage)
    meta, bars = result['metadata'], result['bars']
    meta.update(source)
    for bar in bars:
        if bar['kind'] == 'move':
            assert abs(abs(bar['close'] / bar['open'] - 1) * 100 - percentage) < 1e-9
            assert bar['high'] == max(bar['open'], bar['close'])
            assert bar['low'] == min(bar['open'], bar['close'])
        elif bar['kind'] == 'partial':
            assert bar['reason'] == 'before_large_gap'
            assert bar['open'] != bar['close']
        else:
            assert abs(bar['move_pct']) >= percentage - 1e-9
    for left, right in zip(bars, bars[1:]):
        assert left['close'] == right['open']
        if left['kind'] == 'partial':
            assert right['kind'] in ('session_gap', 'data_gap')
    pending = meta['pending_move']
    assert sum(b['source_minutes'] for b in bars) + (pending or {}).get('source_minutes', 0) == len(candles)
    if pending:
        assert pending['last_price'] == float(candles[-1].close)
        if bars:
            assert pending['open'] == bars[-1]['close']
    events = [(b.get('ath_breakout') or {}).get('count', 0) for b in bars]
    assert sum(events) + ((pending or {}).get('ath_breakout') or {}).get('count', 0) == meta['ath_breakout_observations']
    meta['validation'] = {'exact_full_bricks': True, 'connected_boundaries': True,
                          'all_source_minutes_accounted_for': True, 'all_high_observations_accounted_for': True,
                          'no_ordinary_day_end_partials': True,
                          'independent_exchange_price_verification': False}
    return result


def plot(result, destination):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Patch
    from matplotlib.ticker import StrMethodFormatter
    meta, bars = result['metadata'], result['bars']
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, ax = plt.subplots(figsize=(12, 6.6), facecolor='#101319')
    ax.set_facecolor('#101319')
    palette = {'move': ('#26c59c', '#ee6072'), 'partial': ('#8cd8c4', '#ecabb5'),
               'session_gap': ('#629aff', '#b490ff'), 'data_gap': ('#edac55', '#edac55')}
    for i, b in enumerate(bars):
        color = palette[b['kind']][b['close'] < b['open']]
        ax.add_patch(Rectangle((i-.5, b['low']), 1, b['high']-b['low'],
                               facecolor=color, edgecolor='#101319', linewidth=.8))
        if len(bars) < 25:
            ax.text(i, (b['open']+b['close'])/2, f"{b['move_pct']:+g}%", ha='center', va='center', color='#101319', fontsize=10, weight='bold')
        if b.get('ath_breakout'):
            ax.scatter([i], [b['high']], color='#eab84f', marker='D', s=23, zorder=4)
    if bars:
        low, high = min(b['low'] for b in bars), max(b['high'] for b in bars)
        padding = max(1, high-low) * .18
        ax.set_ylim(low-padding, high+padding)
        ax.set_xlim(-1, len(bars))
        ticks = list(range(0, len(bars), max(1, len(bars)//6)))
        ax.set_xticks(ticks, [bars[i]['end'][8:10]+'/'+bars[i]['end'][5:7] for i in ticks])
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    ax.tick_params(colors='#8993a7', length=0, pad=9)
    ax.grid(axis='y', color='#282e3b', linewidth=.5)
    ax.set_axisbelow(True)
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.set_xlabel('Sequential confirmed bricks · dates in IST · equal spacing, variable duration', color='#8993a7', labelpad=15)
    fig.text(.065,.935,f"{meta['symbol']}  /  {meta['percentage']:g}% confirmed Renko", color='#e6eaf2', size=21, weight='bold')
    fig.text(.065,.89,f"NSE equity · INR    {meta['first_minute'][:10]} — {meta['last_minute'][:10]}    {meta['source_minutes']:,} observed minute candles", color='#8993a7', size=11)
    ax.legend(handles=[Patch(color='#26c59c',label='Up'), Patch(color='#ee6072',label='Down'),
                       Patch(color='#edac55',label='Large unknown interval'), Patch(color='#eab84f',label='Source-history high*')],
              loc='upper left', ncol=4, frameon=False, labelcolor='#8993a7', fontsize=9)
    pending = meta['pending_move']
    note = f"{len(bars)} plotted bricks. "
    if pending:
        note += f"Unfinished move: {pending['open']:,.2f} → {pending['last_price']:,.2f} ({pending['move_pct']:+.2f}%). No partial brick drawn."
    fig.text(.065,.10,note,color='#edac55',size=10)
    fig.text(.065,.057,'Yahoo minute snapshot has missing session-closing minutes. Unknown intervals are not verified overnight gaps.\n*Highs are relative to this downloaded history. This is a static calculation preview; use /chart for pan, zoom and indicators.',color='#8993a7',size=8.5,linespacing=1.7)
    fig.subplots_adjust(left=.065,right=.91,top=.85,bottom=.24)
    fig.savefig(destination, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', default='TCS')
    parser.add_argument('--percentage', type=float, default=2)
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output', type=Path, default=Path('examples/tcs_2pct_validation.json'))
    parser.add_argument('--plot', type=Path)
    args = parser.parse_args()
    result = audit(args.symbol, args.percentage, args.data_dir)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    if args.plot: plot(result, args.plot)
    print(json.dumps({'symbol': args.symbol, 'bars': result['metadata']['bar_counts'],
                      'validation': result['metadata']['validation']}))


if __name__ == '__main__': main()
