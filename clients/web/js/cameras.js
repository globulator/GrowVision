// clients/web/js/cameras.js
// Меню камер, Live-режим, SVG RGB-фильтр, режим Разметки и управление V4L2-настройками

import { api } from './api.js';
import { showError, showToast } from './dialogs.js';
import { getLoadingSpinnerHTML } from './placeholders.js';

let currentRole = 'guest';
let activeCamera = null;
let liveStatusInterval = null;
let liveConnectTimeout = null;
let currentCameraControls = null;
let initialCameraSnapshot = null;
let draftRgb = { r: 1.0, g: 1.0, b: 1.0 };
let currentActiveTab = null;
let currentLiveLayer = 'raw'; // 'raw' или 'annotated'
let streamReady = false;
let deviceOfflineShown = false;
let isClosingFlow = false;
let onLiveProfileSavedCallback = null;

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

const RESET_ICON_SVG = `<svg class="icon-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="1 4 1 10 7 10"/>
  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>
</svg>`;

export function setLiveProfileSavedHandler(fn) {
  onLiveProfileSavedCallback = fn;
}

export function updateCamerasRole(role) {
  currentRole = role || 'guest';
  const btn = document.getElementById('btn-cameras-menu');
  if (btn) {
    btn.classList.toggle('hidden', currentRole === 'guest');
  }
}

function clampRgbValue(val, def = 1.0) {
  const num = Number(val);
  if (!Number.isFinite(num)) return def;
  return Math.max(0.0, Math.min(1.0, Math.round(num * 100) / 100));
}

function applyLiveRgbFilter(rgb) {
  const r = clampRgbValue(rgb?.r, 1.0);
  const g = clampRgbValue(rgb?.g, 1.0);
  const b = clampRgbValue(rgb?.b, 1.0);
  const matrixEl = document.getElementById('live-rgb-matrix');
  const streamImg = document.getElementById('live-stream-img');
  if (matrixEl) {
    matrixEl.setAttribute(
      'values',
      `${r} 0 0 0 0  0 ${g} 0 0 0  0 0 ${b} 0 0  0 0 0 1 0`
    );
  }
  if (streamImg) {
    const isIdentity = Math.abs(r - 1.0) < 1e-3 && Math.abs(g - 1.0) < 1e-3 && Math.abs(b - 1.0) < 1e-3;
    streamImg.style.filter = isIdentity ? '' : 'url(#live-rgb-filter)';
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
    if (loadingOverlay) {
      loadingOverlay.innerHTML = getLoadingSpinnerHTML();
      loadingOverlay.classList.remove('hidden');
    }
    if (errorOverlay) errorOverlay.classList.add('hidden');
  } else if (state === 'streaming') {
    streamReady = true;
    deviceOfflineShown = false;
    if (streamImg) streamImg.classList.remove('hidden');
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
    if (errorOverlay) errorOverlay.classList.add('hidden');
  } else if (state === 'error') {
    streamReady = false;
    if (streamImg) streamImg.classList.add('hidden');
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
    if (errorText) errorText.textContent = message || 'Стрим недоступен';
    if (errorOverlay) errorOverlay.classList.remove('hidden');
  }
}

function attachStreamHandlers(cameraId, mode = currentLiveLayer) {
  const streamImg = document.getElementById('live-stream-img');
  if (!streamImg) return;

  if (liveConnectTimeout) {
    clearTimeout(liveConnectTimeout);
    liveConnectTimeout = null;
  }

  setLiveViewportState('loading');

  streamImg.onload = () => {
    if (!activeCamera || activeCamera.id !== cameraId) return;
    if (deviceOfflineShown) return;
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
    setLiveViewportState('error', deviceOfflineShown ? 'Камера отключена' : 'Стрим недоступен');
  };

  const streamMode = mode === 'annotated' ? 'annotated' : 'raw';
  streamImg.src = `/api/cameras/${cameraId}/live/stream?mode=${streamMode}&t=${Date.now()}`;

  // Если за 12 с не пришёл ни один кадр — показываем заглушку
  liveConnectTimeout = setTimeout(() => {
    if (activeCamera && activeCamera.id === cameraId && !streamReady) {
      setLiveViewportState('error', 'Таймаут подключения к камере');
    }
  }, 12000);
}

