/* Home Sensors dashboard */
"use strict";

const METRICS = {
  temperature: { label: "Temperature", unit: "°C", decimals: 1 },
  humidity:    { label: "Humidity", unit: "%", decimals: 1 },
  co2:         { label: "CO₂", unit: "ppm", decimals: 0 },
  pm25:        { label: "PM2.5", unit: "µg/m³", decimals: 0 },
  pm10:        { label: "PM10", unit: "µg/m³", decimals: 0 },
  tvoc:        { label: "TVOC", unit: "ppb", decimals: 0 },
  pressure:    { label: "Pressure", unit: "hPa", decimals: 1 },
  power:       { label: "Power draw", unit: "W", decimals: 1 },
  energy_kwh:  { label: "Energy", unit: "kWh", decimals: 3 },
  brightness:  { label: "Brightness", unit: "%", decimals: 0 },
  powerSwitch: { label: "Power", unit: "", decimals: 0, stepped: true },
};
// metrics shown on device cards but never charted
const CARD_METRICS = {
  battery: { label: "Battery", unit: "%", decimals: 0 },
  signal:  { label: "Wi-Fi signal", unit: "dBm", decimals: 0 },
  voltage: { label: "Voltage", unit: "V", decimals: 1 },
  current: { label: "Current", unit: "A", decimals: 2 },
};

let rangeHours = 24;
let devices = [];
let charts = [];

const $ = (sel) => document.querySelector(sel);
const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const seriesColor = (slot) => cssVar(`--series-${(slot % 8) + 1}`);

function fmtValue(metric, value) {
  const cfg = METRICS[metric] || CARD_METRICS[metric] || { decimals: 1 };
  return Number(value).toFixed(cfg.decimals ?? 1);
}

function fmtAge(ts) {
  if (!ts) return "";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 129600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function fmtTick(ms) {
  const d = new Date(ms);
  return rangeHours <= 24
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" }) +
      (rangeHours <= 168 ? " " + d.toLocaleTimeString([], { hour: "2-digit" }) : "");
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

function renderStatus(status) {
  const box = $("#source-chips");
  box.innerHTML = "";
  for (const [name, st] of Object.entries(status.sources)) {
    const chip = document.createElement("span");
    let cls = "", text;
    if (!st.configured) {
      text = `${name}: no API key`;
    } else if (st.last_error) {
      cls = "bad";
      text = `${name}: error`;
      chip.title = st.last_error;
    } else if (st.last_success || st.connected) {
      cls = "good";
      text = st.device_count !== undefined
        ? `${name}: ${st.device_count} device${st.device_count === 1 ? "" : "s"}`
        : `${name}: live`;
    } else {
      text = `${name}: connecting…`;
    }
    chip.className = `chip ${cls}`;
    chip.innerHTML = `<span class="dot"></span>`;
    chip.append(text);
    box.append(chip);
  }
  const unconfigured = Object.entries(status.sources)
    .filter(([, st]) => !st.configured).map(([n]) => n);
  const note = $("#setup-note");
  if (unconfigured.length) {
    note.hidden = false;
    note.innerHTML =
      `<strong>Setup needed:</strong> no API credentials for ` +
      `<strong>${unconfigured.join("</strong> and <strong>")}</strong>. ` +
      `<a href="/setup" style="color:var(--series-1);font-weight:500;">Configure →</a>`;
  } else {
    note.hidden = true;
  }
}

function deviceCard(dev) {
  const card = document.createElement("div");
  card.className = "card device-card";
  const latest = dev.latest || {};

  const title = document.createElement("h3");
  const sw = document.createElement("span");
  sw.className = "swatch";
  sw.style.background = seriesColor(dev.slot);
  title.append(sw, dev.name || dev.external_id);
  if (latest.online && latest.online.value === 0) {
    const off = document.createElement("span");
    off.className = "offline";
    off.textContent = "⦻ offline";
    title.append(off);
  }
  card.append(title);

  const model = document.createElement("div");
  model.className = "model";
  model.textContent = `${dev.source} · ${dev.model || ""}`;
  card.append(model);

  const wrap = document.createElement("div");
  wrap.className = "metrics";
  let newest = 0;
  const order = Object.keys(METRICS);
  const entries = Object.entries(latest).sort(([a], [b]) => {
    const rank = (m) =>
      m === "battery" ? 900 : order.includes(m) ? order.indexOf(m) : 500;
    return rank(a) - rank(b);
  });
  for (const [metric, entry] of entries) {
    newest = Math.max(newest, entry.ts);
    if (metric === "online" || metric === "powerSwitch") continue;
    if (metric === "colorTemperatureK" && entry.value === 0) continue;
    const div = document.createElement("div");
    div.className = "metric";
    if (metric === "colorRgb") {
      const hex = "#" + (entry.value >>> 0).toString(16).padStart(6, "0");
      div.innerHTML =
        `<div class="v"><span class="swatch" style="display:inline-block;background:${hex}"></span>` +
        ` <small>${hex}</small></div><div class="k">Color</div>`;
      wrap.append(div);
      continue;
    }
    const cfg = METRICS[metric] || CARD_METRICS[metric];
    const label = cfg ? cfg.label : metric;
    const unit = cfg ? cfg.unit : "";
    div.innerHTML =
      `<div class="v">${fmtValue(metric, entry.value)}<small> ${unit}</small></div>` +
      `<div class="k">${label}</div>`;
    wrap.append(div);
  }
  card.append(wrap);

  if (dev.source === "govee" && "powerSwitch" in latest) {
    const on = latest.powerSwitch.value >= 1;
    const pill = document.createElement("span");
    pill.className = "power-status" + (on ? " on" : " off");
    pill.innerHTML = `<span class="power-dot"></span>${on ? "On" : "Off"}`;
    card.append(pill);
  }

  const age = document.createElement("div");
  age.className = "age";
  age.textContent = newest ? `updated ${fmtAge(newest)}` : "no data yet";
  card.append(age);
  return card;
}

function chartOptions(unit, seriesCount, stepped) {
  const grid = cssVar("--grid");
  const muted = cssVar("--text-muted");
  const secondary = cssVar("--text-secondary");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: {
        display: seriesCount > 1,
        labels: { color: secondary, boxWidth: 12, boxHeight: 12, usePointStyle: true },
      },
      tooltip: {
        callbacks: {
          title: (items) => new Date(items[0].parsed.x).toLocaleString(),
          label: (item) =>
            ` ${item.dataset.label}: ${item.parsed.y}${unit ? " " + unit : ""}`,
        },
      },
    },
    scales: {
      x: {
        type: "linear",
        bounds: "data",
        grid: { display: false },
        border: { color: cssVar("--baseline") },
        ticks: { color: muted, maxTicksLimit: 8, callback: (v) => fmtTick(v) },
      },
      y: {
        grid: { color: grid },
        border: { display: false },
        ticks: { color: muted, ...(stepped ? { stepSize: 1 } : {}) },
        title: unit
          ? { display: true, text: unit, color: muted, font: { size: 11 } }
          : undefined,
      },
    },
  };
}

