/* Nimbus frontend — live dashboard, event injection, controller comparison,
   replay and quantitative evaluation. Prototype UI for a simulated island. */

const $ = (sel) => document.querySelector(sel);
const CONFIG = {
  controllers: {
    naive: { name: 'Naive', desc: 'Reacts only to battery percentage. No trajectory, no smooth control, no cooldown. Intentionally simple.' },
    reactive: { name: 'Reactive', desc: 'Battery + current net power with hysteresis. No velocity / acceleration early detection.' },
    nimbus: { name: 'Nimbus', desc: 'Early trajectory detection (velocity + acceleration), PD desalination control, priority-aware allocation, orderly restoration.' },
  },
};

const state = {
  connected: false,
  controller: 'nimbus',
  history: [],
  chart: null,
  lastEvent: null,
  lastSnapshot: null,
};

/* ------------------------------------------------------------------ setup */
function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws`;
}

function connect() {
  const ws = new WebSocket(wsUrl());
  ws.onopen = () => { state.connected = true; setStatus('SYSTEM STABLE', 'ok'); };
  ws.onclose = () => { state.connected = false; setStatus('RECONNECTING', 'warn'); setTimeout(connect, 1200); };
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.telemetry) handleTelemetry(data.telemetry);
      if (data.decision) renderWhy(data.decision);
    } catch (e) { /* ignore */ }
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

function setStatus(text, cls) {
  const badge = $('#statusBadge');
  $('#statusText').textContent = text;
  badge.className = 'status-badge ' + (cls || '');
}

/* ------------------------------------------------------------------ state */
function trajectoryState(snap) {
  const eb = snap.energy_balance;
  const bat = snap.battery.pct;
  const proj = eb.filtered_kw + eb.velocity_kw_s * 1.0 + 0.5 * eb.acceleration_kw_s2 * 0.36;
  if (eb.filtered_kw < -80 || proj < -120 || bat < 25) return { cls: 'crit', text: 'CRITICAL — PROTECTING SERVICES' };
  if (eb.velocity_kw_s < -8 || eb.filtered_kw < -25) return { cls: 'watch', text: 'DETERIORATING' };
  if (eb.acceleration_kw_s2 < -8) return { cls: 'watch', text: 'WATCHING TRAJECTORY' };
  return { cls: 'stable', text: 'STABLE' };
}

function handleTelemetry(snap) {
  state.lastSnapshot = snap;
  state.controller = snap.controller || state.controller;
  $('#controllerLabel').textContent = CONFIG.controllers[state.controller]?.name || state.controller;
  $('#controllerDesc').textContent = CONFIG.controllers[state.controller]?.desc || '';
  syncControllerUI();

  // generation / demand gauges
  const gen = snap.generation.solar_kw + snap.generation.wind_kw;
  $('#genNum').textContent = Math.round(gen);
  $('#demNum').textContent = Math.round(snap.demand.total_kw);

  // battery
  $('#batteryPct').textContent = Math.round(snap.battery.pct) + '%';
  $('#batteryFill').style.width = snap.battery.pct + '%';

  // stability
  const eb = snap.energy_balance;
  $('#netKv').textContent = (eb.net_kw >= 0 ? '+' : '') + eb.net_kw.toFixed(0);
  $('#velValue').textContent = (eb.velocity_kw_s >= 0 ? '+' : '') + eb.velocity_kw_s.toFixed(1);
  $('#accelValue').textContent = (eb.acceleration_kw_s2 >= 0 ? '+' : '') + eb.acceleration_kw_s2.toFixed(1);
  $('#netKv').style.color = eb.net_kw < -40 ? 'var(--red)' : eb.net_kw < -10 ? 'var(--amber)' : 'var(--green)';
  $('#velValue').style.color = eb.velocity_kw_s < -12 ? 'var(--red)' : eb.velocity_kw_s < -5 ? 'var(--amber)' : 'var(--green)';
  $('#accelValue').style.color = eb.acceleration_kw_s2 < -15 ? 'var(--red)' : eb.acceleration_kw_s2 < -6 ? 'var(--amber)' : 'var(--green)';

  const traj = trajectoryState(snap);
  const trajEl = $('#trajectory');
  trajEl.className = 'trajectory ' + traj.cls;
  trajEl.textContent = traj.text;

  // header status reflects severity
  if (snap.event !== 'none') setStatus(snap.event.toUpperCase().replace('_', ' '), traj.cls);
  else setStatus('SYSTEM STABLE', 'ok');

  // resources
  renderResources(snap.resources);

  // chart
  state.history.push(snap);
  if (state.history.length > 160) state.history = state.history.slice(-160);
  updateChart();
}

function renderResources(resources) {
  const colors = {
    hospital: '#34d399', desalination: '#22d3ee',
    residential: '#818cf8', resort: '#fbbf24',
  };
  const icons = { hospital: '🏥', desalination: '💧', residential: '🏠', resort: '🌴' };
  const critLabel = { hospital: 'CRITICAL', desalination: 'VERY HIGH', residential: 'MEDIUM', resort: 'LOWEST' };
  $('#resourceList').innerHTML = resources.map((r) => {
    const stateCls = (r.state || 'normal').toLowerCase();
    return `
      <div class="resource">
        <div class="resource-head">
          <div class="resource-name">${icons[r.id]} ${r.name.toUpperCase()}</div>
          <div class="resource-pct">${Math.round(r.operating_pct)}%</div>
        </div>
        <div class="resource-bar"><div class="resource-fill" style="width:${r.operating_pct}%;background:${colors[r.id]}"></div></div>
        <div class="resource-meta">
          <span><span class="crit-chip">${critLabel[r.id]}</span> · ${Math.round(r.actual_kw)} kW</span>
          <span class="resource-state ${stateCls}">${stateLabel(r.state)}</span>
        </div>
      </div>`;
  }).join('');
}

function stateLabel(s) {
  return ({ PROTECTED: '● PROTECTED', NORMAL: '● NORMAL', THROTTLED: '● THROTTLED',
            REDUCED: '● REDUCED', SHED: '● SHED', COOLDOWN: '● COOLING DOWN' })[s] || s;
}

/* ------------------------------------------------------------------ chart */
function makeChart() {
  const ctx = $('#energyChart').getContext('2d');
  state.chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'Solar', data: [], borderColor: '#fbbf24', backgroundColor: 'rgba(251,191,36,.15)', fill: false, borderWidth: 2, pointRadius: 0, tension: .3 },
        { label: 'Wind', data: [], borderColor: '#38bdf8', backgroundColor: 'rgba(56,189,248,.15)', fill: false, borderWidth: 2, pointRadius: 0, tension: .3 },
        { label: 'Total demand', data: [], borderColor: '#f472b6', backgroundColor: 'rgba(244,114,182,.12)', fill: true, borderWidth: 2, pointRadius: 0, tension: .3 },
        { label: 'Net power', data: [], borderColor: '#34d399', borderWidth: 2, pointRadius: 0, tension: .3, fill: false },
        { label: 'Battery %', data: [], borderColor: '#818cf8', borderWidth: 1.5, pointRadius: 0, yAxisID: 'y1', tension: .3, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: true, animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#8ba0c0', boxWidth: 12, font: { size: 10 } } } },
      scales: {
        x: { ticks: { color: '#5b6f92', maxTicksLimit: 12, font: { size: 10 } }, grid: { color: 'rgba(30,44,77,.5)' } },
        y: { ticks: { color: '#5b6f92', font: { size: 10 } }, grid: { color: 'rgba(30,44,77,.5)' }, title: { display: true, text: 'kW', color: '#5b6f92' } },
        y1: { position: 'right', min: 0, max: 100, ticks: { color: '#5b6f92', font: { size: 10 } }, grid: { drawOnChartArea: false }, title: { display: true, text: 'Battery %', color: '#5b6f92' } },
      },
    },
  });
}

function updateChart() {
  if (!state.chart) return;
  const h = state.history;
  const labels = h.map((s) => s.time_s.toFixed(1));
  state.chart.data.labels = labels;
  state.chart.data.datasets[0].data = h.map((s) => s.generation.solar_kw);
  state.chart.data.datasets[1].data = h.map((s) => s.generation.wind_kw);
  state.chart.data.datasets[2].data = h.map((s) => s.demand.total_kw);
  state.chart.data.datasets[3].data = h.map((s) => s.energy_balance.net_kw);
  state.chart.data.datasets[4].data = h.map((s) => s.battery.pct);
  state.chart.update('none');
}

/* ------------------------------------------------------------------ WHY */
function renderWhy(decision) {
  if (!decision) return;
  const effects = (decision.effects || []).map((e) => `<li>${e}</li>`).join('');
  $('#whyPanel').innerHTML = `
    <div class="why-title">${decision.title || 'NIMBUS DECISION'}</div>
    <div class="why-reason">${decision.reason || ''}</div>
    ${effects ? `<ul class="why-effects">${effects}</ul>` : ''}
    <div class="why-ctx">Expected result: maintain critical services while preventing battery depletion.</div>`;
}

/* ------------------------------------------------------------------ events */
function resetSliderUI() {
  document.querySelectorAll('#sliders input[type=range]').forEach((i) => {
    i.value = 0;
    $(`[data-val="${i.dataset.key}"]`).textContent = 'auto';
  });
}

function initEvents() {
  document.querySelectorAll('.btn.event').forEach((btn) => {
    btn.addEventListener('click', () => {
      const code = btn.dataset.event;
      state.lastEvent = { code, name: btn.textContent.trim() };
      renderReplay();
      // Manual overrides are cleared server-side on event injection; also
      // reset the slider UI so it reflects "auto" again.
      resetSliderUI();
      api('/api/event', 'POST', { code }).then(() => {});
    });
  });
}

/* ------------------------------------------------------------------ controller */
function initController() {
  document.querySelectorAll('.ctrl-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ctrl = btn.dataset.ctrl;
      if (state.lastEvent) {
        // re-run the last event for a fair comparison under this controller
        api('/api/controller', 'POST', { controller: ctrl }).then(() => {
          setTimeout(() => api('/api/event', 'POST', { code: state.lastEvent.code }), 200);
        });
      } else {
        api('/api/controller', 'POST', { controller: ctrl });
      }
    });
  });
}

function syncControllerUI() {
  document.querySelectorAll('.ctrl-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.ctrl === state.controller);
  });
}

/* ------------------------------------------------------------------ replay */
function renderReplay() {
  const area = $('#replayArea');
  if (!state.lastEvent) {
    area.innerHTML = '<div class="replay-empty">Run an event, then replay it under any controller for a fair comparison.</div>';
    return;
  }
  area.innerHTML = `
    <div class="replay-row">
      <span class="event-name">⏱ LAST EVENT: ${state.lastEvent.name}</span>
    </div>
    <div class="replay-empty" style="margin-top:6px">
      Replay <strong>${state.lastEvent.name}</strong> under each controller via the CONTROLLER panel —
      identical disturbance, same starting state, side by side.
    </div>`;
}

/* ------------------------------------------------------------------ reset */
function initReset() {
  $('#resetBtn').addEventListener('click', () => {
    api('/api/reset', 'POST', { controller: state.controller }).then(() => {});
  });
}

/* ------------------------------------------------------------------ sliders */
function initSliders() {
  const defs = [
    { key: 'solar', label: 'Solar', min: 0, max: 300, step: 5 },
    { key: 'wind', label: 'Wind', min: 0, max: 190, step: 5 },
    { key: 'battery_pct', label: 'Battery', min: 0, max: 100, step: 1 },
    { key: 'residential', label: 'Residential', min: 0, max: 200, step: 5 },
    { key: 'desalination', label: 'Desalination', min: 0, max: 200, step: 5 },
    { key: 'resort', label: 'Resort', min: 0, max: 150, step: 5 },
  ];
  $('#sliders').innerHTML = defs.map((d) => `
    <div class="slider-row">
      <label>${d.label}</label>
      <input type="range" min="${d.min}" max="${d.max}" step="${d.step}" data-key="${d.key}" value="0" />
      <span class="slider-val" data-val="${d.key}">auto</span>
    </div>`).join('');

  $('#sliders').querySelectorAll('input[type=range]').forEach((input) => {
    input.addEventListener('input', () => {
      const v = parseFloat(input.value);
      const key = input.dataset.key;
      const valSpan = $(`[data-val="${key}"]`);
      valSpan.textContent = input.value + (key === 'battery_pct' ? '%' : ' kW');
      api('/api/sliders', 'POST', { [key]: v });
    });
  });

  $('#clearSliders').addEventListener('click', () => {
    api('/api/sliders/clear', 'POST').then(() => {
      $('#sliders').querySelectorAll('input[type=range]').forEach((i) => {
        i.value = 0;
        $(`[data-val="${i.dataset.key}"]`).textContent = 'auto';
      });
    });
  });
}

/* ------------------------------------------------------------------ eval */
function initEval() {
  $('#evalBtn').addEventListener('click', async () => {
    const btn = $('#evalBtn');
    btn.disabled = true; btn.textContent = 'RUNNING…';
    try {
      const res = await api('/api/evaluate?n=120&seed=7');
      renderEval(res);
    } finally {
      btn.disabled = false; btn.textContent = 'RUN 120-SCENARIO EVAL';
    }
  });
}

function renderEval(res) {
  const rows = res.rows || [];
  const order = ['nimbus', 'reactive', 'naive'];
  const ordered = order.map((n) => rows.find((r) => r.controller === n)).filter(Boolean);
  if (!ordered.length) return;

  const pct = (v) => (v * 100).toFixed(1) + '%';
  const fmtLoad = (v) => Math.round(v) + ' kWh';
  const fmtRec = (v) => v.toFixed(1) + ' s';
  const fmtOsc = (v) => v.toFixed(1);

  const metricRows = [
    { label: 'Critical service uptime', get: (r) => r.critical_uptime, fmt: pct, maximize: true },
    { label: 'Water availability', get: (r) => r.water_availability, fmt: pct, maximize: true },
    { label: 'Load shed (disruption)', get: (r) => r.total_load_shed_kwh, fmt: fmtLoad, maximize: false },
    { label: 'Recovery time', get: (r) => r.recovery_time_s, fmt: fmtRec, maximize: false },
    { label: 'Oscillation', get: (r) => r.instability, fmt: fmtOsc, maximize: false },
    { label: 'Min battery', get: (r) => r.min_battery_pct / 100, fmt: pct, maximize: true },
  ];

  const tableRows = metricRows.map((m) => {
    const vals = ordered.map(m.get);
    const target = m.maximize ? Math.max(...vals) : Math.min(...vals);
    const tds = ordered.map((r, i) => {
      const isBest = Math.abs(vals[i] - target) < 1e-9;
      return `<td class="${isBest ? 'best' : ''}">${m.fmt(vals[i])}</td>`;
    }).join('');
    return `<tr><td>${m.label}</td>${tds}</tr>`;
  }).join('');

  const scoreVals = ordered.map((r) => r.score);
  const bestScore = Math.max(...scoreVals);
  const scoreRow = `<tr><td>NIMBUS SCORE</td>${ordered.map((r, i) =>
    `<td class="${scoreVals[i] === bestScore ? 'best' : ''}">${r.score.toFixed(1)}</td>`).join('')}</tr>`;

  // bar chart for scores
  const bars = ordered.map((r, i) => {
    const w = Math.max(2, (r.score / bestScore) * 100);
    const color = ['#22d3ee', '#6366f1', '#f87171'][order.indexOf(r.controller)];
    return `
      <div class="eval-bar-row">
        <div class="eval-bar-label"><span>${CONFIG.controllers[r.controller].name}</span><span>${r.score.toFixed(1)}</span></div>
        <div class="eval-bar-track"><div class="eval-bar-fill" style="width:${w}%;background:${color}"></div></div>
      </div>`;
  }).join('');

  $('#evalPanel').innerHTML = `
    <div class="eval-table-wrap">
      <table class="eval">
        <tr><th>Metric</th><th>Naive</th><th>Reactive</th><th>Nimbus</th></tr>
        ${tableRows}
        ${scoreRow}
      </table>
    </div>
    <div class="eval-bars">${bars}</div>
    <div class="eval-note">
      Averaged over <strong>${res.n_scenarios}</strong> randomized disturbances (storm severity, solar/wind availability, demand, initial battery, event duration, demand spikes and recovery speed). Numbers come directly from the simulation. The Nimbus score is a <strong>prototype</strong> evaluation metric with configurable weighting — not a claim of scientific optimality. Green = best.
    </div>`;
}

/* ------------------------------------------------------------------ boot */
function boot() {
  initEvents();
  initController();
  initReset();
  initSliders();
  initEval();
  makeChart();
  renderReplay();
  connect();
}

document.addEventListener('DOMContentLoaded', boot);