function updateLiveLayerButtonsUi() {
  const rawBtn = document.getElementById('live-layer-raw');
  const annBtn = document.getElementById('live-layer-ann');
  if (rawBtn) rawBtn.classList.toggle('active', currentLiveLayer === 'raw');
  if (annBtn) annBtn.classList.toggle('active', currentLiveLayer === 'annotated');
}

// Запуск и открытие модального окна Live-режима
export async function openLiveModal(camera) {
  if (!camera || !camera.id) return;
  activeCamera = camera;
  currentActiveTab = null;
  currentCameraControls = null;
  initialCameraSnapshot = null;
  draftRgb = { r: 1.0, g: 1.0, b: 1.0 };
  currentLiveLayer = 'raw';
  deviceOfflineShown = false;
  isClosingFlow = false;
  updateLiveLayerButtonsUi();
  applyLiveRgbFilter(draftRgb);

  const modal = document.getElementById('modal-camera-live');
  const titleEl = document.getElementById('live-modal-title');
  const pausedOverlay = document.getElementById('live-paused-overlay');
  const fpsEl = document.getElementById('live-fps');
  const latEl = document.getElementById('live-latency');
  const resEl = document.getElementById('live-res');
  if (!modal) return;

  if (titleEl) {
    titleEl.textContent = `Live: ${formatCameraDisplayName(camera)}`;
  }
  if (fpsEl) fpsEl.textContent = '0.0';
  if (latEl) latEl.textContent = '0';
  if (resEl) resEl.textContent = '\u2014';
  if (pausedOverlay) pausedOverlay.classList.add('hidden');

  setLiveViewportState('loading');
  renderLiveSettingsOverlay();
  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  try {
    const startRes = await api.startLive(camera.id);
    if (!activeCamera || activeCamera.id !== camera.id) return;
    if (resEl && startRes && startRes.resolution) {
      resEl.textContent = startRes.resolution;
    }
    attachStreamHandlers(camera.id, currentLiveLayer);
  } catch (err) {
    setLiveViewportState('error', err.message || 'Ошибка запуска Live-стрима');
  }

  if (liveStatusInterval) clearInterval(liveStatusInterval);
  liveStatusInterval = setInterval(updateLiveMetrics, 1000);
  updateLiveMetrics();

  loadCameraControls(camera.id, true);
}

function hideUnsavedModal() {
  const modal = document.getElementById('modal-live-unsaved');
  if (modal) modal.classList.add('hidden');
}

function showUnsavedModal() {
  const modal = document.getElementById('modal-live-unsaved');
  if (modal) modal.classList.remove('hidden');
}

// Проверка наличия несохранённых изменений V4L2, формата, разрешения или R/G/B
export function hasUnsavedLiveChanges() {
  if (!initialCameraSnapshot || !currentCameraControls) return false;

  if (
    Math.abs(draftRgb.r - initialCameraSnapshot.rgb.r) >= 0.005 ||
    Math.abs(draftRgb.g - initialCameraSnapshot.rgb.g) >= 0.005 ||
    Math.abs(draftRgb.b - initialCameraSnapshot.rgb.b) >= 0.005
  ) {
    return true;
  }

  if (
    (currentCameraControls.current_format || null) !== initialCameraSnapshot.format ||
    (currentCameraControls.current_resolution || null) !== initialCameraSnapshot.resolution
  ) {
    return true;
  }

  const controlsList = currentCameraControls.controls || [];
  for (const ctrl of controlsList) {
    if (!ctrl || !ctrl.name) continue;
    if (ctrl.name in initialCameraSnapshot.controls) {
      if (ctrl.value !== initialCameraSnapshot.controls[ctrl.name]) {
        return true;
      }
    }
  }
  return false;
}

