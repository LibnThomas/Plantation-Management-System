/* Shared Chart.js helpers for PMS.
 * Categorical palette: validated 8-slot order, assigned in fixed order (never cycled);
 * a 9th+ series is folded into "Other". One y-axis only. */
(function () {
  const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
  const OTHER = "#9a9893";
  const SURFACE = "#ffffff";
  const fmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

  if (window.Chart) {
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = "#52514e";
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.legend.labels.boxHeight = 12;
    Chart.defaults.plugins.tooltip.callbacks.label = (ctx) => {
      const v = ctx.parsed.y !== undefined && ctx.chart.options.indexAxis !== "y" ? ctx.parsed.y : ctx.parsed.x;
      return ` ${ctx.dataset.label || ctx.label}: ${fmt.format(v)}`;
    };
    Chart.defaults.maintainAspectRatio = false;
  }

  function foldSeries(series) {
    if (series.length <= 8) return series;
    // keep the 7 largest by total, fold the rest into "Other"
    const withTotal = series.map((s, i) => ({ s, i, t: s.data.reduce((a, b) => a + (+b || 0), 0) }));
    withTotal.sort((a, b) => b.t - a.t);
    const keep = withTotal.slice(0, 7).sort((a, b) => a.i - b.i).map((x) => x.s);
    const rest = withTotal.slice(7).map((x) => x.s);
    const other = { label: "Other", data: rest[0].data.map((_, k) => rest.reduce((a, s) => a + (+s.data[k] || 0), 0)), other: true };
    return keep.concat([other]);
  }

  function axisOpts(stacked, horizontal) {
    const val = { stacked, beginAtZero: true, grid: { color: "#eceeec" }, border: { display: false },
      ticks: { callback: (v) => fmt.format(v) } };
    const cat = { stacked, grid: { display: false }, border: { color: "#c9ccc9" } };
    return horizontal ? { x: val, y: cat } : { x: cat, y: val };
  }

  window.PMSCharts = {
    palette: PALETTE,
    fmt,
    bar(el, labels, series, opts = {}) {
      if (!el) return;
      series = foldSeries(series);
      let ci = 0;
      const datasets = series.map((s) => {
        const color = s.other ? OTHER : PALETTE[ci++];
        return { label: s.label, data: s.data, backgroundColor: color, borderColor: SURFACE, borderWidth: opts.stacked ? 1 : 0,
          borderRadius: 4, borderSkipped: "start", maxBarThickness: 36, categoryPercentage: 0.8, barPercentage: 0.9 };
      });
      return new Chart(el, {
        type: "bar",
        data: { labels, datasets },
        options: {
          indexAxis: opts.horizontal ? "y" : "x",
          interaction: { mode: opts.stacked ? "index" : "nearest", intersect: false },
          plugins: { legend: { display: datasets.length > 1, position: "bottom" } },
          scales: axisOpts(!!opts.stacked, !!opts.horizontal),
        },
      });
    },
    line(el, labels, series) {
      if (!el) return;
      series = foldSeries(series);
      let ci = 0;
      const datasets = series.map((s) => {
        const color = s.other ? OTHER : PALETTE[ci++];
        return { label: s.label, data: s.data, borderColor: color, backgroundColor: color, borderWidth: 2,
          pointRadius: labels.length > 40 ? 0 : 3, pointHoverRadius: 5, tension: 0.25, spanGaps: true };
      });
      return new Chart(el, {
        type: "line",
        data: { labels, datasets },
        options: {
          interaction: { mode: "index", intersect: false },
          plugins: { legend: { display: datasets.length > 1, position: "bottom" } },
          scales: axisOpts(false, false),
        },
      });
    },
    /* single-series horizontal ranking (e.g. labour cost by job type) */
    ranking(el, labels, data, label) {
      if (!el) return;
      el.parentElement.style.height = Math.max(180, labels.length * 30 + 40) + "px";
      return this.bar(el, labels, [{ label: label || "", data }], { horizontal: true });
    },
  };
})();
