// clients/web/js/cameras.js
// Меню камер, Live-режим и управление настройками V4L2/RGB в Web UI

import { api } from './api.js';
import { showError, showToast } from './dialogs.js';

let currentRole = 'guest';
let activeCamera = null;
let liveStatusInterval = null;
let liveSettingsVisible = false;
let currentCameraControls = null;
let currentActiveTab = 'format';

export function updateCamerasRole(role) {
  currentRole = role || 'guest';
  const btn = document.getElementById('btn-cameras-menu');
  if (btn) {
    btn.classList.toggle('hidden', currentRole === 'guest');
  }
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
      const instName = cam.installation_name
        ? `Установка: ${cam.installation_name}`
        : 'Не привязана';
      const hwText = cam.hardware_id ? `<span class="camera-hw-id">${cam.hardware_id}</span>` : '';

      card.innerHTML = `
        <div class="camera-card-header">
          <div class="camera-title-group">
            <span class="status-dot ${isOnline ? 'online' : 'offline'}"></span>
            <strong class="camera-name">${cam.auto_name || 'Камера #' + cam.id}</strong>
          </div>
          <span class="camera-status-text">${isOnline ? 'Онлайн' : 'Офлайн'}</span>
        </div>
        <div class="camera-card-details">
          <div class="camera-inst-badge">${instName}</div>
          ${hwText}
          ${cam.calibration_summary ? `<div class="camera-calib-text">${cam.calibration_summary}</div>` : ''}
        </div>
        <div class="camera-card-actions">
          <button type="button" class="btn btn-primary btn-cam-live" ${!isOnline ? 'disabled' : ''}>
            <svg class="icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="23 7 16 12 23 7"/>
              <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
            </svg>
            <span>Live</span>
          </button>
          <button type="button" class="btn btn-secondary btn-cam-settings" ${!isOnline ? 'disabled' : ''}>
            <svg class="icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
            <span>Настройки</span>
          </button>
        </div>
      `;

      const liveBtn = card.querySelector('.btn-cam-live');
      if (liveBtn) {
        liveBtn.addEventListener('click', () => {
          closeCamerasModal();
          openLiveModal(cam, false);
        });
      }

      const settingsBtn = card.querySelector('.btn-cam-settings');
      if (settingsBtn) {
        settingsBtn.addEventListener('click', () => {
          closeCamerasModal();
          openLiveModal(cam, true);
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

// Запуск и открытие модального окна Live-режима
export async function openLiveModal(camera, startWithSettings = false) {
  if (!camera || !camera.id) return;
  activeCamera = camera;

  const modal = document.getElementById('modal-camera-live');
  const titleEl = document.getElementById('live-modal-title');
  const streamImg = document.getElementById('live-stream-img');
  const pausedOverlay = document.getElementById('live-paused-overlay');
  const settingsDrawer = document.getElementById('live-settings-drawer');
  if (!modal || !streamImg) return;

  if (titleEl) {
    titleEl.textContent = `Live: ${camera.auto_name || 'Камера #' + camera.id}`;
  }

  // Сброс оверлеев и параметров
  if (pausedOverlay) pausedOverlay.classList.add('hidden');
  liveSettingsVisible = Boolean(startWithSettings);
  if (settingsDrawer) {
    settingsDrawer.classList.toggle('hidden', !liveSettingsVisible);
  }

  modal.classList.remove('hidden');

  try {
    // 1. Старт стрима на сервере
    await api.startLive(camera.id);
    // 2. Подключение low-latency потока
    streamImg.src = `/api/cameras/${camera.id}/live/stream?t=${Date.now()}`;
    streamImg.onerror = () => {
      // При временном разрыве стрима не сбрасываем модалку
    };
  } catch (err) {
    showError(err.message || 'Ошибка запуска Live-стрима');
  }

  // 3. Запуск опроса метрик (FPS, задержка, статус паузы)
  if (liveStatusInterval) clearInterval(liveStatusInterval);
  liveStatusInterval = setInterval(updateLiveMetrics, 1000);
  updateLiveMetrics();

  // 4. Загрузка настроек камеры
  loadCameraControls(camera.id);
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

  if (streamImg) {
    streamImg.src = '';
  }

  if (activeCamera && activeCamera.id) {
    try {
      await api.stopLive(activeCamera.id);
    } catch (_) {
      // игнорируем ошибку при закрытии
    }
    activeCamera = null;
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
    if (latEl) latEl.textContent = Math.round(Number(status.latency_ms || 0));
    if (resEl) resEl.textContent = status.resolution || '1280x720';

    if (pausedOverlay) {
      pausedOverlay.classList.toggle('hidden', !Boolean(status.is_paused));
    }
  } catch (_) {
    // игнорируем сетевые сбои поллинга статуса
  }
}

// Загрузка доступных контролов камеры и отрисовка панели
async function loadCameraControls(cameraId) {
  const container = document.getElementById('live-settings-content');
  if (!container) return;

  try {
    const data = await api.getCameraControls(cameraId);
    currentCameraControls = data;
    renderControlsTabs();
  } catch (err) {
    if (container) {
      container.innerHTML = `<div class="form-error-inline">${err.message || 'Ошибка загрузки контролов'}</div>`;
    }
  }
}

// Отрисовка вкладок и содержимого настроек
function renderControlsTabs() {
  const container = document.getElementById('live-settings-content');
  if (!container || !currentCameraControls) return;

  const { formats, controls, rgb, current_format, current_resolution } = currentCameraControls;

  container.innerHTML = `
    <div class="settings-tabs-header">
      <button type="button" class="tab-folder-btn ${currentActiveTab === 'format' ? 'active' : ''}" data-tab="format">Разрешение и формат</button>
      <button type="button" class="tab-folder-btn ${currentActiveTab === 'exposure' ? 'active' : ''}" data-tab="exposure">Экспозиция</button>
      <button type="button" class="tab-folder-btn ${currentActiveTab === 'color' ? 'active' : ''}" data-tab="color">Цвет и баланс</button>
      <button type="button" class="tab-folder-btn ${currentActiveTab === 'focus' ? 'active' : ''}" data-tab="focus">Фокус и оптика</button>
    </div>
    <div class="settings-tab-pane" id="settings-tab-pane"></div>
  `;

  // Навешиваем слушатели на кнопки папок
  const tabBtns = container.querySelectorAll('.tab-folder-btn');
  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      currentActiveTab = btn.dataset.tab;
      tabBtns.forEach((b) => b.classList.toggle('active', b.dataset.tab === currentActiveTab));
      renderTabPaneContent();
    });
  });

  renderTabPaneContent();
}

