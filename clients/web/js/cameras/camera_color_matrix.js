// clients/web/js/cameras/camera_color_matrix.js
// Управление программным SVG RGB-фильтром (feColorMatrix) и автоцветокоррекцией по маркеру

import { api } from '../api.js';
import { showToast } from '../dialogs.js';

export const LIVE_RGB_THROTTLE_MS = 180;

export function clampRgbValue(val, def = 1.0) {
  const num = Number(val);
  if (!Number.isFinite(num)) return def;
  return Math.max(0.0, Math.min(1.0, Math.round(num * 100) / 100));
}

export function applyLiveRgbFilter(rgb, liveLayer = 'raw') {
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
    if (liveLayer === 'annotated') {
      // В режиме «Разметка» кадр уже скорректирован по RGB на edge_agent, сама разметка остаётся в неизменных цветах
      streamImg.style.filter = '';
    } else {
      const isIdentity = Math.abs(r - 1.0) < 1e-3 && Math.abs(g - 1.0) < 1e-3 && Math.abs(b - 1.0) < 1e-3;
      streamImg.style.filter = isIdentity ? '' : 'url(#live-rgb-filter)';
    }
  }
}

export function createColorMatrixController({ getActiveCamera, getCurrentLiveLayer }) {
  let draftRgb = { r: 1.0, g: 1.0, b: 1.0 };
  let colorMarkerDetected = false;
  let isAutoColorCalibrating = false;
  let pendingAutoColorPreview = null;
  let liveRgbSyncTimer = null;
  let lastRgbSyncAt = 0;

  function getDraftRgb() {
    return { ...draftRgb };
  }

  function setDraftRgb(rgb) {
    draftRgb = {
      r: clampRgbValue(rgb?.r, 1.0),
      g: clampRgbValue(rgb?.g, 1.0),
      b: clampRgbValue(rgb?.b, 1.0),
    };
    applyLiveRgbFilter(draftRgb, getCurrentLiveLayer());
  }

  function setDraftChannel(channel, val) {
    draftRgb[channel] = clampRgbValue(val, 1.0);
    applyLiveRgbFilter(draftRgb, getCurrentLiveLayer());
  }

  function clearRgbSyncTimer() {
    if (liveRgbSyncTimer) {
      clearTimeout(liveRgbSyncTimer);
      liveRgbSyncTimer = null;
    }
  }

  async function sendLiveRgbUpdate(cameraId, rgbPayload) {
    const activeCamera = getActiveCamera();
    if (!activeCamera || activeCamera.id !== cameraId) return;
    lastRgbSyncAt = Date.now();
    try {
      await api.updateCameraControls(cameraId, { rgb: rgbPayload });
    } catch (_) {
      // Промежуточные ошибки при перетаскивании ползунка не прерывают настройку
    }
  }

  function scheduleLiveRgbSync(immediate = false) {
    const activeCamera = getActiveCamera();
    if (!activeCamera || !activeCamera.id) return;
    const cameraId = activeCamera.id;
    const payload = {
      r: clampRgbValue(draftRgb.r, 1.0),
      g: clampRgbValue(draftRgb.g, 1.0),
      b: clampRgbValue(draftRgb.b, 1.0),
    };
    clearRgbSyncTimer();
    const elapsed = Date.now() - lastRgbSyncAt;
    if (immediate || elapsed >= LIVE_RGB_THROTTLE_MS) {
      sendLiveRgbUpdate(cameraId, payload);
    } else {
      const delay = Math.max(20, LIVE_RGB_THROTTLE_MS - elapsed);
      liveRgbSyncTimer = setTimeout(() => {
        liveRgbSyncTimer = null;
        sendLiveRgbUpdate(cameraId, payload);
      }, delay);
    }
  }

  function syncAutoColorButtonUi() {
    const autoColorBtn = document.getElementById('btn-live-auto-color');
    if (!autoColorBtn) return;
    autoColorBtn.disabled = !colorMarkerDetected || isAutoColorCalibrating;
    autoColorBtn.textContent = isAutoColorCalibrating ? 'Калибровка...' : 'Автоцветокоррекция';
  }

  function hideAutoColorPreview() {
    pendingAutoColorPreview = null;
    const panel = document.getElementById('live-auto-color-preview');
    const roiImg = document.getElementById('live-auto-color-roi-img');
    if (panel) panel.classList.add('hidden');
    if (roiImg) {
      roiImg.removeAttribute('src');
      roiImg.classList.add('hidden');
    }
  }

  function showAutoColorPreview(rgb, roiBase64) {
    pendingAutoColorPreview = {
      rgb: {
        r: clampRgbValue(rgb?.r, 1.0),
        g: clampRgbValue(rgb?.g, 1.0),
        b: clampRgbValue(rgb?.b, 1.0),
      },
      roiBase64: typeof roiBase64 === 'string' ? roiBase64 : '',
    };

    const panel = document.getElementById('live-auto-color-preview');
    const roiImg = document.getElementById('live-auto-color-roi-img');
    const rgbEl = document.getElementById('live-auto-color-rgb-values');
    const { r, g, b } = pendingAutoColorPreview.rgb;

    if (rgbEl) {
      rgbEl.textContent = `R: ${r.toFixed(2)} · G: ${g.toFixed(2)} · B: ${b.toFixed(2)}`;
    }
    if (roiImg) {
      if (pendingAutoColorPreview.roiBase64) {
        const src = pendingAutoColorPreview.roiBase64.startsWith('data:')
          ? pendingAutoColorPreview.roiBase64
          : `data:image/jpeg;base64,${pendingAutoColorPreview.roiBase64}`;
        roiImg.src = src;
        roiImg.classList.remove('hidden');
      } else {
        roiImg.removeAttribute('src');
        roiImg.classList.add('hidden');
      }
    }
    if (panel) {
      panel.classList.remove('hidden');
    }
  }

  // Запрос предпросмотра автоцветокоррекции (без изменения текущих настроек камеры до подтверждения)
  async function handleAutoColorCalibrate() {
    const activeCamera = getActiveCamera();
    if (!activeCamera || !activeCamera.id || isAutoColorCalibrating || !colorMarkerDetected) return;
    const targetCameraId = activeCamera.id;
    isAutoColorCalibrating = true;
    syncAutoColorButtonUi();
    try {
      const res = await api.autoColorCalibrate(targetCameraId, { preview: true });
      const curCam = getActiveCamera();
      if (!curCam || curCam.id !== targetCameraId) return;
      const recRgb = res && (res.recommended_rgb || res.rgb);
      if (recRgb) {
        showAutoColorPreview(recRgb, res.roi_image_base64 || '');
      }
    } catch (err) {
      showToast(err.message || 'Не удалось выполнить автоцветокоррекцию', 'error');
    } finally {
      isAutoColorCalibrating = false;
      syncAutoColorButtonUi();
    }
  }

  // Подтверждение и применение рекомендованных коэффициентов RGB из плашки предпросмотра
  async function applyAutoColorPreview() {
    const activeCamera = getActiveCamera();
    if (!activeCamera || !activeCamera.id || !pendingAutoColorPreview) return;
    const targetCameraId = activeCamera.id;
    const nextRgb = { ...pendingAutoColorPreview.rgb };
    const applyBtn = document.getElementById('btn-live-auto-color-apply');
    if (applyBtn) applyBtn.disabled = true;

    try {
      await api.updateCameraControls(targetCameraId, { rgb: nextRgb });
      const curCam = getActiveCamera();
      if (!curCam || curCam.id !== targetCameraId) return;
      draftRgb = { ...nextRgb };
      lastRgbSyncAt = Date.now();
      applyLiveRgbFilter(draftRgb, getCurrentLiveLayer());
      ['r', 'g', 'b'].forEach((ch) => {
        const slider = document.getElementById(`slider-rgb-${ch}`);
        const valSpan = document.getElementById(`val-rgb-${ch}`);
        if (slider) slider.value = String(draftRgb[ch]);
        if (valSpan) valSpan.textContent = draftRgb[ch].toFixed(2);
      });
      hideAutoColorPreview();
      showToast(
        `Автоцветокоррекция: R=${draftRgb.r.toFixed(2)}, G=${draftRgb.g.toFixed(2)}, B=${draftRgb.b.toFixed(2)}`
      );
    } catch (err) {
      showToast(err.message || 'Не удалось применить цветокоррекцию', 'error');
    } finally {
      if (applyBtn) applyBtn.disabled = false;
    }
  }

  function reset() {
    clearRgbSyncTimer();
    colorMarkerDetected = false;
    isAutoColorCalibrating = false;
    hideAutoColorPreview();
    draftRgb = { r: 1.0, g: 1.0, b: 1.0 };
    applyLiveRgbFilter(draftRgb, getCurrentLiveLayer());
  }

  function bindAutoColorEvents() {
    const autoColorApplyBtn = document.getElementById('btn-live-auto-color-apply');
    const autoColorCancelBtn = document.getElementById('btn-live-auto-color-cancel');
    if (autoColorApplyBtn) {
      autoColorApplyBtn.addEventListener('click', applyAutoColorPreview);
    }
    if (autoColorCancelBtn) {
      autoColorCancelBtn.addEventListener('click', hideAutoColorPreview);
    }
    document.addEventListener('click', (e) => {
      const previewPanel = document.getElementById('live-auto-color-preview');
      if (!previewPanel || previewPanel.classList.contains('hidden')) return;
      const target = e.target;
      if (target instanceof Node && previewPanel.contains(target)) return;
      if (target instanceof Element && target.closest('#btn-live-auto-color')) return;
      hideAutoColorPreview();
    });
  }

  return {
    getDraftRgb,
    setDraftRgb,
    setDraftChannel,
    applyFilter: () => applyLiveRgbFilter(draftRgb, getCurrentLiveLayer()),
    scheduleLiveRgbSync,
    clearRgbSyncTimer,
    setColorMarkerDetected: (val) => {
      colorMarkerDetected = Boolean(val);
      syncAutoColorButtonUi();
    },
    isMarkerDetected: () => colorMarkerDetected,
    isCalibrating: () => isAutoColorCalibrating,
    syncAutoColorButtonUi,
    hideAutoColorPreview,
    handleAutoColorCalibrate,
    bindAutoColorEvents,
    reset,
  };
}
