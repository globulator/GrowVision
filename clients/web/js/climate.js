// clients/web/js/climate.js
// Панель управления поливом, вентиляцией, pH и мониторинга ESP

import { api } from './api.js';
import { Modal, showToast } from './dialogs.js';
import { pollingCoordinator } from './components/polling.js';
import {
  createSvgElement,
  renderSvgActivityChart,
  renderSvgLineChart,
} from './components/svg_chart.js';

const CLIMATE_MODAL_POLL_TASK = 'climate-modal-refresh';
const CLIMATE_HEADER_POLL_TASK = 'climate-header-esp';

let currentRole = 'guest';
let currentPeriod = '24h';
let currentMode = 'manual';
let scheduleDirty = false;
let climateModalCtrl = null;

function getClimateModalCtrl() {
  if (!climateModalCtrl) {
    climateModalCtrl = new Modal('modal-climate', {
      closeBtn: 'modal-climate-close',
      onClose: () => {
        pollingCoordinator.stop(CLIMATE_MODAL_POLL_TASK);
        showClimateError('');
      },
    });
  }
  return climateModalCtrl;
}

function canControl() {
  return currentRole === 'biologist' || currentRole === 'developer';
}

function showClimateError(msg) {
  const el = document.getElementById('climate-error');
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
  } else {
    el.textContent = '';
    el.classList.add('hidden');
  }
}

function formatLocalTime(isoStr) {
  if (!isoStr) return 'нет данных';
  const dt = new Date(isoStr);
  if (Number.isNaN(dt.getTime())) return 'нет данных';
  return dt.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function renderLineChart(svgId, points, { color, unit }) {
  const normalizedPoints = Array.isArray(points)
    ? points
        .map((p) => ({ ...p, y: Number(p.value) }))
        .filter((p) => Number.isFinite(p.y))
    : [];

  renderSvgLineChart(svgId, {
    width: 360,
    height: 120,
    padL: 36,
    padR: 10,
    padT: 12,
    padB: 18,
    emptyText: 'Нет данных за выбранный период',
    yAxis: { steps: 2, paddingRatio: 0.12, minSpan: 0.01 },
    series: [
      {
        color,
        strokeWidth: 2,
        fillArea: true,
        fillOpacity: 0.14,
        points: normalizedPoints,
      },
    ],
    onPointElement: (circle, { point }) => {
      const tip = createSvgElement('title');
      tip.textContent = `${formatLocalTime(point.timestamp)}: ${Number(point.value).toFixed(1)}${unit}`;
      circle.appendChild(tip);
    },
  });
}

function renderActivityChart(svgId, waterLogs, fanLogs) {
  const spanMs = currentPeriod === '7d' ? 7 * 86400 * 1000 : 24 * 3600 * 1000;
  renderSvgActivityChart(svgId, {
    width: 360,
    height: 120,
    padL: 62,
    padR: 12,
    spanMs,
    emptyText: 'Нет срабатываний за выбранный период',
    lanes: [
      { label: 'Полив', items: Array.isArray(waterLogs) ? waterLogs : [], y: 34, color: '#38bdf8' },
      { label: 'Обдув', items: Array.isArray(fanLogs) ? fanLogs : [], y: 80, color: '#10b981' },
    ],
    formatTooltip: (lane, ev, isOn) =>
      `${lane.label}: ${isOn ? 'ВКЛ' : 'ВЫКЛ'} (${formatLocalTime(ev.timestamp)})`,
  });
}


function updateEspDots(isOnline) {
  ['esp-status-dot', 'climate-esp-dot'].forEach((id) => {
    const dot = document.getElementById(id);
    if (!dot) return;
    dot.classList.toggle('online', Boolean(isOnline));
    dot.classList.toggle('offline', !isOnline);
    dot.title = isOnline ? 'ESP онлайн' : 'ESP офлайн';
  });
}

function renderSummary(data) {
  if (!data) return;
  const online = Boolean(data.esp_online);
  updateEspDots(online);

  const badge = document.getElementById('climate-esp-badge');
  if (badge) {
    badge.textContent = online ? 'ESP: Онлайн' : 'ESP: Офлайн';
  }
  const lastSeenEl = document.getElementById('climate-esp-last-seen');
  if (lastSeenEl) {
    lastSeenEl.textContent = `Последний отклик: ${formatLocalTime(data.esp_last_seen)}`;
  }

  const tempVal = document.getElementById('climate-temp-val');
  const humVal = document.getElementById('climate-hum-val');
  const phVal = document.getElementById('climate-ph-val');
  const tempTime = document.getElementById('climate-temp-time');
  const humTime = document.getElementById('climate-hum-time');
  const phTime = document.getElementById('climate-ph-time');

  if (tempVal) {
    tempVal.textContent =
      data.temperature !== null && data.temperature !== undefined
        ? `${Number(data.temperature).toFixed(1)} °C`
        : '—';
  }
  if (humVal) {
    humVal.textContent =
      data.humidity !== null && data.humidity !== undefined
        ? `${Number(data.humidity).toFixed(1)} %`
        : '—';
  }
  if (phVal) {
    phVal.textContent =
      data.ph !== null && data.ph !== undefined ? Number(data.ph).toFixed(2) : '—';
  }
  if (tempTime) {
    tempTime.textContent = data.sensors_updated_at
      ? formatLocalTime(data.sensors_updated_at)
      : 'Нет данных SHT31';
  }
  if (humTime) {
    humTime.textContent = data.sensors_updated_at
      ? formatLocalTime(data.sensors_updated_at)
      : 'Нет данных SHT31';
  }
  if (phTime) {
    phTime.textContent = data.ph_updated_at
      ? formatLocalTime(data.ph_updated_at)
      : 'Ручной замер';
  }

  currentMode = data.mode === 'auto' ? 'auto' : 'manual';
  const editable = canControl();
  const isAuto = currentMode === 'auto';

  const roleHint = document.getElementById('climate-role-hint');
  if (roleHint) roleHint.classList.toggle('hidden', editable);

  const modeCtrl = document.getElementById('climate-mode-control');
  if (modeCtrl) {
    modeCtrl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.climateMode === currentMode);
      btn.disabled = !editable;
    });
  }

  const devices = [
    {
      toggleId: 'climate-water-toggle',
      statusId: 'climate-water-status',
      lockId: 'climate-water-lock',
      state: data.water_state === 'on',
      pending: Boolean(data.water_pending),
    },
    {
      toggleId: 'climate-fan-toggle',
      statusId: 'climate-fan-status',
      lockId: 'climate-fan-lock',
      state: data.fan_state === 'on',
      pending: Boolean(data.fan_pending),
    },
  ];

  devices.forEach((dev) => {
    const toggle = document.getElementById(dev.toggleId);
    const statusEl = document.getElementById(dev.statusId);
    const lockEl = document.getElementById(dev.lockId);
    if (toggle) {
      toggle.checked = dev.state;
      toggle.disabled = !editable || isAuto;
    }
    if (statusEl) {
      statusEl.textContent = dev.pending
        ? `${dev.state ? 'ВКЛ' : 'ВЫКЛ'} · Ожидает ESP`
        : dev.state
          ? 'ВКЛ'
          : 'ВЫКЛ';
      statusEl.classList.toggle('is-on', dev.state);
    }
    if (lockEl) {
      lockEl.classList.toggle('hidden', !isAuto);
    }
  });

  if (data.schedule && !scheduleDirty) {
    const wi = document.getElementById('climate-water-interval');
    const wd = document.getElementById('climate-water-duration');
    const fi = document.getElementById('climate-fan-interval');
    const fd = document.getElementById('climate-fan-duration');
    if (wi) wi.value = String(data.schedule.water_interval_hours);
    if (wd) wd.value = String(data.schedule.water_duration_minutes);
    if (fi) fi.value = String(data.schedule.fan_interval_hours);
    if (fd) fd.value = String(data.schedule.fan_duration_minutes);
  }

  [
    'climate-water-interval',
    'climate-water-duration',
    'climate-fan-interval',
    'climate-fan-duration',
    'climate-schedule-save-btn',
    'climate-ph-input',
    'climate-ph-save-btn',
  ].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = !editable;
  });
}

