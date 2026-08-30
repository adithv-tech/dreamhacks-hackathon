/* Nimbus frontend — Apple-style live dashboard.
   Prototype UI for a simulated island microgrid. */

const $ = (s) => document.querySelector(s);

const CONTROLLERS = {
  naive: { name: 'Naive', desc: '<strong>Naive</strong> reacts only to battery percentage — no trajectory, no smooth control, no cooldown.' },
  reactive: { name: 'Reactive', desc: '<strong>Reactive</strong> uses battery and current net power with hysteresis, but no velocity / acceleration early detection.' },
  nimbus: { name: 'Nimbus', desc: '<strong>Nimbus</strong> reads the energy-balance trajectory (velocity + acceleration) to act early, allocates by priority, and restores order.' },
};

const state = {
  controller: 'nimbus',
  history: [],
  chart: null,
  lastEvent: null,
};

/* ----------------------------- icon helper ----------------------------- */
/* Consistent Lucide-style stroke icons (self-hosted paths). */
const ICON_PATHS = {
  'shield-plus': '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="M9 12h6"/><path d="M12 9v6"/>',
  droplet: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
  home: '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  palmtree: '<path d="M13 8c0-2.76-2.46-5-5.5-5S2 5.24 2 8h2l1-1 1 1h4"/><path d="M13 7.14A5.82 5.82 0 0 1 16.5 6c3.04 0 5.5 2.24 5.5 5h-3l-1-1-1 1h-3"/><path d="M5.89 9.71c-2.15 2.15-2.3 5.47-.35 7.43l4.24-4.25.7-.7.71-.71 2.12-2.12c-1.95-1.96-5.27-1.8-7.42.35"/><path d="M11 15.5c.5 2.5-.17 4.5-1 6.5h4c2-5.5-.5-12-1-14"/>',
};
function icon(name, size = 18) {
  const path = ICON_PATHS[name];
  return `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

/* ------------------------------- setup -------------------------------- */
function wsUrl() {
  const p = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${p}://${location.host}/ws`;
}

function connect() {
  const ws = new WebSocket(wsUrl());
  ws.onopen = () => setStatus('System stable', 'ok');
  ws.onclose = () => { setStatus('Reconnecting…', ''); setTimeout(connect, 1200); };
  ws.onmessage = (ev) => {
    try {
      const d = JSON.parse(ev.data);
      // Each concern is isolated so one failure never blocks the others.
      if (d.telemetry) { try { handleTelemetry(d.telemetry); } catch (e) { console.error('telemetry', e); } }
      if (d.decision) { try { renderWhy(d.decision); } catch (e) { console.error('decision', e); } }
    } catch (e) { /* ignore malformed frame */ }
  };
  window._ws = ws;
}

function api(path, method = 'GET', body) {
  return fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then((r) => r.json());
}

/* ---------------------------- status & verdict ------------------------- */
function setStatus(text, cls) {
  const badge = $('#statusBadge');
  $('#statusText').textContent = text;
  badge.className = 'status-pill ' + (cls || '');
}

function trajectoryState(snap) {
  const eb = snap.energy_balance;
  const bat = snap.battery.pct;
  const proj = eb.filtered_kw + eb.velocity_kw_s * 1.0 + 0.5 * eb.acceleration_kw_s2 * 0.36;
  if (eb.filtered_kw < -80 || proj < -120 || bat < 25) return { cls: 'crit', text: 'Critical — protecting services', note: 'Energy balance is falling fast. Critical services are being safeguarded.' };
  if (eb.velocity_kw_s < -8 || eb.filtered_kw < -25) return { cls: 'watch', text: 'Deteriorating', note: 'The energy balance is heading in the wrong direction.' };
  if (eb.acceleration_kw_s2 < -8) return { cls: 'watch', text: 'Watching trajectory', note: 'A rapid change has been detected — Nimbus is tracking it.' };
  return { cls: 'stable', text: 'System stable', note: 'Generation is covering demand. Battery is healthy.' };
}