// Отрисовка содержимого выбранной вкладки
function renderTabPaneContent() {
  const pane = document.getElementById('settings-tab-pane');
  if (!pane || !currentCameraControls) return;

  const { formats, controls, rgb, current_format, current_resolution } = currentCameraControls;
  pane.innerHTML = '';

  if (currentActiveTab === 'format') {
    // 1. Папка: Разрешение и формат
    const formGroup = document.createElement('div');
    formGroup.className = 'control-group';

    const allResolutions = [];
    (formats || []).forEach((f) => {
      (f.resolutions || []).forEach((r) => {
        if (!allResolutions.includes(r)) allResolutions.push(r);
      });
    });

    const isSingleRes = allResolutions.length <= 1;
    const isSingleFmt = (formats || []).length <= 1;

    let resOptions = allResolutions
      .map(
        (r) => `<option value="${r}" ${r === current_resolution ? 'selected' : ''}>${r}</option>`
      )
      .join('');

    let fmtOptions = (formats || [])
      .map(
        (f) =>
          `<option value="${f.format}" ${f.format === current_format ? 'selected' : ''}>${f.format}</option>`
      )
      .join('');

    formGroup.innerHTML = `
      <label class="control-label">Разрешение видео</label>
      <select id="ctrl-select-res" class="form-input" ${isSingleRes ? 'disabled' : ''}>
        ${resOptions}
      </select>

      <label class="control-label" style="margin-top: 12px;">Кодек / Формат</label>
      <select id="ctrl-select-fmt" class="form-input" ${isSingleFmt ? 'disabled' : ''}>
        ${fmtOptions}
      </select>
    `;

    const selectRes = formGroup.querySelector('#ctrl-select-res');
    if (selectRes) {
      selectRes.addEventListener('change', async (e) => {
        const newRes = e.target.value;
        try {
          await api.updateCameraControls(activeCamera.id, { resolution: newRes });
          currentCameraControls.current_resolution = newRes;
          showToast(`Разрешение изменено на ${newRes}`);
        } catch (err) {
          showError(err.message || 'Ошибка применения разрешения');
        }
      });
    }

    const selectFmt = formGroup.querySelector('#ctrl-select-fmt');
    if (selectFmt) {
      selectFmt.addEventListener('change', async (e) => {
        const newFmt = e.target.value;
        try {
          await api.updateCameraControls(activeCamera.id, { format: newFmt });
          currentCameraControls.current_format = newFmt;
          showToast(`Формат изменен на ${newFmt}`);
        } catch (err) {
          showError(err.message || 'Ошибка применения формата');
        }
      });
    }

    pane.appendChild(formGroup);
  } else if (currentActiveTab === 'exposure') {
    // 2. Папка: Экспозиция (авто/ручная, выдержка, gain, яркость, контраст)
    const exposureControls = (controls || []).filter((c) => c.category === 'exposure');
    renderV4L2ControlsList(pane, exposureControls);
  } else if (currentActiveTab === 'color') {
    // 3. Папка: Цвет и баланс (авто ББ, температура, насыщенность + RGB ползунки)
    const colorControls = (controls || []).filter((c) => c.category === 'color');
    renderV4L2ControlsList(pane, colorControls);

    // Добавляем ползунки R / G / B
    const rgbBox = document.createElement('div');
    rgbBox.className = 'control-group rgb-control-box';
    const curR = (rgb && rgb.r) || 1.0;
    const curG = (rgb && rgb.g) || 1.0;
    const curB = (rgb && rgb.b) || 1.0;

    rgbBox.innerHTML = `
      <div class="control-section-subtitle">Программная RGB-коррекция</div>
      <div class="rgb-slider-row">
        <label>Канал R: <span id="val-rgb-r">${curR.toFixed(2)}</span></label>
        <input type="range" id="slider-rgb-r" min="0.5" max="2.0" step="0.05" value="${curR}">
      </div>
      <div class="rgb-slider-row">
        <label>Канал G: <span id="val-rgb-g">${curG.toFixed(2)}</span></label>
        <input type="range" id="slider-rgb-g" min="0.5" max="2.0" step="0.05" value="${curG}">
      </div>
      <div class="rgb-slider-row">
        <label>Канал B: <span id="val-rgb-b">${curB.toFixed(2)}</span></label>
        <input type="range" id="slider-rgb-b" min="0.5" max="2.0" step="0.05" value="${curB}">
      </div>
    `;

    const applyRgb = async () => {
      const r = parseFloat(rgbBox.querySelector('#slider-rgb-r').value);
      const g = parseFloat(rgbBox.querySelector('#slider-rgb-g').value);
      const b = parseFloat(rgbBox.querySelector('#slider-rgb-b').value);
      rgbBox.querySelector('#val-rgb-r').textContent = r.toFixed(2);
      rgbBox.querySelector('#val-rgb-g').textContent = g.toFixed(2);
      rgbBox.querySelector('#val-rgb-b').textContent = b.toFixed(2);
      try {
        await api.updateCameraControls(activeCamera.id, { rgb: { r, g, b } });
        currentCameraControls.rgb = { r, g, b };
      } catch (err) {
        showError(err.message || 'Ошибка применения RGB');
      }
    };

    rgbBox.querySelectorAll('input[type="range"]').forEach((input) => {
      input.addEventListener('change', applyRgb);
    });

    pane.appendChild(rgbBox);
  } else if (currentActiveTab === 'focus') {
    // 4. Папка: Фокус и оптика (автофокус, фиксированный фокус, зум, резкость, 50 Гц)
    const focusControls = (controls || []).filter((c) => c.category === 'focus');

    // Проверяем наличие автофокуса
    const hasAutofocus = focusControls.some(
      (c) => c.name.includes('focus_auto') || c.name.includes('focus_automatic')
    );
    const hasAnyFocus = focusControls.some((c) => c.name.includes('focus'));

    // Если фокус отсутствует или фиксированный
    if (!hasAutofocus && !hasAnyFocus) {
      const fixFocusNotice = document.createElement('div');
      fixFocusNotice.className = 'control-group fixed-focus-notice';
      fixFocusNotice.innerHTML = `
        <label class="control-label">Фокусировка</label>
        <input type="text" class="form-input" value="Фиксированный фокус" disabled>
      `;
      pane.appendChild(fixFocusNotice);
    }

    renderV4L2ControlsList(pane, focusControls);
  }
}

