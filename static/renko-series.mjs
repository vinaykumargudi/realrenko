// Full-width Renko rectangles on ordinal x coordinates. No wick, missing time
// slot, or rescaled price: adjacent bodies share a horizontal boundary.
export class RenkoSeries {
  constructor(defaults) { this.defaults = defaults; this.data = null; }
  defaultOptions() { return { ...this.defaults }; }
  priceValueBuilder(bar) { return [bar.high, bar.low, bar.close]; }
  isWhitespace(bar) { return bar.open == null || bar.close == null; }
  update(data) { this.data = data; }
  renderer() { return this; }
  destroy() { this.data = null; }
  draw(target, priceToCoordinate) {
    const data = this.data;
    if (!data?.visibleRange) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: rx, verticalPixelRatio: ry }) => {
      ctx.save();
      for (let i = Math.max(0, data.visibleRange.from); i < Math.min(data.bars.length, data.visibleRange.to); i++) {
        const { x, originalData: bar, barColor } = data.bars[i];
        const open = priceToCoordinate(bar.open), close = priceToCoordinate(bar.close);
        if (open == null || close == null || !Number.isFinite(open) || !Number.isFinite(close)) continue;
        const left = Math.round((x - data.barSpacing / 2) * rx);
        const right = Math.round((x + data.barSpacing / 2) * rx);
        const top = Math.round(Math.min(open, close) * ry);
        const bottom = Math.round(Math.max(open, close) * ry);
        const width = Math.max(1, right - left), height = Math.max(1, bottom - top);
        ctx.fillStyle = bar.color || barColor;
        ctx.globalAlpha = bar.remainder ? 0.55 : 1;
        ctx.fillRect(left, top, width, height);
        // A flat day is a one-pixel horizontal tick at the unchanged price.
        // Remainders have a solid outline so even very small bodies are visible.
        if (bar.remainder) {
          ctx.globalAlpha = 1;
          ctx.fillRect(left, top, width, 1);
          ctx.fillRect(left, top + height - 1, width, 1);
          ctx.fillRect(left, top, 1, height);
          ctx.fillRect(left + width - 1, top, 1, height);
        }
      }
      ctx.restore();
    });
  }
}