// Запрос закрытия Live-окна (с проверкой несохранённых изменений)
export function requestCloseLiveModal() {
  if (isClosingFlow) return;
  if (hasUnsavedLiveChanges()) {
    showUnsavedModal();
    return;
  }
  closeLiveModal();
}

// Откат изменений к исходным значениям и закрытие Live
async function discardLiveChangesAndClose() {
  if (isClosingFlow) return;
  isClosingFlow = true;
  hideUnsavedModal();

  try {
    if (activeCamera && activeCamera.id && initialCameraSnapshot && currentCameraControls) {
      const changedControls = {};
      (currentCameraControls.controls || []).forEach((ctrl) => {
        if (
          ctrl &&
          ctrl.name &&
          ctrl.name in initialCameraSnapshot.controls &&
          ctrl.value !== initialCameraSnapshot.controls[ctrl.name]
        ) {
          changedControls[ctrl.name] = initialCameraSnapshot.controls[ctrl.name];
        }
      });

      const fmtChanged =
        (currentCameraControls.current_format || null) !== initialCameraSnapshot.format;
      const resChanged =
        (currentCameraControls.current_resolution || null) !== initialCameraSnapshot.resolution;

      // При откате сбрасываем сначала триггеры, выключающие auto, затем значения, затем auto=1
      const manualTriggers = {};
      const regularCtrls = {};
      const autoTriggers = {};
      Object.entries(changedControls).forEach(([k, v]) => {
        if (DEPENDENT_TRIGGERS.has(k)) {
          if (Number(v) === 0 || v === false) {
            manualTriggers[k] = v;
          } else {
            autoTriggers[k] = v;
          }
        } else {
          regularCtrls[k] = v;
        }
      });

      if (Object.keys(manualTriggers).length > 0) {
        await api.updateCameraControls(activeCamera.id, { controls: manualTriggers });
      }
      if (Object.keys(regularCtrls).length > 0 || fmtChanged || resChanged) {
        const payload = {};
        if (Object.keys(regularCtrls).length > 0) payload.controls = regularCtrls;
        if (fmtChanged && initialCameraSnapshot.format) payload.format = initialCameraSnapshot.format;
        if (resChanged && initialCameraSnapshot.resolution) payload.resolution = initialCameraSnapshot.resolution;
        await api.updateCameraControls(activeCamera.id, payload);
      }
      if (Object.keys(autoTriggers).length > 0) {
        await api.updateCameraControls(activeCamera.id, { controls: autoTriggers });
      }
    }
  } catch (err) {
    showToast(`Не удалось полностью откатить настройки: ${err.message}`, 'error');
  } finally {
    isClosingFlow = false;
    await closeLiveModal();
  }
}

// Сохранение профиля камеры и (при scope === 'all_gallery') переприменение RGB к галерее
async function saveLiveChangesAndClose(scope) {
  if (isClosingFlow) return;
  isClosingFlow = true;
  hideUnsavedModal();

  try {
    if (activeCamera && activeCamera.id && currentCameraControls) {
      const controlsMap = {};
      (currentCameraControls.controls || []).forEach((c) => {
        if (c && c.name && c.value !== undefined && c.value !== null) {
          controlsMap[c.name] = c.value;
        }
      });
      await api.saveCameraControls(activeCamera.id, {
        scope,
        controls: controlsMap,
        format: currentCameraControls.current_format || null,
        resolution: currentCameraControls.current_resolution || null,
        rgb: { ...draftRgb },
      });
      if (typeof onLiveProfileSavedCallback === 'function') {
        await onLiveProfileSavedCallback(scope);
      }
      showToast(
        scope === 'all_gallery'
          ? 'Настройки сохранены и применены ко всей галерее'
          : 'Настройки сохранены для будущих снимков'
      );
    }
  } catch (err) {
    showError(err.message || 'Не удалось сохранить настройки камеры');
    isClosingFlow = false;
    return;
  }
  isClosingFlow = false;
  await closeLiveModal();
}

