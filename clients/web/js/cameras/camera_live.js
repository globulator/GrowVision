// clients/web/js/cameras/camera_live.js
// Live-стрим камеры, опрос статуса через PollingCoordinator, сохранение/откат профиля и общий механизм быстрого снимка

import { api } from '../api.js';
import { Modal, showError, showToast } from '../dialogs.js';
import { getLoadingSpinnerHTML } from '../placeholders.js';
import { pollingCoordinator } from '../components/polling.js';
import { clampRgbValue, createColorMatrixController } from './camera_color_matrix.js';
import { DEPENDENT_TRIGGERS, createV4l2Controller } from './camera_v4l2.js';

const LIVE_STARTUP_GRACE_MS = 5000;
const LIVE_POLL_TASK_NAME = 'camera-live-status';

let activeCamera = null;
let liveConnectTimeout = null;
let liveStreamRetryTimer = null;
let initialCameraSnapshot = null;
let currentLiveLayer = 'raw'; // 'raw' или 'annotated'
let currentSegmentationMode = 'auto'; // 'auto' | 'onnx' | 'hsv'
let streamReady = false;
let deviceOfflineShown = false;
let hasFirstLiveStatus = false;
let streamAttached = false;
let isClosingFlow = false;
let liveStartedAt = 0;
let onLiveProfileSavedCallback = null;

let liveModalCtrl = null;
let unsavedModalCtrl = null;

const colorMatrix = createColorMatrixController({
  getActiveCamera: () => activeCamera,
  getCurrentLiveLayer: () => currentLiveLayer,
});

const v4l2 = createV4l2Controller({
  getActiveCamera: () => activeCamera,
  colorMatrix,
  onControlsLoaded: (data) => {
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
      const initSegMode = normalizeSegmentationMode(data.segmentation_mode);
      initialCameraSnapshot = {
        format: data.current_format || null,
        resolution: data.current_resolution || null,
        controls: ctrlMap,
        rgb: initRgb,
        segmentation_mode: initSegMode,
      };
      currentSegmentationMode = initSegMode;
      updateSegmentationModeButtonsUi();
      colorMatrix.setDraftRgb(initRgb);
    } else if (data && data.segmentation_mode) {
      currentSegmentationMode = normalizeSegmentationMode(data.segmentation_mode);
      updateSegmentationModeButtonsUi();
    }
  },
});

