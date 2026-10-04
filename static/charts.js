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

  /* HTMX replaces whole page sections, which removes canvases but leaves their
   * Chart instances alive (and observing detached nodes). Drop any chart whose
   * canvas is no longer in the page before drawing new ones. */
  function pruneCharts() {
    Object.values(Chart.instances).forEach(function (chart) {
      if (!chart.canvas || !chart.canvas.isConnected) chart.destroy();
    });
  }

  function destroy(canvasId) {
    pruneCharts();
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

    const stacked = (canvasId, labels, sets) => {
      const canvas = destroy(canvasId);
      if (!canvas) return;
      new Chart(canvas, {
        type: "bar",
        data: {
          labels,
          datasets: sets.map(([label, values, color]) => ({
            label, data: values, backgroundColor: color, borderWidth: 0, borderRadius: 3, borderSkipped: false,
          })),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales: (() => {
            const scales = baseScales(colors);
            scales.x.stacked = true;
            scales.y.stacked = true;
            return scales;
          })(),
          plugins: {
            legend: { position: "top", align: "start",
              labels: { color: colors.muted, usePointStyle: true, pointStyle: "rect", boxWidth: 10, font: { size: 12 } } },
            tooltip: tooltip(colors),
          },
        },
      });
    };

    if (data.living) {
      stacked("flow-chart", data.labels, [
        ["Living", data.living, colors.bank],
        ["Insurance", data.insurance, colors.pf],
        ["EMIs", data.emi, colors.loans],
        ["SIPs", data.sip, colors.investments],
        ["One-time", data.oneTime, colors.muted],
        ["Left over", data.left, colors.netWorth],
        ["Overspent", data.over, colors.negative],
      ]);
      stacked("growth-chart", data.labels, [
        ["Put into funds", data.capFunds, colors.investments],
        ["Fund growth", data.gainFunds, colors.netWorth],
        ["PF contributions", data.capPf, colors.pf],
        ["PF interest", data.gainPf, colors.loans],
      ]);
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

  /* Prepayment: balance with the current EMI (dashed) against with extra
   * payments (solid). Redrawn each time the form changes. */
  let lastPrepay = null;

  window.renderPrepayChart = function (data) {
    lastPrepay = data;
    if (chartsUnavailable()) return;
    Chart.defaults.font.family = "'Bricolage Grotesque', system-ui, sans-serif";
    const colors = palette();
    const canvas = destroy("prepay-chart");
    if (!canvas) return;
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [
          { label: "Current EMI", data: data.base, borderColor: colors.muted, backgroundColor: colors.muted,
            borderWidth: 2, borderDash: [6, 5], pointRadius: 0, tension: 0.2 },
          { label: "With extra payments", data: data.new, borderColor: colors.netWorth, backgroundColor: colors.netWorth,
            borderWidth: 3, pointRadius: 0, tension: 0.2 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: baseScales(colors, { beginAtZero: true }),
        plugins: {
          legend: { position: "top", align: "start",
            labels: { color: colors.muted, usePointStyle: true, pointStyle: "line", boxWidth: 24, font: { size: 12 } } },
          tooltip: tooltip(colors),
        },
      },
    });
  };

  window.renderLoanChart = function (data) {
    lastLoan = data;
    if (chartsUnavailable()) return;
    Chart.defaults.font.family = "'Bricolage Grotesque', system-ui, sans-serif";
    const colors = palette();

    // Interest versus principal, totalled per year.
    const split = destroy("loan-split-chart");
    if (split && data.splitYears) {
      const scales = baseScales(colors);
      scales.x.stacked = true;
      scales.y.stacked = true;
      new Chart(split, {
        type: "bar",
        data: {
          labels: data.splitYears,
          datasets: [
            { label: "Principal", data: data.splitPrincipal, backgroundColor: colors.netWorth, borderRadius: 3, borderSkipped: false },
            { label: "Interest", data: data.splitInterest, backgroundColor: colors.loans, borderRadius: 3, borderSkipped: false },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales,
          plugins: {
            legend: { position: "top", align: "start",
              labels: { color: colors.muted, usePointStyle: true, pointStyle: "rect", boxWidth: 10, font: { size: 12 } } },
            tooltip: tooltip(colors),
          },
        },
      });
    }

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
    if (lastPrepay) window.renderPrepayChart(lastPrepay);
  });

  /* ---------------------------------------------------------------- boot */

  /* Pages carry their chart data as <script type="application/json"
   * data-chart="kind"> and no inline calls. Drawing waits until the page --
   * or an HTMX swap -- has settled: running a script mid-swap measured the
   * canvases before they were in their final place, and Chart.js then
   * restored them to the wrong size. */
  const renderers = {
    planner: (data) => window.renderPlannerCharts(data),
    loan: (data) => window.renderLoanChart(data),
    prepay: (data) => window.renderPrepayChart(data),
  };

  function drawCharts(scope) {
    (scope || document).querySelectorAll("script[data-chart]").forEach(function (script) {
      const render = renderers[script.dataset.chart];
      if (!render) return;
      try {
        render(JSON.parse(script.textContent));
      } catch (error) {
        console.error("Could not draw chart", script.dataset.chart, error);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { drawCharts(document); });
  } else {
    drawCharts(document);
  }
  document.addEventListener("htmx:afterSettle", function (event) {
    const target = event.detail && event.detail.elt ? event.detail.elt : event.target;
    drawCharts(target);
  });
})();