// Безусловная остановка и закрытие Live-режима
export async function closeLiveModal() {
  hideUnsavedModal();
  const modal = document.getElementById('modal-camera-live');
  const streamImg = document.getElementById('live-stream-img');
  if (modal) modal.classList.add('hidden');
  document.body.classList.remove('modal-open');

  if (liveStatusInterval) {
    clearInterval(liveStatusInterval);
    liveStatusInterval = null;
  }
  if (liveConnectTimeout) {
    clearTimeout(liveConnectTimeout);
    liveConnectTimeout = null;
  }

  streamReady = false;
  deviceOfflineShown = false;
  currentActiveTab = null;
  currentLiveLayer = 'raw';
  initialCameraSnapshot = null;
  currentCameraControls = null;
  draftRgb = { r: 1.0, g: 1.0, b: 1.0 };
  applyLiveRgbFilter(draftRgb);

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

// Периодическое обновление FPS, задержки, разрешения, паузы и статуса подключения камеры
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

    if (status.device_online === false) {
      if (!deviceOfflineShown) {
        deviceOfflineShown = true;
        if (liveConnectTimeout) {
          clearTimeout(liveConnectTimeout);
          liveConnectTimeout = null;
        }
        setLiveViewportState('error', status.stream_error || 'Камера отключена');
      }
    } else if (deviceOfflineShown && status.is_active) {
      deviceOfflineShown = false;
      attachStreamHandlers(activeCamera.id, currentLiveLayer);
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

    if (!initialCameraSnapshot && data) {
      const ctrlMap = {};
      (data.controls || []).forEach((c) => {
        if (c && c.name) {
          ctrlMap[c.name] = c.value;
        }
      });
      const initRgb = {
        r: clampRgbValue(data.rgb?.r, 1.0),
        g: clampRgbValue(data.rgb?.g, 1.0),
        b: clampRgbValue(data.rgb?.b, 1.0),
      };
      initialCameraSnapshot = {
        format: data.current_format || null,
        resolution: data.current_resolution || null,
        controls: ctrlMap,
        rgb: initRgb,
      };
      draftRgb = { ...initRgb };
      applyLiveRgbFilter(draftRgb);
    }

    const resEl = document.getElementById('live-res');
    if (resEl && data && data.current_resolution && (!resEl.textContent || resEl.textContent === '\u2014')) {
      resEl.textContent = data.current_resolution;
    }
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

function isBinaryControl(ctrl) {
  if (!ctrl) return false;
  if (ctrl.type === 'bool') return true;
  if (ctrl.type === 'int' || ctrl.type === 'int64') {
    const minV = Number(ctrl.min);
    const maxV = Number(ctrl.max);
    const stepV = ctrl.step !== undefined && ctrl.step !== null ? Number(ctrl.step) : 1;
    return minV === 0 && maxV === 1 && stepV === 1;
  }
  return false;
}

function formatControlValue(ctrlName, rawVal) {
  const num = Number(rawVal);
  if (ctrlName === 'exposure_time_absolute' && Number.isFinite(num)) {
    const ms = Number((num * 0.1).toFixed(1));
    return `${ms} мс`;
  }
  if (ctrlName === 'white_balance_temperature' && Number.isFinite(num)) {
    return `${Math.round(num)} К`;
  }
  return String(rawVal);
}

function formatMenuItemLabel(ctrlName, label) {
  const raw = String(label || '').trim();
  if (ctrlName === 'power_line_frequency') {
    if (/disabled|off|откл/i.test(raw)) return 'Откл';
    return raw.replace(/\bHz\b/gi, 'Гц');
  }
  if (ctrlName === 'auto_exposure') {
    if (/aperture\s*priority/i.test(raw)) return 'Приоритет';
    if (/manual/i.test(raw)) return 'Ручной';
    if (/auto/i.test(raw)) return 'Авто';
  }
  if (ctrlName === 'led1_mode') {
    if (/^off$/i.test(raw)) return 'Выкл';
    if (/^on$/i.test(raw)) return 'Вкл';
    if (/blink/i.test(raw)) return 'Мигание';
    if (/auto/i.test(raw)) return 'Авто';
  }
  return raw;
}

function createSliderWithDefault({
  min,
  max,
  step,
  value,
  defaultVal,
  disabled,
  ctrlName,
  formatValFn,
  onInputVal,
  onCommitVal,
}) {
  const wrap = document.createElement('div');
  wrap.className = 'live-slider-wrap';

  const slider = document.createElement('input');
  slider.type = 'range';
  slider.className = 'live-pill-slider';
  if (ctrlName) slider.dataset.cname = ctrlName;
  slider.min = String(min);
  slider.max = String(max);
  slider.step = String(step);
  slider.value = String(value);
  if (disabled) slider.disabled = true;

  wrap.appendChild(slider);

  const hasDefault = defaultVal !== null && defaultVal !== undefined && Number.isFinite(Number(defaultVal));
  if (hasDefault && max > min) {
    const defNum = Math.max(min, Math.min(max, Number(defaultVal)));
    const ratio = (defNum - min) / (max - min);
    const mark = document.createElement('span');
    mark.className = 'live-slider-default-mark';
    mark.style.left = `calc(${(ratio * 100).toFixed(2)}% + ${(8 - ratio * 16).toFixed(2)}px)`;
    mark.title = `По умолчанию: ${formatValFn ? formatValFn(defNum) : defNum}`;
    wrap.appendChild(mark);
  }

  slider.addEventListener('input', (e) => {
    if (onInputVal) onInputVal(Number(e.target.value));
  });
  slider.addEventListener('change', (e) => {
    if (onCommitVal) onCommitVal(Number(e.target.value));
  });

  if (hasDefault && !disabled) {
    slider.addEventListener('dblclick', () => {
      const defNum = Math.max(min, Math.min(max, Number(defaultVal)));
      slider.value = String(defNum);
      if (onInputVal) onInputVal(defNum);
      if (onCommitVal) onCommitVal(defNum);
    });
  }

  return { wrap, slider };
}

function appendResetButton(head, defaultVal, disabled, onReset) {
  if (defaultVal === null || defaultVal === undefined || !Number.isFinite(Number(defaultVal))) {
    return null;
  }
  const resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.className = 'live-pill-reset-btn';
  resetBtn.title = 'Сбросить на значение по умолчанию';
  resetBtn.setAttribute('aria-label', 'Сбросить на значение по умолчанию');
  resetBtn.innerHTML = RESET_ICON_SVG;
  if (disabled) resetBtn.disabled = true;
  resetBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!resetBtn.disabled) onReset();
  });
  head.appendChild(resetBtn);
  return resetBtn;
}