// Рендеринг списка v4l2 контролов (слайдеры, чекбоксы, меню)
function renderV4L2ControlsList(container, controlsList) {
  if (!controlsList || controlsList.length === 0) {
    const emptyNotice = document.createElement('div');
    emptyNotice.className = 'control-group';
    emptyNotice.innerHTML = '<span class="control-label">Контролы данной категории не поддерживаются камерой</span>';
    container.appendChild(emptyNotice);
    return;
  }

  controlsList.forEach((ctrl) => {
    const group = document.createElement('div');
    group.className = 'control-group';

    const isInactive = Boolean(ctrl.is_inactive);
    const labelText = formatControlTitle(ctrl.name);

    if (ctrl.type === 'bool') {
      const isChecked = Boolean(ctrl.value);
      group.innerHTML = `
        <label class="checkbox-label ${isInactive ? 'disabled' : ''}">
          <input type="checkbox" data-cname="${ctrl.name}" ${isChecked ? 'checked' : ''} ${isInactive ? 'disabled' : ''}>
          <span>${labelText}</span>
        </label>
      `;
      const chk = group.querySelector('input[type="checkbox"]');
      if (chk) {
        chk.addEventListener('change', async (e) => {
          const val = e.target.checked ? 1 : 0;
          await sendControlChange(ctrl.name, val);
        });
      }
    } else if (ctrl.type === 'menu' && ctrl.menu_items && ctrl.menu_items.length > 0) {
      const options = ctrl.menu_items
        .map(
          (m) => `<option value="${m.id}" ${m.id === ctrl.value ? 'selected' : ''}>${m.label}</option>`
        )
        .join('');
      group.innerHTML = `
        <label class="control-label ${isInactive ? 'disabled' : ''}">${labelText}</label>
        <select class="form-input" data-cname="${ctrl.name}" ${isInactive ? 'disabled' : ''}>
          ${options}
        </select>
      `;
      const sel = group.querySelector('select');
      if (sel) {
        sel.addEventListener('change', async (e) => {
          await sendControlChange(ctrl.name, parseInt(e.target.value, 10));
        });
      }
    } else {
      // Слайдер (int)
      const minVal = ctrl.min !== null && ctrl.min !== undefined ? ctrl.min : 0;
      const maxVal = ctrl.max !== null && ctrl.max !== undefined ? ctrl.max : 100;
      const stepVal = ctrl.step || 1;
      const curVal = ctrl.value !== null && ctrl.value !== undefined ? ctrl.value : minVal;

      group.innerHTML = `
        <div class="slider-header ${isInactive ? 'disabled' : ''}">
          <label class="control-label">${labelText}</label>
          <span class="slider-val" id="val-${ctrl.name}">${curVal}</span>
        </div>
        <input type="range" class="control-slider" data-cname="${ctrl.name}" min="${minVal}" max="${maxVal}" step="${stepVal}" value="${curVal}" ${isInactive ? 'disabled' : ''}>
      `;
      const slider = group.querySelector('input[type="range"]');
      const valDisplay = group.querySelector(`#val-${ctrl.name}`);
      if (slider) {
        slider.addEventListener('input', (e) => {
          if (valDisplay) valDisplay.textContent = e.target.value;
        });
        slider.addEventListener('change', async (e) => {
          await sendControlChange(ctrl.name, parseInt(e.target.value, 10));
        });
      }
    }

    container.appendChild(group);
  });
}