export function formatCameraDisplayName(cam) {
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

export function setLiveProfileSavedHandler(fn) {
  onLiveProfileSavedCallback = fn;
}

function normalizeSegmentationMode(mode) {
  const raw = String(mode || 'auto').trim().toLowerCase();
  return raw === 'onnx' || raw === 'hsv' ? raw : 'auto';
}

function updateLiveLayerButtonsUi() {
  const rawBtn = document.getElementById('live-layer-raw');
  const annBtn = document.getElementById('live-layer-ann');
  if (rawBtn) rawBtn.classList.toggle('active', currentLiveLayer === 'raw');
  if (annBtn) annBtn.classList.toggle('active', currentLiveLayer === 'annotated');
}

function updateSegmentationModeButtonsUi() {
  const container = document.getElementById('live-seg-mode-control');
  if (!container) return;
  const activeMode = normalizeSegmentationMode(currentSegmentationMode);
  container.querySelectorAll('.seg-btn').forEach((btn) => {
    const isActive = btn.dataset.segMode === activeMode;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
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

function clearStreamTimers() {
  if (liveConnectTimeout) {
    clearTimeout(liveConnectTimeout);
    liveConnectTimeout = null;
  }
  if (liveStreamRetryTimer) {
    clearTimeout(liveStreamRetryTimer);
    liveStreamRetryTimer = null;
  }
  colorMatrix.clearRgbSyncTimer();
}

function attachStreamHandlers(cameraId, mode = currentLiveLayer) {
  const streamImg = document.getElementById('live-stream-img');
  if (!streamImg) return;
  if (deviceOfflineShown) return;

  clearStreamTimers();

  if (!streamReady) {
    setLiveViewportState('loading');
  }

  streamImg.onload = () => {
    if (!activeCamera || activeCamera.id !== cameraId) return;
    if (deviceOfflineShown) return;
    if (liveConnectTimeout) {
      clearTimeout(liveConnectTimeout);
      liveConnectTimeout = null;
    }
    if (liveStreamRetryTimer) {
      clearTimeout(liveStreamRetryTimer);
      liveStreamRetryTimer = null;
    }
    setLiveViewportState('streaming');
  };

  streamImg.onerror = () => {
    if (!activeCamera || activeCamera.id !== cameraId) return;
    if (!streamImg.getAttribute('src')) return;
    if (deviceOfflineShown) {
      setLiveViewportState('error', 'Камера отключена');
      return;
    }
    const inStartupGrace = !streamReady && (Date.now() - liveStartedAt) < LIVE_STARTUP_GRACE_MS;
    if (inStartupGrace) {
      setLiveViewportState('loading');
      if (liveStreamRetryTimer) clearTimeout(liveStreamRetryTimer);
      liveStreamRetryTimer = setTimeout(() => {
        liveStreamRetryTimer = null;
        if (activeCamera && activeCamera.id === cameraId && !streamReady && !deviceOfflineShown) {
          const sm = currentLiveLayer === 'annotated' ? 'annotated' : 'raw';
          streamImg.src = `/api/cameras/${cameraId}/live/stream?mode=${sm}&t=${Date.now()}`;
        }
      }, 400);
      return;
    }
    if (liveConnectTimeout) {
      clearTimeout(liveConnectTimeout);
      liveConnectTimeout = null;
    }
    setLiveViewportState('error', 'Стрим недоступен');
  };

  const streamMode = mode === 'annotated' ? 'annotated' : 'raw';
  streamImg.src = `/api/cameras/${cameraId}/live/stream?mode=${streamMode}&t=${Date.now()}`;

  // Если за 12 с не пришёл ни один кадр — показываем заглушку
  liveConnectTimeout = setTimeout(() => {
    if (activeCamera && activeCamera.id === cameraId && !streamReady && !deviceOfflineShown) {
      setLiveViewportState('error', 'Таймаут подключения к камере');
    }
  }, 12000);
}

// Периодическое обновление FPS, задержки, разрешения, паузы и статуса подключения камеры
async function updateLiveMetrics() {
  if (!activeCamera || !activeCamera.id) return;
  const targetCameraId = activeCamera.id;
  try {
    const status = await api.getLiveStatus(targetCameraId);
    if (!status || !activeCamera || activeCamera.id !== targetCameraId) return;

    hasFirstLiveStatus = true;

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

    colorMatrix.setColorMarkerDetected(Boolean(status.color_marker_detected));

    if (status.device_online === false) {
      if (!deviceOfflineShown) {
        deviceOfflineShown = true;
        streamAttached = false;
        if (liveConnectTimeout) {
          clearTimeout(liveConnectTimeout);
          liveConnectTimeout = null;
        }
        if (liveStreamRetryTimer) {
          clearTimeout(liveStreamRetryTimer);
          liveStreamRetryTimer = null;
        }
        const streamImg = document.getElementById('live-stream-img');
        if (streamImg) {
          streamImg.onload = null;
          streamImg.onerror = null;
          streamImg.removeAttribute('src');
          streamImg.classList.add('hidden');
        }
      }
      setLiveViewportState('error', status.stream_error || 'Камера отключена');
    } else if (deviceOfflineShown) {
      if (status.is_active) {
        deviceOfflineShown = false;
        liveStartedAt = Date.now();
        streamAttached = true;
        attachStreamHandlers(targetCameraId, currentLiveLayer);
      }
    } else if (!streamAttached) {
      streamAttached = true;
      attachStreamHandlers(targetCameraId, currentLiveLayer);
    }
  } catch (_) {
    // игнорируем сетевые сбои поллинга статуса
  }
}

function getLiveModalCtrl() {
  if (!liveModalCtrl) {
    liveModalCtrl = new Modal('modal-camera-live', {
      closeBtn: 'modal-camera-live-close',
      onRequestClose: () => requestCloseLiveModal(),
    });
  }
  return liveModalCtrl;
}

function getUnsavedModalCtrl() {
  if (!unsavedModalCtrl) {
    unsavedModalCtrl = new Modal('modal-live-unsaved', {
      closeBtn: 'modal-live-unsaved-close',
    });
  }
  return unsavedModalCtrl;
}

function hideUnsavedModal() {
  getUnsavedModalCtrl().close();
}

function showUnsavedModal() {
  getUnsavedModalCtrl().open();
}

// Запуск и открытие модального окна Live-режима
export async function openLiveModal(camera) {
  if (!camera || !camera.id) return;
  activeCamera = camera;
  v4l2.reset();
  initialCameraSnapshot = null;
  currentLiveLayer = 'raw';
  currentSegmentationMode = 'auto';
  liveStartedAt = Date.now();
  streamReady = false;
  deviceOfflineShown = false;
  hasFirstLiveStatus = false;
  streamAttached = false;
  colorMatrix.reset();
  isClosingFlow = false;
  updateLiveLayerButtonsUi();
  updateSegmentationModeButtonsUi();

  const modalCtrl = getLiveModalCtrl();
  const modal = modalCtrl.getElement();
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
  v4l2.renderLiveSettingsOverlay();
  modalCtrl.open();

  try {
    const startRes = await api.startLive(camera.id);
    if (!activeCamera || activeCamera.id !== camera.id) return;
    if (resEl && startRes && startRes.resolution) {
      resEl.textContent = startRes.resolution;
    }
    if (startRes && startRes.segmentation_mode) {
      currentSegmentationMode = normalizeSegmentationMode(startRes.segmentation_mode);
      updateSegmentationModeButtonsUi();
    }
  } catch (err) {
    setLiveViewportState('error', err.message || 'Ошибка запуска Live-стрима');
    return;
  }

  pollingCoordinator.register(LIVE_POLL_TASK_NAME, updateLiveMetrics, 1000, {
    pauseWhenModalOpen: false,
    pauseWhenHidden: true,
  });
  pollingCoordinator.start(LIVE_POLL_TASK_NAME, { runImmediately: true });

  v4l2.loadCameraControls(camera.id, true);
}

// Проверка наличия несохранённых изменений V4L2, формата, разрешения или R/G/B
export function hasUnsavedLiveChanges() {
  const currentCameraControls = v4l2.getControls();
  if (!initialCameraSnapshot || !currentCameraControls) return false;
  const draftRgb = colorMatrix.getDraftRgb();

  if (
    Math.abs(draftRgb.r - initialCameraSnapshot.rgb.r) >= 0.005 ||
    Math.abs(draftRgb.g - initialCameraSnapshot.rgb.g) >= 0.005 ||
    Math.abs(draftRgb.b - initialCameraSnapshot.rgb.b) >= 0.005
  ) {
    return true;
  }

  if (
    (currentCameraControls.current_format || null) !== initialCameraSnapshot.format ||
    (currentCameraControls.current_resolution || null) !== initialCameraSnapshot.resolution ||
    normalizeSegmentationMode(currentSegmentationMode) !==
      normalizeSegmentationMode(initialCameraSnapshot.segmentation_mode)
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

  const currentCameraControls = v4l2.getControls();
  const draftRgb = colorMatrix.getDraftRgb();

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
      const rgbChanged =
        Math.abs(draftRgb.r - initialCameraSnapshot.rgb.r) >= 0.005 ||
        Math.abs(draftRgb.g - initialCameraSnapshot.rgb.g) >= 0.005 ||
        Math.abs(draftRgb.b - initialCameraSnapshot.rgb.b) >= 0.005;
      const segModeChanged =
        normalizeSegmentationMode(currentSegmentationMode) !==
        normalizeSegmentationMode(initialCameraSnapshot.segmentation_mode);

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
      if (
        Object.keys(regularCtrls).length > 0 ||
        fmtChanged ||
        resChanged ||
        rgbChanged ||
        segModeChanged
      ) {
        const payload = {};
        if (Object.keys(regularCtrls).length > 0) payload.controls = regularCtrls;
        if (fmtChanged && initialCameraSnapshot.format) payload.format = initialCameraSnapshot.format;
        if (resChanged && initialCameraSnapshot.resolution) payload.resolution = initialCameraSnapshot.resolution;
        if (rgbChanged) payload.rgb = { ...initialCameraSnapshot.rgb };
        if (segModeChanged) {
          payload.segmentation_mode = normalizeSegmentationMode(
            initialCameraSnapshot.segmentation_mode
          );
        }
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

  const currentCameraControls = v4l2.getControls();
  const draftRgb = colorMatrix.getDraftRgb();

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
        segmentation_mode: normalizeSegmentationMode(currentSegmentationMode),
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
  getLiveModalCtrl().close();
  pollingCoordinator.stop(LIVE_POLL_TASK_NAME);
  clearStreamTimers();

  streamReady = false;
  deviceOfflineShown = false;
  hasFirstLiveStatus = false;
  streamAttached = false;
  currentLiveLayer = 'raw';
  currentSegmentationMode = 'auto';
  updateSegmentationModeButtonsUi();
  initialCameraSnapshot = null;
  v4l2.reset();
  colorMatrix.reset();

  const streamImg = document.getElementById('live-stream-img');
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

// Скачивание векторного SVG-маркера для печати (aruco или color)
export async function downloadMarkerSvg(markerType, sizeMm = 50) {
  const safeType = markerType === 'color' ? 'color' : 'aruco';
  const url = `/api/cameras/markers/${safeType}.svg?size_mm=${encodeURIComponent(sizeMm)}`;
  const filename =
    safeType === 'aruco'
      ? `growvision_aruco_4x4_id0_${sizeMm}mm.svg`
      : `growvision_color_marker_${sizeMm}mm.svg`;
  try {
    await api.downloadFile(url, filename);
    showToast(safeType === 'aruco' ? 'ArUco-маркер скачан (SVG)' : 'Цветовой маркер скачан (SVG)');
  } catch (err) {
    showError(err.message || 'Не удалось скачать SVG-маркер');
  }
}

// Скачивание свежего JPEG с учётом текущих коэффициентов R/G/B
async function downloadLiveSnapshot() {
  if (!activeCamera || !activeCamera.id) return;
  const btn = document.getElementById('btn-live-download-snapshot');
  if (btn) btn.disabled = true;
  try {
    const draftRgb = colorMatrix.getDraftRgb();
    const r = clampRgbValue(draftRgb.r, 1.0).toFixed(2);
    const g = clampRgbValue(draftRgb.g, 1.0).toFixed(2);
    const b = clampRgbValue(draftRgb.b, 1.0).toFixed(2);
    const url = `/api/cameras/${activeCamera.id}/live/snapshot?r=${r}&g=${g}&b=${b}&t=${Date.now()}`;
    const filename = `snapshot_${activeCamera.hardware_id || activeCamera.id}_${Date.now()}.jpg`;
    await api.downloadFile(url, filename);
  } catch (err) {
    showError(err.message || 'Не удалось скачать тестовый снимок');
  } finally {
    if (btn) btn.disabled = false;
  }
}

/**
 * Общий механизм выполнения быстрого снимка установки (используется модулем быстрого снимка test_snap.js).
 */
export async function executeQuickSnap(unitId) {
  const res = await api.snap(Number(unitId));
  if (res && res.status === 'error') {
    throw new Error(res.result_message || 'Ошибка выполнения снимка Edge-агентом');
  }
  const cap = res && res.captures && res.captures[0] ? res.captures[0] : res || {};
  return {
    ...res,
    ...cap,
    raw_image_url: cap.raw_url || cap.raw_image_url || res?.raw_url || res?.raw_image_url,
    annotated_image_url:
      cap.annotated_url || cap.annotated_image_url || res?.annotated_url || res?.annotated_image_url,
    captured_at: cap.captured_at || res?.captured_at,
    area_display: cap.area_display || res?.area_display,
  };
}

/**
 * Общий механизм скачивания быстрого снимка установки (оригинал или разметка) через api.downloadFile.
 */
export async function downloadSnapImage(url, unitName = 'unit', layerTag = 'raw') {
  if (!url) return;
  const dlUrl = `${url}${url.includes('?') ? '&' : '?'}download=1`;
  const now = new Date();
  const datePart = now.toISOString().slice(0, 10);
  const timePart = `${String(now.getHours()).padStart(2, '0')}-${String(now.getMinutes()).padStart(2, '0')}`;
  const safeName = (unitName || 'unit').replace(/[^a-zA-Z0-9а-яА-Я_-]/g, '_');
  const filename = `growvision_${safeName}_snap_${layerTag}_${datePart}_${timePart}.jpg`;
  await api.downloadFile(dlUrl, filename);
}

async function switchSegmentationMode(nextMode) {
  const normalized = normalizeSegmentationMode(nextMode);
  if (normalized === currentSegmentationMode) return;
  const prevMode = currentSegmentationMode;
  currentSegmentationMode = normalized;
  updateSegmentationModeButtonsUi();
  const currentCameraControls = v4l2.getControls();
  if (currentCameraControls) {
    currentCameraControls.segmentation_mode = normalized;
  }
  if (!activeCamera || !activeCamera.id) return;
  try {
    await api.updateCameraControls(activeCamera.id, { segmentation_mode: normalized });
  } catch (err) {
    currentSegmentationMode = prevMode;
    if (currentCameraControls) {
      currentCameraControls.segmentation_mode = prevMode;
    }
    updateSegmentationModeButtonsUi();
    showError(err.message || 'Ошибка переключения режима сегментации');
  }
}

async function switchLiveLayer(nextMode) {
  if (!activeCamera || !activeCamera.id) return;
  const normalized = nextMode === 'annotated' ? 'annotated' : 'raw';
  if (normalized === currentLiveLayer) return;
  currentLiveLayer = normalized;
  updateLiveLayerButtonsUi();
  colorMatrix.applyFilter();
  try {
    await api.setLiveMode(activeCamera.id, normalized);
  } catch (_) {
    // поток сам передаст query ?mode=...
  }
  if (activeCamera && activeCamera.id && hasFirstLiveStatus && !deviceOfflineShown) {
    attachStreamHandlers(activeCamera.id, normalized);
  }
}

export function initLiveModalEvents() {
  getLiveModalCtrl().bind();
  getUnsavedModalCtrl().bind();
  colorMatrix.bindAutoColorEvents();

  const segModeControl = document.getElementById('live-seg-mode-control');
  if (segModeControl) {
    segModeControl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        switchSegmentationMode(btn.dataset.segMode);
      });
    });
  }

  const layerRawBtn = document.getElementById('live-layer-raw');
  const layerAnnBtn = document.getElementById('live-layer-ann');
  if (layerRawBtn) {
    layerRawBtn.addEventListener('click', () => switchLiveLayer('raw'));
  }
  if (layerAnnBtn) {
    layerAnnBtn.addEventListener('click', () => switchLiveLayer('annotated'));
  }

  const unsavedDiscardBtn = document.getElementById('btn-live-unsaved-discard');
  const unsavedNextBtn = document.getElementById('btn-live-unsaved-next');
  const unsavedGalleryBtn = document.getElementById('btn-live-unsaved-gallery');
  if (unsavedDiscardBtn) {
    unsavedDiscardBtn.addEventListener('click', discardLiveChangesAndClose);
  }
  if (unsavedNextBtn) {
    unsavedNextBtn.addEventListener('click', () => saveLiveChangesAndClose('next_frame'));
  }
  if (unsavedGalleryBtn) {
    unsavedGalleryBtn.addEventListener('click', () => saveLiveChangesAndClose('all_gallery'));
  }

  const btnDownloadSnap = document.getElementById('btn-live-download-snapshot');
  const btnLiveDownloadAruco = document.getElementById('btn-live-download-aruco');
  const btnLiveDownloadColor = document.getElementById('btn-live-download-color');
  if (btnDownloadSnap) {
    btnDownloadSnap.addEventListener('click', downloadLiveSnapshot);
  }
  if (btnLiveDownloadAruco) {
    btnLiveDownloadAruco.addEventListener('click', () => downloadMarkerSvg('aruco', 50));
  }
  if (btnLiveDownloadColor) {
    btnLiveDownloadColor.addEventListener('click', () => downloadMarkerSvg('color', 50));
  }
}