async function refreshClimateData({ includeHistory = true } = {}) {
  try {
    const summary = await api.getClimateSummary();
    renderSummary(summary);
    if (includeHistory) {
      const history = await api.getClimateHistory(currentPeriod);
      renderLineChart('climate-chart-temp', history.temperature, {
        color: '#f59e0b',
        unit: ' °C',
      });
      renderLineChart('climate-chart-hum', history.humidity, {
        color: '#38bdf8',
        unit: ' %',
      });
      renderLineChart('climate-chart-ph', history.ph, {
        color: '#a78bfa',
        unit: ' pH',
      });
      renderActivityChart('climate-chart-activity', history.water, history.fan);
    }
  } catch (err) {
    showClimateError(err.message || 'Не удалось загрузить данные климата');
  }
}

async function pollHeaderEspStatus() {
  if (document.hidden) return;
  try {
    const summary = await api.getClimateSummary();
    updateEspDots(Boolean(summary && summary.esp_online));
  } catch (_) {
    // Игнорируем в фоне
  }
}

export function updateClimateRole(role) {
  currentRole = role || 'guest';
  const modal = document.getElementById('modal-climate');
  if (modal && !modal.classList.contains('hidden')) {
    refreshClimateData({ includeHistory: false });
  }
}

export async function openClimateModal() {
  const modalCtrl = getClimateModalCtrl();
  if (!modalCtrl.getElement()) return;
  scheduleDirty = false;
  showClimateError('');
  modalCtrl.open();
  await refreshClimateData({ includeHistory: true });

  if (modalCtrl.isOpen()) {
    pollingCoordinator.register(
      CLIMATE_MODAL_POLL_TASK,
      () => {
        if (modalCtrl.isOpen()) {
          return refreshClimateData({ includeHistory: true });
        }
      },
      5000,
      { pauseWhenModalOpen: false, pauseWhenHidden: true }
    );
    pollingCoordinator.start(CLIMATE_MODAL_POLL_TASK);
  }
}

