// clients/web/js/cameras.js
// Меню камер, Live-режим и управление настройками V4L2/RGB в Web UI

import { api } from './api.js';
import { showError, showToast } from './dialogs.js';

let currentRole = 'guest';
let activeCamera = null;
let liveStatusInterval = null;
let liveConnectTimeout = null;
let currentCameraControls = null;
let currentActiveTab = null;
let streamReady = false;

const CATEGORY_DEFS = [
  {
    id: 'format',
    title: 'Разрешение и формат',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="2" y="4" width="20" height="16" rx="2"/>
      <path d="M8 9H6v2M16 15h2v-2"/>
    </svg>`,
  },
  {
    id: 'exposure',
    title: 'Экспозиция и яркость',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="4"/>
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>
    </svg>`,
  },
  {
    id: 'color',
    title: 'Цвет и баланс белого',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>
    </svg>`,
  },
  {
    id: 'focus',
    title: 'Фокус и оптика',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>
    </svg>`,
  },
];

const DEPENDENT_TRIGGERS = new Set([
  'white_balance_automatic',
  'auto_exposure',
  'focus_automatic_continuous',
  'focus_auto',
]);

export function updateCamerasRole(role) {
  currentRole = role || 'guest';
  const btn = document.getElementById('btn-cameras-menu');
  if (btn) {
    btn.classList.toggle('hidden', currentRole === 'guest');
  }
}

function formatCameraDisplayName(cam) {
  const raw = (cam && cam.auto_name ? String(cam.auto_name) : '').trim();
  if (!raw || raw === cam.hardware_id || /^usb-.*-video-index\d+$/i.test(raw)) {
    const cleaned = (raw || cam.hardware_id || '')
      .replace(/^usb-/i, '')
      .replace(/-video-index\d+$/i, '')
      .replace(/_/g, ' ')
      .trim();
    return cleaned || `Камера #${cam.id}`;
  }
  return raw;
}

function formatCalibrationSummary(summary) {
  if (!summary) return '';
  return String(summary)
    .replace(/^Калибровка\s+[^:]+:\s*/i, '')
    .replace(/(\d+)x(\d+)/g, '$1\u00d7$2')
    .trim();
}