// Отправка изменения v4l2 контрола на сервер
async function sendControlChange(ctrlName, value) {
  if (!activeCamera || !activeCamera.id) return;
  try {
    await api.updateCameraControls(activeCamera.id, {
      controls: { [ctrlName]: value },
    });
    // Обновляем локальное состояние контрола
    if (currentCameraControls && currentCameraControls.controls) {
      const c = currentCameraControls.controls.find((item) => item.name === ctrlName);
      if (c) c.value = value;
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
    exposure_time_absolute: 'Время выдержки',
    gain: 'Усиление (Gain)',
    focus_automatic_continuous: 'Автофокус (непрерывный)',
    focus_auto: 'Автофокус',
    focus_absolute: 'Ручной фокус',
    sharpness: 'Резкость',
    zoom_absolute: 'Зум / Увеличение',
    power_line_frequency: 'Частота сети (подавление мерцания)',
  };
  return titles[name] || name;
}

// Скачивание свежего JPEG напрямую без записи в БД
function downloadLiveSnapshot() {
  if (!activeCamera || !activeCamera.id) return;
  const url = `/api/cameras/${activeCamera.id}/live/snapshot?t=${Date.now()}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = `snapshot_${activeCamera.hardware_id || activeCamera.id}_${Date.now()}.jpg`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// Инициализация модуля камер
export function initCameras() {
  const btnMenu = document.getElementById('btn-cameras-menu');
  const modalMenuClose = document.getElementById('modal-cameras-menu-close');
  const modalLiveClose = document.getElementById('modal-camera-live-close');
  const btnToggleSettings = document.getElementById('btn-toggle-camera-settings');
  const btnDownloadSnap = document.getElementById('btn-live-download-snapshot');

  if (btnMenu) {
    btnMenu.addEventListener('click', openCamerasModal);
  }
  if (modalMenuClose) {
    modalMenuClose.addEventListener('click', closeCamerasModal);
  }
  if (modalLiveClose) {
    modalLiveClose.addEventListener('click', closeLiveModal);
  }

  if (btnToggleSettings) {
    btnToggleSettings.addEventListener('click', () => {
      liveSettingsVisible = !liveSettingsVisible;
      const drawer = document.getElementById('live-settings-drawer');
      if (drawer) {
        drawer.classList.toggle('hidden', !liveSettingsVisible);
      }
    });
  }

  if (btnDownloadSnap) {
    btnDownloadSnap.addEventListener('click', downloadLiveSnapshot);
  }
}