function tableView(metric, unit, series) {
  const details = document.createElement("details");
  details.className = "table-view";
  details.innerHTML = "<summary>Data table (latest 12 points)</summary>";
  const table = document.createElement("table");
  const header = ["Time", ...series.map((s) => `${s.name}${unit ? ` (${unit})` : ""}`)];
  table.innerHTML =
    "<tr>" + header.map((h) => `<th>${h}</th>`).join("") + "</tr>";
  const stamps = [...new Set(series.flatMap((s) => s.points.map((p) => p[0])))]
    .sort((a, b) => b - a).slice(0, 12);
  for (const ts of stamps) {
    const cells = series.map((s) => {
      const hit = s.points.find((p) => p[0] === ts);
      return hit ? fmtValue(metric, hit[1]) : "–";
    });
    const row = document.createElement("tr");
    row.innerHTML =
      `<td>${new Date(ts * 1000).toLocaleString()}</td>` +
      cells.map((c) => `<td>${c}</td>`).join("");
    table.append(row);
  }
  details.append(table);
  return details;
}

// Remaining device (non-environmental) metrics, charted before the divider.
const DEVICE_METRICS = ["energy_kwh", "powerSwitch"];
// Charted after the "Environmental information" divider. brightness is
// deliberately absent -- shown on the light's card tile, not charted.
const ENV_METRICS = ["temperature", "humidity", "co2", "pm25", "pm10", "tvoc", "pressure"];

async function fetchMetricSeries(metric) {
  const withMetric = devices.filter((d) => d.latest && metric in d.latest);
  if (!withMetric.length) return null;
  const series = await Promise.all(
    withMetric.map(async (d) => ({
      dev: d,
      name: d.name,
      points: await getJSON(
        `/api/history?device_id=${d.id}&metric=${metric}&hours=${rangeHours}`
      ),
    }))
  );
  const nonEmpty = series.filter((s) => s.points.length > 1);
  return nonEmpty.length ? nonEmpty : null;
}