export function closeClimateModal() {
  getClimateModalCtrl().close();
}

async function handleDeviceToggle(device, checked) {
  if (!canControl()) return;
  showClimateError('');
  const toggleId = device === 'water' ? 'climate-water-toggle' : 'climate-fan-toggle';
  const toggle = document.getElementById(toggleId);
  if (toggle) toggle.disabled = true;
  try {
    await api.controlClimate(device, checked ? 'on' : 'off');
    const label = device === 'water' ? 'Полив' : 'Вентиляция';
    showToast(`${label}: команда «${checked ? 'ВКЛ' : 'ВЫКЛ'}» отправлена на ESP`);
    await refreshClimateData({ includeHistory: false });
  } catch (err) {
    if (toggle) toggle.checked = !checked;
    showClimateError(err.message || 'Не удалось отправить команду на ESP');
  } finally {
    if (toggle) toggle.disabled = !canControl() || currentMode === 'auto';
  }
}

export function initClimate() {
  const btnOpen = document.getElementById('btn-climate-menu');
  const refreshBtn = document.getElementById('climate-refresh-btn');
  const modeCtrl = document.getElementById('climate-mode-control');
  const periodCtrl = document.getElementById('climate-period-control');
  const waterToggle = document.getElementById('climate-water-toggle');
  const fanToggle = document.getElementById('climate-fan-toggle');
  const schedForm = document.getElementById('climate-schedule-form');
  const phForm = document.getElementById('climate-ph-form');

  getClimateModalCtrl().bind();

  if (btnOpen) btnOpen.addEventListener('click', openClimateModal);
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      scheduleDirty = false;
      refreshClimateData({ includeHistory: true });
    });
  }

  if (modeCtrl) {
    modeCtrl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (btn.disabled || !canControl()) return;
        const nextMode = btn.dataset.climateMode;
        showClimateError('');
        try {
          await api.setClimateMode(nextMode);
          showToast(
            nextMode === 'auto'
              ? 'Климат: включён автоматический режим'
              : 'Климат: включён ручной режим'
          );
          await refreshClimateData({ includeHistory: false });
        } catch (err) {
          showClimateError(err.message || 'Не удалось сменить режим');
        }
      });
    });
  }

  if (periodCtrl) {
    periodCtrl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        currentPeriod = btn.dataset.climatePeriod === '7d' ? '7d' : '24h';
        periodCtrl.querySelectorAll('.seg-btn').forEach((b) => {
          b.classList.toggle('active', b.dataset.climatePeriod === currentPeriod);
        });
        await refreshClimateData({ includeHistory: true });
      });
    });
  }

  if (waterToggle) {
    waterToggle.addEventListener('change', (e) =>
      handleDeviceToggle('water', Boolean(e.target.checked))
    );
  }
  if (fanToggle) {
    fanToggle.addEventListener('change', (e) =>
      handleDeviceToggle('fan', Boolean(e.target.checked))
    );
  }

  [
    'climate-water-interval',
    'climate-water-duration',
    'climate-fan-interval',
    'climate-fan-duration',
  ].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => { scheduleDirty = true; });
  });

  if (schedForm) {
    schedForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!canControl()) return;
      showClimateError('');
      const payload = {
        water_interval_hours: Number(document.getElementById('climate-water-interval').value),
        water_duration_minutes: Number(document.getElementById('climate-water-duration').value),
        fan_interval_hours: Number(document.getElementById('climate-fan-interval').value),
        fan_duration_minutes: Number(document.getElementById('climate-fan-duration').value),
      };
      if (Object.values(payload).some((v) => !Number.isInteger(v) || v < 1)) {
        showClimateError('Все параметры расписания должны быть целыми числами >= 1');
        return;
      }
      try {
        await api.updateClimateSchedule(payload);
        scheduleDirty = false;
        showToast('Расписание полива и вентиляции сохранено');
        await refreshClimateData({ includeHistory: false });
      } catch (err) {
        showClimateError(err.message || 'Не удалось сохранить расписание');
      }
    });
  }

  if (phForm) {
    phForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!canControl()) return;
      showClimateError('');
      const input = document.getElementById('climate-ph-input');
      const val = Number(String(input.value).replace(',', '.'));
      if (!Number.isFinite(val) || val < 0 || val > 14) {
        showClimateError('Уровень pH должен быть в диапазоне 0–14');
        return;
      }
      try {
        await api.addPhReading(val);
        input.value = '';
        showToast(`Замер pH (${val.toFixed(1)}) сохранён`);
        await refreshClimateData({ includeHistory: true });
      } catch (err) {
        showClimateError(err.message || 'Не удалось сохранить pH');
      }
    });
  }

  pollingCoordinator.register(CLIMATE_HEADER_POLL_TASK, pollHeaderEspStatus, 10000, {
    pauseWhenModalOpen: true,
    pauseWhenHidden: true,
  });
  pollingCoordinator.start(CLIMATE_HEADER_POLL_TASK, { runImmediately: true });
}