function handleTelemetry(snap) {
  state.controller = snap.controller || state.controller;
  syncSegUI();
  const eb = snap.energy_balance;
  const gen = snap.generation.solar_kw + snap.generation.wind_kw;

  // metrics
  $('#genValue').textContent = Math.round(gen).toLocaleString();
  $('#genSub').textContent = `Solar ${Math.round(snap.generation.solar_kw)} · Wind ${Math.round(snap.generation.wind_kw)}`;
  $('#demValue').textContent = Math.round(snap.demand.total_kw).toLocaleString();
  $('#demSub').textContent = `Hospital ${Math.round(snap.demand.hospital_kw)} · Water ${Math.round(snap.demand.desalination_kw)}`;
  $('#batteryPct').textContent = Math.round(snap.battery.pct) + '%';
  $('#batteryKwh').textContent = Math.round(snap.battery.kwh).toLocaleString() + ' kWh';
  $('#batteryRing').style.setProperty('--pct', snap.battery.pct + '%');
  $('#batteryRate').textContent = batteryRateLabel(snap);

  // trajectory stats
  $('#netValue').textContent = signed(eb.net_kw, 0);
  $('#velValue').textContent = signed(eb.velocity_kw_s, 1);
  $('#accelValue').textContent = signed(eb.acceleration_kw_s2, 1);
  colorNum('#netValue', eb.net_kw, -40, -10);
  colorNum('#velValue', eb.velocity_kw_s, -12, -5);
  colorNum('#accelValue', eb.acceleration_kw_s2, -15, -6);

  // verdict
  const traj = trajectoryState(snap);
  const verdict = $('#trajectory');
  verdict.className = 'verdict ' + traj.cls;
  verdict.querySelector('.verdict-text').textContent = traj.text;
  $('#verdictNote').textContent = traj.note;

  // header status + event chip
  if (snap.event !== 'none') {
    setStatus(snap.event.toUpperCase().replace('_', ' '), traj.cls === 'crit' ? 'crit' : traj.cls);
    showEventChip(snap.event, traj.cls);
  } else {
    setStatus('System stable', 'ok');
    hideEventChip();
  }

  renderResources(snap.resources);

  // chart
  state.history.push(snap);
  if (state.history.length > 150) state.history = state.history.slice(-150);
  updateChart();
}

function batteryRateLabel(snap) {
  const p = snap.energy_balance.battery_power_kw;
  if (p > 0.5) return 'Charging · +' + Math.round(p) + ' kW';
  if (p < -0.5) return 'Discharging · ' + Math.round(p) + ' kW';
  return 'Idle';
}

function signed(v, dec) {
  const s = (v >= 0 ? '+' : '') + v.toFixed(dec);
  return s;
}

function colorNum(sel, v, lo, mid) {
  const el = $(sel);
  el.style.color = v < lo ? 'var(--red-ink)' : v < mid ? 'var(--amber-ink)' : 'var(--text-1)';
}

/* ------------------------------- resources ----------------------------- */
function renderResources(resources) {
  const colors = { hospital: '#30d158', desalination: '#0a84ff', residential: '#8a5cf6', resort: '#ff9f0a' };
  const icons = { hospital: 'shield-plus', desalination: 'droplet', residential: 'home', resort: 'palmtree' };
  const crit = { hospital: 'Critical', desalination: 'Very high', residential: 'Medium', resort: 'Lowest' };
  $('#resourceList').innerHTML = resources.map((r) => {
    const cls = (r.state || 'normal').toLowerCase();
    return `
      <div class="resource">
        <div class="resource-head">
          <div class="resource-name">${icon(icons[r.id] || 'home')} ${r.name}</div>
          <div class="resource-pct">${Math.round(r.operating_pct)}%</div>
        </div>
        <div class="resource-bar"><div class="resource-fill" style="width:${r.operating_pct}%;background:${colors[r.id]}"></div></div>
        <div class="resource-meta">
          <span class="crit-chip">${crit[r.id]} · ${Math.round(r.actual_kw)} kW</span>
          <span class="resource-state ${cls}">${stateLabel(r.state)}</span>
        </div>
      </div>`;
  }).join('');
}

function stateLabel(s) {
  return ({ PROTECTED: 'Protected', NORMAL: 'Normal', THROTTLED: 'Throttled',
            REDUCED: 'Reduced', SHED: 'Shed', COOLDOWN: 'Recovering' })[s] || s;
}

/* ------------------------------- chart -------------------------------- */
function makeChart() {
  // If the chart library or a canvas is unavailable, fail gracefully instead of
  // breaking the rest of the dashboard.
  if (typeof Chart === 'undefined') return;
  const canvas = $('#energyChart');
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  try {
    state.chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'Solar', data: [], borderColor: '#ff9f0a', borderWidth: 2, pointRadius: 0, tension: .35, fill: false },
        { label: 'Wind', data: [], borderColor: '#64d2ff', borderWidth: 2, pointRadius: 0, tension: .35, fill: false },
        { label: 'Demand', data: [], borderColor: '#ff5f6d', borderWidth: 2, pointRadius: 0, tension: .35, fill: false },
        { label: 'Net balance', data: [], borderColor: '#30d158', borderWidth: 2.5, pointRadius: 0, tension: .35, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#86868b', boxWidth: 8, boxHeight: 8, usePointStyle: true, padding: 18, font: { size: 12, weight: 500 } } },
      },
      scales: {
        x: { ticks: { color: '#a1a1a6', maxTicksLimit: 10, font: { size: 11 } }, grid: { color: 'rgba(0,0,0,0.05)' }, border: { display: false } },
        y: { ticks: { color: '#a1a1a6', font: { size: 11 }, padding: 6 }, grid: { color: 'rgba(0,0,0,0.06)' }, border: { display: false }, title: { display: false } },
      },
    },
    });
  } catch (e) {
    // Chart could not be built (e.g. canvas unavailable); leave it null so
    // updateChart() is a safe no-op and the rest of the UI keeps working.
    state.chart = null;
  }
}