function metricChartEls(metric, cfg, nonEmpty) {
  const heading = document.createElement("h2");
  heading.textContent = cfg.label + (cfg.unit ? ` (${cfg.unit})` : "");
  const wrap = document.createElement("div");
  wrap.className = "chart-wrap";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", `${cfg.label} over time`);
  wrap.append(canvas);
  const chart = new Chart(canvas, {
    type: "line",
    data: {
      datasets: nonEmpty.map((s) => ({
        label: s.name,
        data: s.points.map(([ts, v]) => ({ x: ts * 1000, y: v })),
        borderColor: seriesColor(s.dev.slot),
        backgroundColor: seriesColor(s.dev.slot),
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        stepped: !!cfg.stepped,
        tension: 0.15,
      })),
    },
    options: chartOptions(cfg.unit, nonEmpty.length, !!cfg.stepped),
  });
  charts.push(chart);
  return { heading, wrap, table: tableView(metric, cfg.unit, nonEmpty) };
}

async function renderPowerChart() {
  // All devices overlaid on one chart -- the all-up view that sits above
  // the per-plug energy breakdown.
  const section = $("#power-chart");
  const metric = "power";
  const nonEmpty = await fetchMetricSeries(metric);
  if (!nonEmpty) { section.hidden = true; return; }
  section.hidden = false;
  section.innerHTML = "";
  const { heading, wrap, table } = metricChartEls(metric, METRICS[metric], nonEmpty);
  section.append(heading, wrap, table);
}

async function renderCharts() {
  for (const c of charts) c.destroy();
  charts = [];

  await renderPowerChart();

  const box = $("#charts");
  box.innerHTML = "";

  for (const metric of DEVICE_METRICS) {
    const nonEmpty = await fetchMetricSeries(metric);
    if (!nonEmpty) continue;
    const card = document.createElement("div");
    card.className = "card chart-card";
    const { heading, wrap, table } = metricChartEls(metric, METRICS[metric], nonEmpty);
    card.append(heading, wrap, table);
    box.append(card);
  }

  const envCards = [];
  for (const metric of ENV_METRICS) {
    const nonEmpty = await fetchMetricSeries(metric);
    if (!nonEmpty) continue;
    const card = document.createElement("div");
    card.className = "card chart-card";
    const { heading, wrap, table } = metricChartEls(metric, METRICS[metric], nonEmpty);
    card.append(heading, wrap, table);
    envCards.push(card);
  }
  if (envCards.length) {
    const divider = document.createElement("h2");
    divider.className = "section-divider";
    divider.textContent = "Environmental information";
    box.append(divider, ...envCards);
  }

  if (!box.children.length) {
    box.innerHTML =
      `<div class="card empty">No time-series data yet — charts appear once the collector has stored readings.</div>`;
  }
}

let energyChart = null;
let energyBucket = "day";
let energyDeviceId = null;

