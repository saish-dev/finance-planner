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
      netWorth: cssVar("--series-1", "#2a78d6"),
      investments: cssVar("--series-2", "#eb6834"),
      bank: cssVar("--series-3", "#1baf7a"),
      loans: cssVar("--series-4", "#eda100"),
      pf: cssVar("--series-5", "#e87ba4"),
      pool: cssVar("--series-6", "#008300"),
      positive: cssVar("--series-1", "#2a78d6"),
      negative: cssVar("--polarity-negative", "#e34948"),
      grid: cssVar("--grid", "#e1e0d9"),
      axis: cssVar("--axis", "#c3c2b7"),
      muted: cssVar("--text-muted", "#898781"),
      ink: cssVar("--text-primary", "#0b0b0b"),
      surface: cssVar("--surface-1", "#fcfcfb"),
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
      ctx.font = "600 11px system-ui, -apple-system, 'Segoe UI', sans-serif";
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
      titleColor: colors.surface,
      bodyColor: colors.surface,
      padding: 10,
      cornerRadius: 6,
      displayColors: true,
      usePointStyle: true,
      callbacks: {
        label: (item) => ` ${item.dataset.label}: ${fullInr(item.parsed.y)}`,
      },
    };
  }

  window.renderPlannerCharts = function (data) {
    if (chartsUnavailable()) return;
    const colors = palette();

    const lineCanvas = destroy("net-worth-chart");
    if (lineCanvas) {
      const line = (label, values, color) => ({
        label,
        data: values,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
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
            line("Net worth", data.netWorth, colors.netWorth),
            line("Investments", data.investments, colors.investments),
            line("Bank", data.bank, colors.bank),
            line("Loans left", data.loans, colors.loans),
            line("PF corpus", data.pf, colors.pf),
            line("Surplus pool", data.pool, colors.pool),
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
            borderRadius: 4,
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

  window.renderLoanChart = function (data) {
    if (chartsUnavailable()) return;
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
})();