// Открытие модального окна со списком камер
export async function openCamerasModal() {
  const modal = document.getElementById('modal-cameras-menu');
  const container = document.getElementById('cameras-list-container');
  if (!modal || !container) return;

  modal.classList.remove('hidden');
  container.innerHTML = '<div class="loading-spinner">Загрузка списка камер...</div>';

  try {
    const cameras = await api.getCameras();
    container.innerHTML = '';

    if (!cameras || cameras.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <p class="empty-state-desc">Камеры не обнаружены на Raspberry Pi.</p>
        </div>
      `;
      return;
    }

    cameras.forEach((cam) => {
      const card = document.createElement('div');
      card.className = 'camera-menu-card glass-panel';

      const isOnline = Boolean(cam.is_online);
      const displayName = formatCameraDisplayName(cam);
      const instName = cam.installation_name
        ? `Установка: ${cam.installation_name}`
        : 'Не привязана';
      const showHwId = cam.hardware_id && cam.hardware_id !== displayName;
      const hwText = showHwId ? `<span class="camera-hw-id">${cam.hardware_id}</span>` : '';
      const calibText = formatCalibrationSummary(cam.calibration_summary);

      card.innerHTML = `
        <div class="camera-card-header">
          <div class="camera-title-group">
            <span class="status-dot ${isOnline ? 'online' : 'offline'}"></span>
            <strong class="camera-name">${displayName}</strong>
          </div>
          <span class="camera-status-text">${isOnline ? 'Онлайн' : 'Офлайн'}</span>
        </div>
        <div class="camera-card-details">
          <div class="camera-inst-badge">${instName}</div>
          ${hwText}
          ${calibText ? `<div class="camera-calib-text">${calibText}</div>` : ''}
        </div>
        <div class="camera-card-actions">
          <button type="button" class="btn btn-primary btn-cam-live" ${!isOnline ? 'disabled' : ''}>
            <svg class="icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="23 7 16 12 23 17 23 7"/>
              <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
            </svg>
            <span>Live</span>
          </button>
        </div>
      `;

      const liveBtn = card.querySelector('.btn-cam-live');
      if (liveBtn) {
        liveBtn.addEventListener('click', () => {
          closeCamerasModal();
          openLiveModal(cam);
        });
      }

      container.appendChild(card);
    });
  } catch (err) {
    container.innerHTML = `<div class="form-error-inline">${err.message || 'Ошибка загрузки камер'}</div>`;
  }
}

export function closeCamerasModal() {
  const modal = document.getElementById('modal-cameras-menu');
  if (modal) {
    modal.classList.add('hidden');
  }
}

function setLiveViewportState(state, message = '') {
  const streamImg = document.getElementById('live-stream-img');
  const loadingOverlay = document.getElementById('live-loading-overlay');
  const errorOverlay = document.getElementById('live-error-overlay');
  const errorText = document.getElementById('live-error-text');

  if (state === 'loading') {
    streamReady = false;
    if (streamImg) streamImg.classList.add('hidden');
    if (loadingOverlay) loadingOverlay.classList.remove('hidden');
    if (errorOverlay) errorOverlay.classList.add('hidden');
  } else if (state === 'streaming') {
    streamReady = true;
    if (streamImg) streamImg.classList.remove('hidden');
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
    if (errorOverlay) errorOverlay.classList.add('hidden');
  } else if (state === 'error') {
    streamReady = false;
    if (streamImg) streamImg.classList.add('hidden');
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
    if (errorText && message) errorText.textContent = message;
    if (errorOverlay) errorOverlay.classList.remove('hidden');
  }
}

function attachStreamHandlers(cameraId) {
  const streamImg = document.getElementById('live-stream-img');
  if (!streamImg) return;

  if (liveConnectTimeout) {
    clearTimeout(liveConnectTimeout);
    liveConnectTimeout = null;
  }

  setLiveViewportState('loading');

  streamImg.onload = () => {
    if (!activeCamera || activeCamera.id !== cameraId) return;
    if (liveConnectTimeout) {
      clearTimeout(liveConnectTimeout);
      liveConnectTimeout = null;
    }
    setLiveViewportState('streaming');
  };

  streamImg.onerror = () => {
    if (!activeCamera || activeCamera.id !== cameraId) return;
    if (!streamImg.getAttribute('src')) return;
    if (liveConnectTimeout) {
      clearTimeout(liveConnectTimeout);
      liveConnectTimeout = null;
    }
    setLiveViewportState('error', 'Стрим недоступен');
  };

  streamImg.src = `/api/cameras/${cameraId}/live/stream?t=${Date.now()}`;

  // Если за 12 с не пришёл ни один кадр — показываем понятную заглушку
  liveConnectTimeout = setTimeout(() => {
    if (activeCamera && activeCamera.id === cameraId && !streamReady) {
      setLiveViewportState('error', 'Таймаут подключения к камере');
    }
  }, 12000);
}

// Запуск и открытие модального окна Live-режима
export async function openLiveModal(camera) {
  if (!camera || !camera.id) return;
  activeCamera = camera;
  currentActiveTab = null;
  currentCameraControls = null;

  const modal = document.getElementById('modal-camera-live');
  const titleEl = document.getElementById('live-modal-title');
  const pausedOverlay = document.getElementById('live-paused-overlay');
  const fpsEl = document.getElementById('live-fps');
  const latEl = document.getElementById('live-latency');
  if (!modal) return;

  if (titleEl) {
    titleEl.textContent = `Live: ${formatCameraDisplayName(camera)}`;
  }
  if (fpsEl) fpsEl.textContent = '0.0';
  if (latEl) latEl.textContent = '0';
  if (pausedOverlay) pausedOverlay.classList.add('hidden');

  setLiveViewportState('loading');
  renderLiveSettingsOverlay();
  modal.classList.remove('hidden');

  try {
    await api.startLive(camera.id);
    if (!activeCamera || activeCamera.id !== camera.id) return;
    attachStreamHandlers(camera.id);
  } catch (err) {
    setLiveViewportState('error', err.message || 'Ошибка запуска Live-стрима');
  }

  if (liveStatusInterval) clearInterval(liveStatusInterval);
  liveStatusInterval = setInterval(updateLiveMetrics, 1000);
  updateLiveMetrics();

  loadCameraControls(camera.id, true);
}

// Остановка и закрытие Live-режима
export async function closeLiveModal() {
  const modal = document.getElementById('modal-camera-live');
  const streamImg = document.getElementById('live-stream-img');
  if (modal) modal.classList.add('hidden');

  if (liveStatusInterval) {
    clearInterval(liveStatusInterval);
    liveStatusInterval = null;
  }
  if (liveConnectTimeout) {
    clearTimeout(liveConnectTimeout);
    liveConnectTimeout = null;
  }

  streamReady = false;
  currentActiveTab = null;
  if (streamImg) {
    streamImg.onload = null;
    streamImg.onerror = null;
    streamImg.removeAttribute('src');
    streamImg.classList.add('hidden');
  }

  if (activeCamera && activeCamera.id) {
    const camId = activeCamera.id;
    activeCamera = null;
    try {
      await api.stopLive(camId);
    } catch (_) {
      // игнорируем ошибку при закрытии
    }
  }
}

// Периодическое обновление FPS, задержки, разрешения и статуса паузы
async function updateLiveMetrics() {
  if (!activeCamera || !activeCamera.id) return;
  try {
    const status = await api.getLiveStatus(activeCamera.id);
    if (!status) return;

    const fpsEl = document.getElementById('live-fps');
    const latEl = document.getElementById('live-latency');
    const resEl = document.getElementById('live-res');
    const pausedOverlay = document.getElementById('live-paused-overlay');

    if (fpsEl) fpsEl.textContent = Number(status.fps || 0).toFixed(1);
    if (latEl) latEl.textContent = String(Math.round(Number(status.latency_ms || 0)));
    if (resEl && status.resolution) resEl.textContent = status.resolution;

    if (pausedOverlay) {
      pausedOverlay.classList.toggle('hidden', !Boolean(status.is_paused));
    }
  } catch (_) {
    // игнорируем сетевые сбои поллинга статуса
  }
}

// Загрузка доступных контролов камеры и отрисовка панели
async function loadCameraControls(cameraId, refresh = false) {
  try {
    const data = await api.getCameraControls(cameraId, { refresh });
    if (!activeCamera || activeCamera.id !== cameraId) return;
    currentCameraControls = data;
    renderLiveSettingsOverlay();
  } catch (_) {
    renderLiveSettingsOverlay();
  }
}

// Прокрутка горизонтального контейнера колёсиком мыши при наведении
function attachHorizontalWheelScroll(el) {
  if (!el) return;
  el.addEventListener(
    'wheel',
    (e) => {
      if (el.scrollWidth > el.clientWidth && e.deltaY !== 0) {
        e.preventDefault();
        el.scrollLeft += e.deltaY;
      }
    },
    { passive: false }
  );
}

// Отрисовка ряда квадратных кнопок категорий и выпадающей строки плашек
function renderLiveSettingsOverlay() {
  const container = document.getElementById('live-settings-content');
  if (!container) return;

  const buttonsHtml = CATEGORY_DEFS.map((cat) => {
    const isActive = currentActiveTab === cat.id;
    return `
      <button type="button"
        class="live-cat-btn cat-${cat.id} ${isActive ? 'active' : ''}"
        data-tab="${cat.id}"
        title="${cat.title}"
        aria-label="${cat.title}"
        aria-pressed="${isActive ? 'true' : 'false'}">
        ${cat.icon}
      </button>
    `;
  }).join('');

  container.innerHTML = `
    <div class="live-cat-bar" id="live-cat-bar">
      <div class="live-cat-track">${buttonsHtml}</div>
    </div>
    <div class="live-pills-bar ${currentActiveTab ? '' : 'hidden'}" id="live-pills-bar">
      <div class="live-pills-track" id="live-pills-track"></div>
    </div>
  `;

  const catBar = container.querySelector('#live-cat-bar');
  const pillsBar = container.querySelector('#live-pills-bar');
  attachHorizontalWheelScroll(catBar);
  attachHorizontalWheelScroll(pillsBar);

  container.querySelectorAll('.live-cat-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      // Повторный клик по активной кнопке скрывает плашки
      currentActiveTab = currentActiveTab === tab ? null : tab;
      container.querySelectorAll('.live-cat-btn').forEach((b) => {
        const active = b.dataset.tab === currentActiveTab;
        b.classList.toggle('active', active);
        b.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      if (pillsBar) {
        pillsBar.classList.toggle('hidden', !currentActiveTab);
      }
      renderCategoryPills();
    });
  });

  if (currentActiveTab) {
    renderCategoryPills();
  }
}

function createControlPill(labelText, isDisabled = false, extraClass = '') {
  const pill = document.createElement('div');
  pill.className = `live-ctrl-pill ${isDisabled ? 'is-disabled' : ''} ${extraClass}`.trim();
  if (labelText) {
    const head = document.createElement('div');
    head.className = 'live-pill-head';
    head.innerHTML = `<span class="live-pill-label">${labelText}</span>`;
    pill.appendChild(head);
  }
  return pill;
}

function getResolutionsForFormat(formats, fmt) {
  const matched = (formats || []).find((f) => f.format === fmt);
  if (matched && Array.isArray(matched.resolutions) && matched.resolutions.length > 0) {
    return matched.resolutions;
  }
  const all = [];
  (formats || []).forEach((f) => {
    (f.resolutions || []).forEach((r) => {
      if (!all.includes(r)) all.push(r);
    });
  });
  return all;
}

// Отрисовка компактных прозрачных плашек настроек выбранной категории в одну строку
function renderCategoryPills() {
  const track = document.getElementById('live-pills-track');
  if (!track || !currentActiveTab) return;

  track.innerHTML = '';

  if (!currentCameraControls) {
    const pill = createControlPill('Параметры камеры', true);
    const note = document.createElement('span');
    note.className = 'live-pill-static';
    note.textContent = 'Загрузка...';
    pill.appendChild(note);
    track.appendChild(pill);
    return;
  }

  const { formats, controls, rgb, current_format, current_resolution } = currentCameraControls;

  if (currentActiveTab === 'format') {
    const availFormats = (formats || []).filter((f) => f && f.format);
    const activeFmt = current_format || (availFormats[0] && availFormats[0].format) || '';
    const resolutions = getResolutionsForFormat(availFormats, activeFmt);

    // Плашка: Разрешение
    const resPill = createControlPill('Разрешение', resolutions.length <= 1);
    if (resolutions.length === 0) {
      const emptyEl = document.createElement('span');
      emptyEl.className = 'live-pill-static';
      emptyEl.textContent = current_resolution || 'Неизвестно';
      resPill.appendChild(emptyEl);
    } else {
      const selRes = document.createElement('select');
      selRes.id = 'ctrl-select-res';
      selRes.className = 'live-pill-select';
      if (resolutions.length <= 1) selRes.disabled = true;
      selRes.innerHTML = resolutions
        .map(
          (r) => `<option value="${r}" ${r === current_resolution ? 'selected' : ''}>${r.replace('x', '\u00d7')}</option>`
        )
        .join('');
      selRes.addEventListener('change', async (e) => {
        const newRes = e.target.value;
        try {
          await api.updateCameraControls(activeCamera.id, { resolution: newRes });
          currentCameraControls.current_resolution = newRes;
          const resEl = document.getElementById('live-res');
          if (resEl) resEl.textContent = newRes;
          showToast(`Разрешение: ${newRes.replace('x', '\u00d7')}`);
        } catch (err) {
          showError(err.message || 'Ошибка применения разрешения');
        }
      });
      resPill.appendChild(selRes);
    }
    track.appendChild(resPill);

    // Плашка: Формат / Кодек
    const fmtPill = createControlPill('Формат', availFormats.length <= 1);
    if (availFormats.length === 0) {
      const emptyEl = document.createElement('span');
      emptyEl.className = 'live-pill-static';
      emptyEl.textContent = activeFmt || 'Неизвестно';
      fmtPill.appendChild(emptyEl);
    } else {
      const selFmt = document.createElement('select');
      selFmt.id = 'ctrl-select-fmt';
      selFmt.className = 'live-pill-select';
      if (availFormats.length <= 1) selFmt.disabled = true;
      selFmt.innerHTML = availFormats
        .map(
          (f) => `<option value="${f.format}" ${f.format === activeFmt ? 'selected' : ''}>${f.format}</option>`
        )
        .join('');
      selFmt.addEventListener('change', async (e) => {
        const newFmt = e.target.value;
        const fmtResList = getResolutionsForFormat(availFormats, newFmt);
        let nextRes = currentCameraControls.current_resolution;
        const payload = { format: newFmt };
        if (fmtResList.length > 0 && !fmtResList.includes(nextRes)) {
          nextRes = fmtResList[0];
          payload.resolution = nextRes;
        }
        try {
          await api.updateCameraControls(activeCamera.id, payload);
          currentCameraControls.current_format = newFmt;
          if (payload.resolution) {
            currentCameraControls.current_resolution = payload.resolution;
            const resEl = document.getElementById('live-res');
            if (resEl) resEl.textContent = payload.resolution;
          }
          renderCategoryPills();
          showToast(`Формат: ${newFmt}`);
        } catch (err) {
          showError(err.message || 'Ошибка применения формата');
        }
      });
      fmtPill.appendChild(selFmt);
    }
    track.appendChild(fmtPill);
    return;
  }

  if (currentActiveTab === 'exposure') {
    const exposureControls = (controls || []).filter((c) => c.category === 'exposure');
    appendV4L2ControlPills(track, exposureControls);
    return;
  }

  if (currentActiveTab === 'color') {
    const colorControls = (controls || []).filter((c) => c.category === 'color');
    appendV4L2ControlPills(track, colorControls, true);

    // Компактные плашки программной RGB-коррекции
    const curR = rgb && rgb.r !== undefined ? Number(rgb.r) : 1.0;
    const curG = rgb && rgb.g !== undefined ? Number(rgb.g) : 1.0;
    const curB = rgb && rgb.b !== undefined ? Number(rgb.b) : 1.0;

    const rgbChannels = [
      { id: 'r', title: 'Канал R', value: curR, accentClass: 'rgb-r' },
      { id: 'g', title: 'Канал G', value: curG, accentClass: 'rgb-g' },
      { id: 'b', title: 'Канал B', value: curB, accentClass: 'rgb-b' },
    ];

    rgbChannels.forEach((ch) => {
      const pill = createControlPill(`<span class="${ch.accentClass}">${ch.title}</span>`);
      const head = pill.querySelector('.live-pill-head');
      const valSpan = document.createElement('span');
      valSpan.className = 'live-pill-val';
      valSpan.id = `val-rgb-${ch.id}`;
      valSpan.textContent = ch.value.toFixed(2);
      head.appendChild(valSpan);

      const input = document.createElement('input');
      input.type = 'range';
      input.className = 'live-pill-slider';
      input.id = `slider-rgb-${ch.id}`;
      input.min = '0.5';
      input.max = '2.0';
      input.step = '0.05';
      input.value = String(ch.value);

      input.addEventListener('input', () => {
        valSpan.textContent = parseFloat(input.value).toFixed(2);
      });
      input.addEventListener('change', async () => {
        const nextRgb = {
          r: parseFloat(document.getElementById('slider-rgb-r').value),
          g: parseFloat(document.getElementById('slider-rgb-g').value),
          b: parseFloat(document.getElementById('slider-rgb-b').value),
        };
        try {
          await api.updateCameraControls(activeCamera.id, { rgb: nextRgb });
          currentCameraControls.rgb = nextRgb;
        } catch (err) {
          showError(err.message || 'Ошибка применения RGB');
        }
      });

      pill.appendChild(input);
      track.appendChild(pill);
    });
    return;
  }

  if (currentActiveTab === 'focus') {
    const focusControls = (controls || []).filter((c) => c.category === 'focus');
    const hasFocusCtrl = focusControls.some(
      (c) =>
        c.name === 'focus_automatic_continuous' ||
        c.name === 'focus_auto' ||
        c.name === 'focus_absolute'
    );

    if (!hasFocusCtrl) {
      const fixedPill = createControlPill('Фокусировка', true);
      const staticVal = document.createElement('input');
      staticVal.type = 'text';
      staticVal.className = 'live-pill-static-input';
      staticVal.value = 'Фиксированный фокус';
      staticVal.disabled = true;
      fixedPill.appendChild(staticVal);
      track.appendChild(fixedPill);
    }

    appendV4L2ControlPills(track, focusControls, !hasFocusCtrl);
  }
}

function appendV4L2ControlPills(track, controlsList, allowEmpty = false) {
  if (!controlsList || controlsList.length === 0) {
    if (!allowEmpty) {
      const emptyPill = createControlPill('Настройки', true);
      const span = document.createElement('span');
      span.className = 'live-pill-static';
      span.textContent = 'Не поддерживаются камерой';
      emptyPill.appendChild(span);
      track.appendChild(emptyPill);
    }
    return;
  }

  controlsList.forEach((ctrl) => {
    const isInactive = Boolean(ctrl.is_inactive);
    const labelText = formatControlTitle(ctrl.name);

    if (ctrl.type === 'bool') {
      const pill = createControlPill(labelText, isInactive, 'live-pill-toggle');
      const wrap = document.createElement('label');
      wrap.className = 'live-pill-switch';
      const isChecked = Boolean(ctrl.value);
      wrap.innerHTML = `
        <input type="checkbox" data-cname="${ctrl.name}" ${isChecked ? 'checked' : ''} ${isInactive ? 'disabled' : ''}>
        <span class="live-pill-switch-text">${isChecked ? 'Вкл' : 'Выкл'}</span>
      `;
      const chk = wrap.querySelector('input[type="checkbox"]');
      const txt = wrap.querySelector('.live-pill-switch-text');
      if (chk) {
        chk.addEventListener('change', async (e) => {
          const val = e.target.checked ? 1 : 0;
          if (txt) txt.textContent = e.target.checked ? 'Вкл' : 'Выкл';
          await sendControlChange(ctrl.name, val);
        });
      }
      pill.appendChild(wrap);
      track.appendChild(pill);
    } else if (ctrl.type === 'menu' && ctrl.menu_items && ctrl.menu_items.length > 0) {
      const pill = createControlPill(labelText, isInactive);
      const sel = document.createElement('select');
      sel.className = 'live-pill-select';
      sel.dataset.cname = ctrl.name;
      if (isInactive) sel.disabled = true;
      sel.innerHTML = ctrl.menu_items
        .map(
          (m) => `<option value="${m.id}" ${m.id === ctrl.value ? 'selected' : ''}>${m.label}</option>`
        )
        .join('');
      sel.addEventListener('change', async (e) => {
        await sendControlChange(ctrl.name, parseInt(e.target.value, 10));
      });
      pill.appendChild(sel);
      track.appendChild(pill);
    } else {
      const minVal = ctrl.min !== null && ctrl.min !== undefined ? ctrl.min : 0;
      const maxVal = ctrl.max !== null && ctrl.max !== undefined ? ctrl.max : 100;
      const stepVal = ctrl.step || 1;
      const curVal = ctrl.value !== null && ctrl.value !== undefined ? ctrl.value : minVal;

      const pill = createControlPill(labelText, isInactive);
      const head = pill.querySelector('.live-pill-head');
      const valDisplay = document.createElement('span');
      valDisplay.className = 'live-pill-val';
      valDisplay.id = `val-${ctrl.name}`;
      valDisplay.textContent = String(curVal);
      head.appendChild(valDisplay);

      const slider = document.createElement('input');
      slider.type = 'range';
      slider.className = 'live-pill-slider';
      slider.dataset.cname = ctrl.name;
      slider.min = String(minVal);
      slider.max = String(maxVal);
      slider.step = String(stepVal);
      slider.value = String(curVal);
      if (isInactive) slider.disabled = true;

      slider.addEventListener('input', (e) => {
        valDisplay.textContent = e.target.value;
      });
      slider.addEventListener('change', async (e) => {
        await sendControlChange(ctrl.name, parseInt(e.target.value, 10));
      });

      pill.appendChild(slider);
      track.appendChild(pill);
    }
  });
}

// Отправка изменения v4l2 контрола на сервер и синхронизация зависимых контролов
async function sendControlChange(ctrlName, value) {
  if (!activeCamera || !activeCamera.id) return;
  const cameraId = activeCamera.id;
  try {
    const patchRes = await api.updateCameraControls(cameraId, {
      controls: { [ctrlName]: value },
    });
    if (currentCameraControls && currentCameraControls.controls) {
      if (patchRes && Array.isArray(patchRes.controls)) {
        currentCameraControls.controls = patchRes.controls;
      } else {
        const c = currentCameraControls.controls.find((item) => item.name === ctrlName);
        if (c) c.value = value;
      }
      if (DEPENDENT_TRIGGERS.has(ctrlName)) {
        renderCategoryPills();
      }
    }

    // Для зависимых контролов запрашиваем обновлённое реальное состояние с камеры
    if (DEPENDENT_TRIGGERS.has(ctrlName)) {
      const refreshed = await api.getCameraControls(cameraId, { refresh: true });
      if (activeCamera && activeCamera.id === cameraId && refreshed) {
        currentCameraControls = refreshed;
        renderCategoryPills();
      }
    }
  } catch (err) {
    showError(err.message || 'Ошибка применения контрола камеры');
  }
}

// Человекочитаемые подписи контролов
function formatControlTitle(name) {
  const titles = {
    brightness: 'Яркость',
    contrast: 'Контрастность',
    saturation: 'Насыщенность',
    hue: 'Цветовой тон',
    white_balance_automatic: 'Автобаланс белого',
    white_balance_temperature: 'Температура ББ (K)',
    auto_exposure: 'Режим экспозиции',
    exposure_time_absolute: 'Выдержка',
    exposure_dynamic_framerate: 'Динам. FPS',
    backlight_compensation: 'Компенс. засветки',
    gain: 'Усиление (Gain)',
    focus_automatic_continuous: 'Автофокус',
    focus_auto: 'Автофокус',
    focus_absolute: 'Ручной фокус',
    sharpness: 'Резкость',
    zoom_absolute: 'Зум',
    power_line_frequency: 'Частота сети',
  };
  return titles[name] || name;
}

// Скачивание свежего JPEG напрямую без открытия новой вкладки через fetch -> Blob
async function downloadLiveSnapshot() {
  if (!activeCamera || !activeCamera.id) return;
  const btn = document.getElementById('btn-live-download-snapshot');
  if (btn) btn.disabled = true;
  try {
    const url = `/api/cameras/${activeCamera.id}/live/snapshot?t=${Date.now()}`;
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) {
      let detail = `Ошибка HTTP ${resp.status}`;
      try {
        const errJson = await resp.json();
        if (errJson && errJson.detail) detail = errJson.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const blob = await resp.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = `snapshot_${activeCamera.hardware_id || activeCamera.id}_${Date.now()}.jpg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (err) {
    showError(err.message || 'Не удалось скачать тестовый снимок');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Инициализация модуля камер
export function initCameras() {
  const btnMenu = document.getElementById('btn-cameras-menu');
  const modalMenuClose = document.getElementById('modal-cameras-menu-close');
  const modalLiveClose = document.getElementById('modal-camera-live-close');
  const btnDownloadSnap = document.getElementById('btn-live-download-snapshot');
  const btnLiveRetry = document.getElementById('btn-live-retry');

  if (btnMenu) {
    btnMenu.addEventListener('click', openCamerasModal);
  }
  if (modalMenuClose) {
    modalMenuClose.addEventListener('click', closeCamerasModal);
  }
  if (modalLiveClose) {
    modalLiveClose.addEventListener('click', closeLiveModal);
  }
  if (btnDownloadSnap) {
    btnDownloadSnap.addEventListener('click', downloadLiveSnapshot);
  }
  if (btnLiveRetry) {
    btnLiveRetry.addEventListener('click', async () => {
      if (!activeCamera || !activeCamera.id) return;
      setLiveViewportState('loading');
      try {
        await api.startLive(activeCamera.id);
        attachStreamHandlers(activeCamera.id);
      } catch (err) {
        setLiveViewportState('error', err.message || 'Стрим недоступен');
      }
    });
  }
}