function energyLabel(ts) {
  const d = new Date(ts * 1000);
  if (energyBucket === "hour")
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
           d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (energyBucket === "month")
    return d.toLocaleDateString([], { month: "short", year: "numeric" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

async function renderEnergy() {
  const section = $("#energy");
  const eligible = devices.filter(
    (d) => d.latest && ("power" in d.latest || "energy_kwh" in d.latest ||
                        d.source === "govee" && d.meta.type === "devices.types.socket")
  );
  if (!eligible.length) { section.hidden = true; return; }
  section.hidden = false;

  const select = $("#energy-device");
  if (select.options.length !== eligible.length) {
    select.innerHTML = eligible
      .map((d) => `<option value="${d.id}">${d.name}</option>`).join("");
    if (energyDeviceId) select.value = energyDeviceId;
  }
  energyDeviceId = Number(select.value || eligible[0].id);
  const device = devices.find((d) => d.id === energyDeviceId);

  const body = await getJSON(`/api/energy?device_id=${energyDeviceId}&bucket=${energyBucket}`);
  const totalEl = $("#energy-total");
  const unitWord = { hour: "hourly", day: "daily", week: "weekly", month: "monthly" }[energyBucket];
  totalEl.innerHTML = body.data.length
    ? `Total <strong>${body.total_kwh.toFixed(energyBucket === "hour" ? 3 : 1)} kWh</strong> · ` +
      `<strong>$${body.total_cost.toFixed(2)}</strong> (${unitWord}, shown span) ` +
      `<small class="rate-note">peak $${body.peak_rate.toFixed(5)}/kWh · off-peak $${body.offpeak_rate.toFixed(5)}/kWh</small>`
    : "no energy data yet";

  if (energyChart) { energyChart.destroy(); energyChart = null; }
  if (!body.data.length) { $("#energy-table").innerHTML = ""; return; }

  const color = seriesColor(device.slot);
  const muted = cssVar("--text-muted");
  energyChart = new Chart($("#energy-canvas"), {
    type: "bar",
    data: {
      labels: body.data.map(([ts]) => energyLabel(ts)),
      datasets: [{
        label: device.name,
        data: body.data.map(([, v]) => v),
        costs: body.data.map(([, , c]) => c),
        backgroundColor: color,
        borderRadius: 4,
        maxBarThickness: 26,
        categoryPercentage: 0.8,
        barPercentage: 0.9,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item) =>
              ` ${item.parsed.y.toFixed(3)} kWh · $${item.dataset.costs[item.dataIndex].toFixed(2)}`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: cssVar("--baseline") },
          ticks: { color: muted, maxTicksLimit: 14, maxRotation: 0, autoSkip: true },
        },
        y: {
          grid: { color: cssVar("--grid") },
          border: { display: false },
          ticks: { color: muted },
          title: { display: true, text: "kWh", color: muted, font: { size: 11 } },
        },
      },
    },
  });

  const tbl = $("#energy-table");
  tbl.innerHTML = "";
  const details = document.createElement("details");
  details.className = "table-view";
  details.innerHTML = "<summary>Data table (latest 12 buckets)</summary>";
  const table = document.createElement("table");
  table.innerHTML = "<tr><th>Period</th><th>kWh</th><th>Cost</th></tr>" +
    body.data.slice(-12).reverse()
      .map(([ts, v, c]) => `<tr><td>${energyLabel(ts)}</td><td>${v.toFixed(3)}</td><td>$${c.toFixed(2)}</td></tr>`)
      .join("");
  details.append(table);
  tbl.append(details);
}

$("#energy-bucket").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-bucket]");
  if (!btn) return;
  energyBucket = btn.dataset.bucket;
  for (const b of $("#energy-bucket").querySelectorAll("button"))
    b.classList.toggle("active", b === btn);
  renderEnergy();
});
$("#energy-device").addEventListener("change", () => {
  energyDeviceId = Number($("#energy-device").value);
  renderEnergy();
});

async function refresh() {
  try {
    const [status, devs] = await Promise.all([
      getJSON("/api/status"),
      getJSON("/api/devices"),
    ]);
    renderStatus(status);
    devices = devs.map((d, i) => ({ ...d, slot: i }));
    const cards = $("#cards");
    cards.innerHTML = "";
    for (const d of devices) cards.append(deviceCard(d));
    if (!devices.length) {
      cards.innerHTML = `<div class="card empty">No devices discovered yet.</div>`;
    }
    await renderEnergy();
    await renderCharts();
    $("#footer-note").textContent =
      `Local dashboard · SQLite at data/sensors.db · refreshed ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    $("#footer-note").textContent = `Refresh failed: ${e.message}`;
  }
}

$("#import-btn").addEventListener("click", () => $("#import-file").click());
$("#import-file").addEventListener("change", () => {
  const file = $("#import-file").files[0];
  if (!file) return;
  const panel = $("#import-panel");
  const options = devices
    .filter((d) => d.source === "govee")
    .map((d) => `<option value="${d.id}">${d.name}</option>`)
    .join("");
  panel.hidden = false;
  panel.innerHTML =
    `<span>Import <strong>${file.name}</strong> into:</span>` +
    `<select id="import-device">${options}</select>` +
    `<button class="toggle" id="import-go">Import</button>` +
    `<button class="toggle" id="import-cancel">Cancel</button>` +
    `<span id="import-result"></span>`;
  $("#import-cancel").onclick = () => { panel.hidden = true; $("#import-file").value = ""; };
  $("#import-go").onclick = async () => {
    const form = new FormData();
    form.append("file", file);
    form.append("device_id", $("#import-device").value);
    $("#import-result").textContent = "importing…";
    try {
      const r = await fetch("/api/import", { method: "POST", body: form });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || r.status);
      $("#import-result").textContent =
        `✓ ${body.readings_new} new readings (${body.metrics.join(", ")}) from ` +
        `${new Date(body.from * 1000).toLocaleDateString()} to ${new Date(body.to * 1000).toLocaleDateString()}`;
      refresh();
    } catch (e) {
      $("#import-result").textContent = `failed: ${e.message}`;
    }
  };
});

$("#range-picker").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-hours]");
  if (!btn) return;
  rangeHours = Number(btn.dataset.hours);
  for (const b of $("#range-picker").querySelectorAll("button"))
    b.classList.toggle("active", b === btn);
  renderCharts();
});

window
  .matchMedia("(prefers-color-scheme: dark)")
  .addEventListener("change", () => { renderEnergy(); renderCharts(); });

refresh();
setInterval(refresh, 60_000);