// Отрисовка компактных плашек настроек выбранной категории в одну строку
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

  const { formats, controls, current_format, current_resolution } = currentCameraControls;

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
    } else if (availFormats.length <= 3) {
      const seg = document.createElement('div');
      seg.className = 'segmented-control live-pill-segmented';
      availFormats.forEach((f) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `seg-btn ${f.format === activeFmt ? 'active' : ''}`;
        btn.textContent = f.format;
        if (availFormats.length <= 1) btn.disabled = true;
        btn.addEventListener('click', async () => {
          if (f.format === currentCameraControls.current_format) return;
          const newFmt = f.format;
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
        seg.appendChild(btn);
      });
      fmtPill.appendChild(seg);
    } else {
      const selFmt = document.createElement('select');
      selFmt.id = 'ctrl-select-fmt';
      selFmt.className = 'live-pill-select';
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

    // Компактные плашки программной RGB-коррекции (0.00 - 1.00, шаг 0.01, default 1.00)
    const rgbChannels = [
      { id: 'r', title: 'Канал R', value: clampRgbValue(draftRgb.r, 1.0), accentClass: 'rgb-r' },
      { id: 'g', title: 'Канал G', value: clampRgbValue(draftRgb.g, 1.0), accentClass: 'rgb-g' },
      { id: 'b', title: 'Канал B', value: clampRgbValue(draftRgb.b, 1.0), accentClass: 'rgb-b' },
    ];

    rgbChannels.forEach((ch) => {
      const pill = createControlPill(`<span class="${ch.accentClass}">${ch.title}</span>`);
      const head = pill.querySelector('.live-pill-head');

      const headRight = document.createElement('div');
      headRight.className = 'live-pill-head-right';

      const valSpan = document.createElement('span');
      valSpan.className = 'live-pill-val';
      valSpan.id = `val-rgb-${ch.id}`;
      valSpan.textContent = ch.value.toFixed(2);
      headRight.appendChild(valSpan);

      const { wrap, slider } = createSliderWithDefault({
        min: 0.0,
        max: 1.0,
        step: 0.01,
        value: ch.value,
        defaultVal: 1.0,
        disabled: false,
        ctrlName: `rgb_${ch.id}`,
        formatValFn: (v) => Number(v).toFixed(2),
        onInputVal: (val) => {
          const clamped = clampRgbValue(val, 1.0);
          draftRgb[ch.id] = clamped;
          valSpan.textContent = clamped.toFixed(2);
          applyLiveRgbFilter(draftRgb);
        },
        onCommitVal: (val) => {
          const clamped = clampRgbValue(val, 1.0);
          draftRgb[ch.id] = clamped;
          valSpan.textContent = clamped.toFixed(2);
          applyLiveRgbFilter(draftRgb);
        },
      });
      slider.id = `slider-rgb-${ch.id}`;

      appendResetButton(headRight, 1.0, false, () => {
        slider.value = '1';
        draftRgb[ch.id] = 1.0;
        valSpan.textContent = '1.00';
        applyLiveRgbFilter(draftRgb);
      });

      head.appendChild(headRight);
      pill.appendChild(wrap);
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

    if (isBinaryControl(ctrl)) {
      const pill = createControlPill(labelText, isInactive, 'live-pill-toggle');
      const isChecked = Boolean(Number(ctrl.value));

      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.role = 'switch';
      toggleBtn.setAttribute('aria-checked', isChecked ? 'true' : 'false');
      toggleBtn.dataset.cname = ctrl.name;
      toggleBtn.className = `live-toggle-btn ${isChecked ? 'is-on' : ''}`;
      if (isInactive) toggleBtn.disabled = true;
      toggleBtn.innerHTML = `
        <span class="live-toggle-track"><span class="live-toggle-thumb"></span></span>
        <span class="live-toggle-text">${isChecked ? 'Вкл' : 'Выкл'}</span>
      `;

      toggleBtn.addEventListener('click', async () => {
        if (toggleBtn.disabled) return;
        const nextChecked = toggleBtn.getAttribute('aria-checked') !== 'true';
        const val = nextChecked ? 1 : 0;
        toggleBtn.setAttribute('aria-checked', nextChecked ? 'true' : 'false');
        toggleBtn.classList.toggle('is-on', nextChecked);
        const txt = toggleBtn.querySelector('.live-toggle-text');
        if (txt) txt.textContent = nextChecked ? 'Вкл' : 'Выкл';
        await sendControlChange(ctrl.name, val);
      });

      pill.appendChild(toggleBtn);
      track.appendChild(pill);
    } else if (ctrl.type === 'menu' && ctrl.menu_items && ctrl.menu_items.length > 0) {
      if (ctrl.menu_items.length <= 3) {
        const pill = createControlPill(labelText, isInactive, 'live-pill-wide');
        const seg = document.createElement('div');
        seg.className = 'segmented-control live-pill-segmented';
        seg.dataset.cname = ctrl.name;

        ctrl.menu_items.forEach((m) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = `seg-btn ${m.id === ctrl.value ? 'active' : ''}`;
          btn.dataset.value = String(m.id);
          const shortLabel = formatMenuItemLabel(ctrl.name, m.label);
          btn.textContent = shortLabel;
          btn.title = m.label;
          if (isInactive) btn.disabled = true;
          btn.addEventListener('click', async () => {
            if (isInactive || m.id === ctrl.value) return;
            seg.querySelectorAll('.seg-btn').forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
            await sendControlChange(ctrl.name, Number(m.id));
          });
          seg.appendChild(btn);
        });

        pill.appendChild(seg);
        track.appendChild(pill);
      } else {
        const pill = createControlPill(labelText, isInactive);
        const sel = document.createElement('select');
        sel.className = 'live-pill-select';
        sel.dataset.cname = ctrl.name;
        if (isInactive) sel.disabled = true;
        sel.innerHTML = ctrl.menu_items
          .map(
            (m) =>
              `<option value="${m.id}" ${m.id === ctrl.value ? 'selected' : ''}>${formatMenuItemLabel(ctrl.name, m.label)}</option>`
          )
          .join('');
        sel.addEventListener('change', async (e) => {
          await sendControlChange(ctrl.name, parseInt(e.target.value, 10));
        });
        pill.appendChild(sel);
        track.appendChild(pill);
      }
    } else {
      const minVal = ctrl.min !== null && ctrl.min !== undefined ? Number(ctrl.min) : 0;
      const maxVal = ctrl.max !== null && ctrl.max !== undefined ? Number(ctrl.max) : 100;
      const stepVal = ctrl.step ? Number(ctrl.step) : 1;
      const curVal = ctrl.value !== null && ctrl.value !== undefined ? Number(ctrl.value) : minVal;
      const defVal = ctrl.default !== null && ctrl.default !== undefined ? Number(ctrl.default) : null;

      const pill = createControlPill(labelText, isInactive);
      const head = pill.querySelector('.live-pill-head');

      const headRight = document.createElement('div');
      headRight.className = 'live-pill-head-right';

      const valDisplay = document.createElement('span');
      valDisplay.className = 'live-pill-val';
      valDisplay.id = `val-${ctrl.name}`;
      valDisplay.textContent = formatControlValue(ctrl.name, curVal);
      headRight.appendChild(valDisplay);

      const { wrap, slider } = createSliderWithDefault({
        min: minVal,
        max: maxVal,
        step: stepVal,
        value: curVal,
        defaultVal: defVal,
        disabled: isInactive,
        ctrlName: ctrl.name,
        formatValFn: (v) => formatControlValue(ctrl.name, v),
        onInputVal: (val) => {
          valDisplay.textContent = formatControlValue(ctrl.name, val);
        },
        onCommitVal: async (val) => {
          valDisplay.textContent = formatControlValue(ctrl.name, val);
          await sendControlChange(ctrl.name, Math.round(val));
        },
      });

      appendResetButton(headRight, defVal, isInactive, async () => {
        const clamped = Math.max(minVal, Math.min(maxVal, defVal));
        slider.value = String(clamped);
        valDisplay.textContent = formatControlValue(ctrl.name, clamped);
        await sendControlChange(ctrl.name, Math.round(clamped));
      });

      head.appendChild(headRight);
      pill.appendChild(wrap);
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
    white_balance_temperature: 'Температура ББ',
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
    led1_mode: 'Индикатор',
  };
  return titles[name] || name;
}

// Скачивание свежего JPEG с учётом текущих коэффициентов R/G/B
async function downloadLiveSnapshot() {
  if (!activeCamera || !activeCamera.id) return;
  const btn = document.getElementById('btn-live-download-snapshot');
  if (btn) btn.disabled = true;
  try {
    const r = clampRgbValue(draftRgb.r, 1.0).toFixed(2);
    const g = clampRgbValue(draftRgb.g, 1.0).toFixed(2);
    const b = clampRgbValue(draftRgb.b, 1.0).toFixed(2);
    const url = `/api/cameras/${activeCamera.id}/live/snapshot?r=${r}&g=${g}&b=${b}&t=${Date.now()}`;
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

async function switchLiveLayer(nextMode) {
  if (!activeCamera || !activeCamera.id) return;
  const normalized = nextMode === 'annotated' ? 'annotated' : 'raw';
  if (normalized === currentLiveLayer) return;
  currentLiveLayer = normalized;
  updateLiveLayerButtonsUi();
  try {
    await api.setLiveMode(activeCamera.id, normalized);
  } catch (_) {
    // поток сам передаст query ?mode=...
  }
  if (activeCamera && activeCamera.id) {
    attachStreamHandlers(activeCamera.id, normalized);
  }
}

// Инициализация модуля камер
export function initCameras() {
  const btnMenu = document.getElementById('btn-cameras-menu');
  const modalMenu = document.getElementById('modal-cameras-menu');
  const modalMenuClose = document.getElementById('modal-cameras-menu-close');
  const modalLive = document.getElementById('modal-camera-live');
  const modalLiveClose = document.getElementById('modal-camera-live-close');
  const btnDownloadSnap = document.getElementById('btn-live-download-snapshot');
  const btnLiveRetry = document.getElementById('btn-live-retry');
  const layerRawBtn = document.getElementById('live-layer-raw');
  const layerAnnBtn = document.getElementById('live-layer-ann');

  const modalUnsaved = document.getElementById('modal-live-unsaved');
  const unsavedCloseBtn = document.getElementById('modal-live-unsaved-close');
  const unsavedDiscardBtn = document.getElementById('btn-live-unsaved-discard');
  const unsavedNextBtn = document.getElementById('btn-live-unsaved-next');
  const unsavedGalleryBtn = document.getElementById('btn-live-unsaved-gallery');

  if (btnMenu) {
    btnMenu.addEventListener('click', openCamerasModal);
  }
  if (modalMenuClose) {
    modalMenuClose.addEventListener('click', closeCamerasModal);
  }
  if (modalMenu) {
    modalMenu.addEventListener('click', (e) => {
      if (e.target === modalMenu) closeCamerasModal();
    });
  }

  if (modalLiveClose) {
    modalLiveClose.addEventListener('click', requestCloseLiveModal);
  }
  if (modalLive) {
    modalLive.addEventListener('click', (e) => {
      if (e.target === modalLive) requestCloseLiveModal();
    });
  }

  if (layerRawBtn) {
    layerRawBtn.addEventListener('click', () => switchLiveLayer('raw'));
  }
  if (layerAnnBtn) {
    layerAnnBtn.addEventListener('click', () => switchLiveLayer('annotated'));
  }

  if (unsavedCloseBtn) {
    unsavedCloseBtn.addEventListener('click', hideUnsavedModal);
  }
  if (modalUnsaved) {
    modalUnsaved.addEventListener('click', (e) => {
      if (e.target === modalUnsaved) hideUnsavedModal();
    });
  }
  if (unsavedDiscardBtn) {
    unsavedDiscardBtn.addEventListener('click', discardLiveChangesAndClose);
  }
  if (unsavedNextBtn) {
    unsavedNextBtn.addEventListener('click', () => saveLiveChangesAndClose('next_frame'));
  }
  if (unsavedGalleryBtn) {
    unsavedGalleryBtn.addEventListener('click', () => saveLiveChangesAndClose('all_gallery'));
  }

  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (modalUnsaved && !modalUnsaved.classList.contains('hidden')) {
      e.preventDefault();
      hideUnsavedModal();
      return;
    }
    if (modalLive && !modalLive.classList.contains('hidden')) {
      e.preventDefault();
      requestCloseLiveModal();
      return;
    }
    if (modalMenu && !modalMenu.classList.contains('hidden')) {
      e.preventDefault();
      closeCamerasModal();
    }
  });

  if (btnDownloadSnap) {
    btnDownloadSnap.addEventListener('click', downloadLiveSnapshot);
  }
  if (btnLiveRetry) {
    btnLiveRetry.addEventListener('click', async () => {
      if (!activeCamera || !activeCamera.id) return;
      deviceOfflineShown = false;
      setLiveViewportState('loading');
      try {
        const startRes = await api.startLive(activeCamera.id);
        const resEl = document.getElementById('live-res');
        if (resEl && startRes && startRes.resolution) {
          resEl.textContent = startRes.resolution;
        }
        if (currentLiveLayer === 'annotated') {
          await api.setLiveMode(activeCamera.id, 'annotated');
        }
        attachStreamHandlers(activeCamera.id, currentLiveLayer);
      } catch (err) {
        setLiveViewportState('error', err.message || 'Стрим недоступен');
      }
    });
  }
}