function updateChart() {
  if (!state.chart) return;
  const h = state.history;
  state.chart.data.labels = h.map((s) => s.time_s.toFixed(0));
  state.chart.data.datasets[0].data = h.map((s) => s.generation.solar_kw);
  state.chart.data.datasets[1].data = h.map((s) => s.generation.wind_kw);
  state.chart.data.datasets[2].data = h.map((s) => s.demand.total_kw);
  state.chart.data.datasets[3].data = h.map((s) => s.energy_balance.net_kw);
  state.chart.update('none');
}

/* ------------------------------- why ---------------------------------- */
function renderWhy(d) {
  if (!d) return;
  const effects = (d.effects || []).map((e) => `<li>${e}</li>`).join('');
  $('#whyPanel').innerHTML = `
    <div class="why-title">${d.title || 'Nimbus decision'}</div>
    <div class="why-reason">${d.reason || ''}</div>
    ${effects ? `<ul class="why-effects">${effects}</ul>` : ''}
    <div class="why-ctx">Expected result: keep critical services running and prevent battery depletion.</div>`;
}

/* ------------------------------- events ------------------------------- */
function initEvents() {
  document.querySelectorAll('.event-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const code = btn.dataset.event;
      state.lastEvent = { code, name: btn.querySelector('.ev-name').textContent };
      renderReplay();
      resetSliderUI();
      api('/api/event', 'POST', { code });
    });
  });
}

function showEventChip(code, cls) {
  const chip = $('#eventChip');
  chip.hidden = false;
  chip.textContent = code.toUpperCase().replace('_', ' ');
  chip.className = 'event-chip ' + (cls === 'stable' ? 'watch' : cls);
}
function hideEventChip() { $('#eventChip').hidden = true; }

/* ------------------------------ controller ---------------------------- */
function initController() {
  document.querySelectorAll('.seg-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ctrl = btn.dataset.ctrl;
      if (state.lastEvent) {
        api('/api/controller', 'POST', { controller: ctrl }).then(() => {
          setTimeout(() => api('/api/event', 'POST', { code: state.lastEvent.code }), 200);
        });
      } else {
        api('/api/controller', 'POST', { controller: ctrl });
      }
    });
  });
}

function syncSegUI() {
  document.querySelectorAll('.seg-btn').forEach((b) => b.classList.toggle('active', b.dataset.ctrl === state.controller));
  $('#controllerDesc').innerHTML = CONTROLLERS[state.controller].desc;
  $('#whyController').textContent = CONTROLLERS[state.controller].name;
}

/* ------------------------------- replay ------------------------------- */
function renderReplay() {
  const area = $('#replayArea');
  if (!state.lastEvent) {
    area.innerHTML = '<p class="placeholder">Run a disturbance, then switch controllers to replay it identically.</p>';
    return;
  }
  area.innerHTML = `
    <div class="replay-row">
      <span class="event-name">⚡ ${state.lastEvent.name}</span>
      <span class="tag">last event</span>
    </div>
    <p class="replay-hint">Use the controller switch above to replay <strong>${state.lastEvent.name}</strong> under Naive, Reactive or Nimbus — identical disturbance, same start.</p>`;
}

/* ------------------------------- reset -------------------------------- */
function initReset() {
  $('#resetBtn').addEventListener('click', () => api('/api/reset', 'POST', { controller: state.controller }));
}

/* ------------------------------ sliders ------------------------------- */
function initSliders() {
  const defs = [
    { key: 'solar', label: 'Solar', min: 0, max: 300, step: 5, unit: ' kW' },
    { key: 'wind', label: 'Wind', min: 0, max: 190, step: 5, unit: ' kW' },
    { key: 'battery_pct', label: 'Battery', min: 0, max: 100, step: 1, unit: '%' },
    { key: 'residential', label: 'Residential', min: 0, max: 200, step: 5, unit: ' kW' },
    { key: 'desalination', label: 'Desalination', min: 0, max: 200, step: 5, unit: ' kW' },
    { key: 'resort', label: 'Resort', min: 0, max: 150, step: 5, unit: ' kW' },
  ];
  $('#sliders').innerHTML = defs.map((d) => `
    <div class="slider-row">
      <label>${d.label}</label>
      <input type="range" min="${d.min}" max="${d.max}" step="${d.step}" data-key="${d.key}" value="0" />
      <span class="slider-val" data-val="${d.key}">auto</span>
    </div>`).join('');

  $('#sliders').querySelectorAll('input[type=range]').forEach((input) => {
    input.addEventListener('input', () => {
      const key = input.dataset.key;
      $(`[data-val="${key}"]`).textContent = input.value + (defs.find((d) => d.key === key).unit);
      api('/api/sliders', 'POST', { [key]: parseFloat(input.value) });
    });
  });
  $('#clearSliders').addEventListener('click', () => {
    api('/api/sliders/clear', 'POST').then(() => resetSliderUI());
  });
}

