/* Chart rendering.
 *
 * Colours are read from CSS custom properties rather than hard-coded, so the
 * light and dark palettes (each stepped for its own surface, not flipped)
 * live in one place in planner.css.
 *
 * These functions are re-invoked after every HTMX swap of the dashboard body,
 * so each one destroys the chart already attached to its canvas first.
 */

(function () {
  "use strict";

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.body).getPropertyValue(name).trim();
    return value || fallback;
  }

  function palette() {
    return {
      netWorth: cssVar("--series-1", "#0e7a55"),
      investments: cssVar("--series-2", "#2f6fde"),
      bank: cssVar("--series-3", "#0f2a20"),
      loans: cssVar("--series-4", "#d98a00"),
      pf: cssVar("--series-5", "#8a5cd6"),
      positive: cssVar("--series-1", "#0e7a55"),
      negative: cssVar("--polarity-negative", "#d1432b"),
      grid: cssVar("--grid", "#e3e9e4"),
      axis: cssVar("--axis", "#c4cdc6"),
      muted: cssVar("--text-muted", "#5f6e65"),
      ink: cssVar("--hero", "#0f2a20"),
      surface: cssVar("--surface-1", "#ffffff"),
    };
  }

  /* ₹1.2 Cr / ₹12.5 L / ₹9,500 -- the same shorthand the tables use. */
  function compactInr(value) {
    const sign = value < 0 ? "-" : "";
    const size = Math.abs(value);
    if (size >= 1e7) return sign + "₹" + (size / 1e7).toFixed(size >= 1e8 ? 0 : 1) + " Cr";
    if (size >= 1e5) return sign + "₹" + (size / 1e5).toFixed(size >= 1e6 ? 0 : 1) + " L";
    if (size >= 1e3) return sign + "₹" + Math.round(size / 1e3) + "K";
    return sign + "₹" + Math.round(size);
  }

  function fullInr(value) {
    const sign = value < 0 ? "-" : "";
    const digits = Math.round(Math.abs(value)).toString();
    const head = digits.slice(0, -3);
    const tail = digits.slice(-3);
    if (!head) return sign + "₹" + tail;
    return sign + "₹" + head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
  }

  /* Chart.js comes from a CDN. If it cannot be reached, say so in place of
   * the chart rather than leaving an empty box -- every number in these
   * charts also exists in the Summary and Cashflow tables. */
  function chartsUnavailable() {
    if (typeof Chart !== "undefined") return false;
    document.querySelectorAll(".chart-holder").forEach(function (holder) {
      holder.innerHTML =
        '<p class="chart-fallback">Charts need the Chart.js CDN, which this browser ' +
        "could not reach. The same figures are in the Summary and Cashflow tables.</p>";
    });
    return true;
  }

  function destroy(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();
    return canvas;
  }

  /* Series names printed at the last point, so identity never rests on colour
   * alone -- which is also the relief the aqua and yellow steps need on the
   * light surface, where they sit below 3:1 contrast. */
  const endLabels = {
    id: "endLabels",
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      const colors = palette();
      ctx.save();
      ctx.font = "600 11px 'Bricolage Grotesque', system-ui, sans-serif";
      ctx.textBaseline = "middle";
      chart.data.datasets.forEach((dataset, index) => {
        const meta = chart.getDatasetMeta(index);
        if (meta.hidden) return;
        const last = meta.data[meta.data.length - 1];
        if (!last) return;
        ctx.fillStyle = colors.muted;
        ctx.fillText(dataset.label, last.x + 8, last.y);
      });
      ctx.restore();
    },
  };

  /* A soft vertical fade under the net-worth line. A function, not a fixed
   * colour, so it is sized to the chart once Chart.js knows the plot area. */
  function areaFill(canvas, color) {
    return function (context) {
      const area = context.chart.chartArea;
      if (!area) return "transparent";
      const gradient = context.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
      gradient.addColorStop(0, color + "40");
      gradient.addColorStop(1, color + "00");
      return gradient;
    };
  }

  function baseScales(colors, opts) {
    return {
      x: {
        grid: { display: false },
        border: { color: colors.axis },
        ticks: { color: colors.muted, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 12 },
      },
      y: {
        grid: { color: colors.grid, drawTicks: false },
        border: { display: false },
        ticks: {
          color: colors.muted,
          font: { size: 11 },
          padding: 8,
          callback: (value) => compactInr(value),
        },
        ...(opts || {}),
      },
    };
  }

  function tooltip(colors) {
    return {
      backgroundColor: colors.ink,
      titleColor: "#ffffff",
      bodyColor: "#ffffff",
      padding: 12,
      cornerRadius: 12,
      displayColors: true,
      usePointStyle: true,
      callbacks: {
        label: (item) => ` ${item.dataset.label}: ${fullInr(item.parsed.y)}`,
      },
    };
  }

  let lastPlanner = null;
  let lastLoan = null;

  window.renderPlannerCharts = function (data) {
    lastPlanner = data;
    if (chartsUnavailable()) return;
    Chart.defaults.font.family = "'Bricolage Grotesque', system-ui, sans-serif";
    const colors = palette();

    const lineCanvas = destroy("net-worth-chart");
    if (lineCanvas) {
      const line = (label, values, color, fill) => ({
        label,
        data: values,
        borderColor: color,
        backgroundColor: fill ? areaFill(lineCanvas, color) : color,
        fill: !!fill,
        borderWidth: fill ? 3 : 2,
        tension: 0.25,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBorderWidth: 2,
        pointHoverBorderColor: colors.surface,
      });

      new Chart(lineCanvas, {
        type: "line",
        plugins: [endLabels],
        data: {
          labels: data.labels,
          datasets: [
            line("Net worth", data.netWorth, colors.netWorth, true),
            line("Investments", data.investments, colors.investments),
            line("Bank", data.bank, colors.bank),
            line("Loans left", data.loans, colors.loans),
            line("PF corpus", data.pf, colors.pf),
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          layout: { padding: { right: 78 } },
          interaction: { mode: "index", intersect: false },
          scales: baseScales(colors),
          plugins: {
            legend: {
              position: "top",
              align: "start",
              labels: { color: colors.muted, usePointStyle: true, pointStyle: "line", boxWidth: 24, font: { size: 12 } },
            },
            tooltip: tooltip(colors),
          },
        },
      });
    }

    const barCanvas = destroy("surplus-chart");
    if (barCanvas) {
      new Chart(barCanvas, {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: [{
            label: "Net surplus",
            data: data.surplus,
            // A single series carrying polarity: the diverging blue/red pair,
            // not four categorical hues.
            backgroundColor: data.surplus.map((v) => (v < 0 ? colors.negative : colors.positive)),
            borderRadius: 8,
            borderSkipped: false,
            barPercentage: 0.82,
            categoryPercentage: 0.86,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales: baseScales(colors, { beginAtZero: true }),
          plugins: {
            legend: { display: false },
            tooltip: tooltip(colors),
          },
        },
      });
    }
  };

  /* What-if: the scenario against the baseline. Baseline is a muted dashed
   * line so the scenario, drawn solid in the accent colour, is what the eye
   * lands on. A shaded band marks a job-loss stretch on the monthly chart. */
  let lastWhatIf = null;

  window.renderWhatIfCharts = function (data) {
    lastWhatIf = data;
    if (chartsUnavailable()) return;
    Chart.defaults.font.family = "'Bricolage Grotesque', system-ui, sans-serif";
    const colors = palette();

    const pair = (canvasId, labels, base, scen, extra) => {
      const canvas = destroy(canvasId);
      if (!canvas) return;
      new Chart(canvas, {
        type: "line",
        plugins: extra || [],
        data: {
          labels,
          datasets: [
            { label: "Baseline", data: base, borderColor: colors.muted, backgroundColor: colors.muted,
              borderWidth: 2, borderDash: [6, 5], pointRadius: 0, pointHoverRadius: 4, tension: 0.2 },
            { label: "What-if", data: scen, borderColor: colors.netWorth, backgroundColor: colors.netWorth,
              borderWidth: 3, pointRadius: 0, pointHoverRadius: 4, tension: 0.2 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales: baseScales(colors),
          plugins: {
            legend: { position: "top", align: "start",
              labels: { color: colors.muted, usePointStyle: true, pointStyle: "line", boxWidth: 24, font: { size: 12 } } },
            tooltip: tooltip(colors),
          },
        },
      });
    };

    const band = {
      id: "eventBand",
      beforeDatasetsDraw(chart) {
        const span = data.event && data.event.jobLoss;
        if (!span) return;
        const x = chart.scales.x;
        const from = data.months.indexOf(span[0]);
        const to = data.months.indexOf(span[1]);
        if (from < 0 || to < 0) return;
        const { ctx, chartArea } = chart;
        const left = x.getPixelForValue(from);
        const right = x.getPixelForValue(to);
        ctx.save();
        ctx.fillStyle = colors.negative + "22";
        ctx.fillRect(left, chartArea.top, Math.max(right - left, 3), chartArea.bottom - chartArea.top);
        ctx.restore();
      },
    };

    pair("whatif-nw-chart", data.years, data.nwBase, data.nwScen);
    pair("whatif-bank-chart", data.months, data.bankBase, data.bankScen, [band]);
  };

  window.renderLoanChart = function (data) {
    lastLoan = data;
    if (chartsUnavailable()) return;
    Chart.defaults.font.family = "'Bricolage Grotesque', system-ui, sans-serif";
    const colors = palette();
    const canvas = destroy("loan-chart");
    if (!canvas) return;

    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [{
          label: "Balance outstanding",
          data: data.balance,
          borderColor: colors.loans,
          backgroundColor: colors.loans,
          borderWidth: 2,
          tension: 0.2,
          pointRadius: 0,
          pointHoverRadius: 4,
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: baseScales(colors, { beginAtZero: true }),
        plugins: {
          // One series: the card heading names it, so no legend box.
          legend: { display: false },
          tooltip: tooltip(colors),
        },
      },
    });
  };

  // The theme switch changes the CSS variables the colours come from.
  document.addEventListener("planner:theme", function () {
    if (lastPlanner) window.renderPlannerCharts(lastPlanner);
    if (lastLoan) window.renderLoanChart(lastLoan);
    if (lastWhatIf) window.renderWhatIfCharts(lastWhatIf);
  });
})();