function resetSliderUI() {
  document.querySelectorAll('#sliders input[type=range]').forEach((i) => {
    i.value = 0;
    $(`[data-val="${i.dataset.key}"]`).textContent = 'auto';
  });
}

/* ------------------------------ evaluation ---------------------------- */
function initEval() {
  $('#evalBtn').addEventListener('click', async () => {
    const btn = $('#evalBtn');
    btn.disabled = true; btn.textContent = 'Running…';
    try {
      renderEval(await api('/api/evaluate?n=120&seed=7'));
    } finally {
      btn.disabled = false; btn.textContent = 'Run 120 scenarios';
    }
  });
}

function renderEval(res) {
  const order = ['nimbus', 'reactive', 'naive'];
  const rows = order.map((n) => (res.rows || []).find((r) => r.controller === n)).filter(Boolean);
  if (!rows.length) return;

  const pct = (v) => (v * 100).toFixed(1) + '%';
  const metricRows = [
    { label: 'Critical uptime', get: (r) => r.critical_uptime, fmt: pct, max: true },
    { label: 'Water availability', get: (r) => r.water_availability, fmt: pct, max: true },
    { label: 'Load shed', get: (r) => r.total_load_shed_kwh, fmt: (v) => Math.round(v) + ' kWh', max: false },
    { label: 'Recovery', get: (r) => r.recovery_time_s, fmt: (v) => v.toFixed(1) + ' s', max: false },
    { label: 'Oscillation', get: (r) => r.instability, fmt: (v) => v.toFixed(1), max: false },
    { label: 'Min battery', get: (r) => r.min_battery_pct / 100, fmt: pct, max: true },
  ];

  const head = '<tr><th>Metric</th><th>Naive</th><th>Reactive</th><th>Nimbus</th></tr>';
  const body = metricRows.map((m) => {
    const vals = rows.map(m.get);
    const target = m.max ? Math.max(...vals) : Math.min(...vals);
    const tds = rows.map((r, i) => {
      const best = Math.abs(vals[i] - target) < 1e-9;
      return `<td class="${best ? 'best' : ''}">${m.fmt(vals[i])}</td>`;
    }).join('');
    return `<tr><td>${m.label}</td>${tds}</tr>`;
  }).join('');

  const scoreVals = rows.map((r) => r.score);
  const bestScore = Math.max(...scoreVals);
  const scoreRow = `<tr><td>Nimbus score</td>${rows.map((r, i) =>
    `<td class="${scoreVals[i] === bestScore ? 'best' : ''}">${r.score.toFixed(1)}</td>`).join('')}</tr>`;

  const bars = order.map((n) => {
    const r = rows.find((x) => x.controller === n);
    if (!r) return '';
    const color = n === 'nimbus' ? '#40c8ff' : n === 'reactive' ? '#0a84ff' : 'rgba(255,255,255,0.3)';
    return `
      <div class="eval-bar-row">
        <div class="eval-bar-label"><span>${CONTROLLERS[n].name}${r.score === bestScore ? ' · best' : ''}</span><span>${r.score.toFixed(1)}</span></div>
        <div class="eval-bar-track"><div class="eval-bar-fill" style="width:${Math.max(2, (r.score / bestScore) * 100)}%;background:${color}"></div></div>
      </div>`;
  }).join('');

  $('#evalPanel').innerHTML = `
    <div class="eval-table-wrap"><table class="eval">${head}${body}${scoreRow}</table></div>
    <div class="eval-bars">${bars}</div>
    <p class="eval-note">Averaged over ${res.n_scenarios} randomized disturbances. Numbers come directly from the simulation. The Nimbus score is a <strong>prototype</strong> metric with configurable weighting. Green = best.</p>`;
}

/* -------------------------------- boot -------------------------------- */
function boot() {
  // Each initializer is isolated so a failure in one (e.g. the chart) never
  // prevents the live telemetry connection from starting.
  [initEvents, initController, initReset, initSliders, initEval,
   makeChart, renderReplay, syncSegUI].forEach((fn) => {
    try { fn(); } catch (e) { console.error('init', fn.name, e); }
  });
  connect();
}

document.addEventListener('DOMContentLoaded', boot);
