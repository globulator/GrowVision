// clients/web/js/cameras.js
// Меню камер и единая точка входа модулей Live-режима, V4L2-настроек, RGB-матрицы и быстрого снимка

import { api } from './api.js';
import { Modal, showError, showToast } from './dialogs.js';
import {
  closeLiveModal,
  downloadMarkerSvg,
  downloadSnapImage,
  executeQuickSnap,
  formatCameraDisplayName,
  hasUnsavedLiveChanges,
  initLiveModalEvents,
  openLiveModal,
  requestCloseLiveModal,
  setLiveProfileSavedHandler,
} from './cameras/camera_live.js';

let currentRole = 'guest';
let onCamerasChangedCallback = null;
let camerasModalRequestSeq = 0;
let camerasModalCtrl = null;

export function setCamerasChangedHandler(fn) {
  onCamerasChangedCallback = fn;
}

export function updateCamerasRole(role) {
  currentRole = role || 'guest';
  const btn = document.getElementById('btn-cameras-menu');
  if (btn) {
    btn.classList.toggle('hidden', currentRole === 'guest');
  }
}

function formatCalibrationSummary(summary) {
  if (!summary) return '';
  return String(summary)
    .replace(/^Калибровка\s+[^:]+:\s*/i, '')
    .replace(/(\d+)x(\d+)/g, '$1\u00d7$2')
    .trim();
}

function setCamerasScanStatus(mode, text = '') {
  const statusEl = document.getElementById('cameras-scan-status');
  if (!statusEl) return;
  statusEl.classList.remove('is-loading', 'is-warning');
  if (mode === 'hidden' || !text) {
    statusEl.innerHTML = '';
    statusEl.classList.add('hidden');
    return;
  }
  if (mode === 'loading') {
    statusEl.classList.add('is-loading');
    statusEl.innerHTML = `<span class="cameras-scan-spinner" aria-hidden="true"></span><span>${text}</span>`;
    statusEl.classList.remove('hidden');
    return;
  }
  statusEl.classList.add('is-warning');
  statusEl.textContent = text;
  statusEl.classList.remove('hidden');
}

function renderCamerasList(container, cameras, activeInstallations) {
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
        <select class="form-input camera-bind-select" aria-label="Привязка камеры ${displayName} к установке"></select>
        <button type="button" class="btn btn-primary btn-cam-live" ${!isOnline ? 'disabled' : ''}>
          <svg class="icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="23 7 16 12 23 17 23 7"/>
            <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
          </svg>
          <span>Live</span>
        </button>
      </div>
    `;

    const bindSelect = card.querySelector('.camera-bind-select');
    if (bindSelect) {
      const unbindOpt = document.createElement('option');
      unbindOpt.value = '';
      unbindOpt.textContent = 'Отвязать';
      bindSelect.appendChild(unbindOpt);

      activeInstallations.forEach((inst) => {
        const opt = document.createElement('option');
        opt.value = String(inst.id);
        opt.textContent = inst.name;
        bindSelect.appendChild(opt);
      });

      const currentInstVal =
        cam.installation_id !== null && cam.installation_id !== undefined
          ? String(cam.installation_id)
          : '';
      if (
        currentInstVal &&
        !activeInstallations.some((inst) => String(inst.id) === currentInstVal)
      ) {
        const extraOpt = document.createElement('option');
        extraOpt.value = currentInstVal;
        extraOpt.textContent = cam.installation_name || `Установка #${currentInstVal}`;
        bindSelect.appendChild(extraOpt);
      }
      bindSelect.value = currentInstVal;

      if (currentRole !== 'developer') {
        bindSelect.disabled = true;
        bindSelect.title = 'Привязка камер доступна только разработчику';
      } else {
        bindSelect.addEventListener('change', async () => {
          const nextVal = bindSelect.value;
          const prevVal = currentInstVal;
          const targetInstId = nextVal === '' ? null : Number(nextVal);
          bindSelect.disabled = true;
          try {
            await api.bindCamera(cam.id, targetInstId);
            if (targetInstId === null) {
              showToast(`Камера «${displayName}» отвязана`);
            } else {
              const chosen = activeInstallations.find((i) => Number(i.id) === targetInstId);
              showToast(
                chosen
                  ? `Камера «${displayName}» привязана к «${chosen.name}»`
                  : `Камера «${displayName}» привязана`
              );
            }
            if (typeof onCamerasChangedCallback === 'function') {
              await onCamerasChangedCallback();
            }
            await openCamerasModal({ scan: false });
          } catch (err) {
            bindSelect.value = prevVal;
            bindSelect.disabled = false;
            showError(err.message || 'Не удалось изменить привязку камеры');
          }
        });
      }
    }

    const liveBtn = card.querySelector('.btn-cam-live');
    if (liveBtn) {
      liveBtn.addEventListener('click', () => {
        closeCamerasModal();
        openLiveModal(cam);
      });
    }

    container.appendChild(card);
  });
}

function getCamerasModalCtrl() {
  if (!camerasModalCtrl) {
    camerasModalCtrl = new Modal('modal-cameras-menu', {
      closeBtn: 'modal-cameras-menu-close',
      onClose: () => {
        camerasModalRequestSeq += 1;
        setCamerasScanStatus('hidden');
      },
    });
  }
  return camerasModalCtrl;
}

// Открытие модального окна со списком камер
export async function openCamerasModal(options = {}) {
  const modalCtrl = getCamerasModalCtrl();
  const modal = modalCtrl.getElement();
  const container = document.getElementById('cameras-list-container');
  if (!modal || !container) return;

  const reqSeq = ++camerasModalRequestSeq;
  const shouldScan =
    (options && options.scan !== undefined ? Boolean(options.scan) : true) &&
    currentRole === 'developer';

  modalCtrl.open();
  container.innerHTML = '<div class="loading-spinner">Загрузка списка камер...</div>';
  setCamerasScanStatus(shouldScan ? 'loading' : 'hidden', shouldScan ? 'Сканирование камер...' : '');

  const scanPromise = shouldScan ? api.scanCameras().catch((err) => ({ __scanError: err })) : null;

  let cachedLoaded = false;
  let activeInstallations = [];

  try {
    const [cameras, installations] = await Promise.all([
      api.getCameras(),
      api.getInstallations(false).catch(() => []),
    ]);
    if (reqSeq !== camerasModalRequestSeq || modal.classList.contains('hidden')) return;

    activeInstallations = Array.isArray(installations)
      ? installations.filter((inst) => !inst.is_archived)
      : [];
    cachedLoaded = true;
    renderCamerasList(container, cameras, activeInstallations);
  } catch (err) {
    if (reqSeq !== camerasModalRequestSeq || modal.classList.contains('hidden')) return;
    if (!shouldScan) {
      container.innerHTML = `<div class="form-error-inline">${err.message || 'Ошибка загрузки камер'}</div>`;
      return;
    }
  }

  if (!scanPromise) {
    setCamerasScanStatus('hidden');
    return;
  }

  const scanRes = await scanPromise;
  if (reqSeq !== camerasModalRequestSeq || modal.classList.contains('hidden')) return;

  if (scanRes && !scanRes.__scanError && scanRes.status !== 'error') {
    let freshCameras = Array.isArray(scanRes.cameras) ? scanRes.cameras : null;
    if (!freshCameras) {
      try {
        freshCameras = await api.getCameras();
      } catch (_) {
        freshCameras = null;
      }
    }
    if (reqSeq !== camerasModalRequestSeq || modal.classList.contains('hidden')) return;
    if (freshCameras) {
      if (!activeInstallations.length) {
        const instRes = await api.getInstallations(false).catch(() => []);
        if (reqSeq !== camerasModalRequestSeq || modal.classList.contains('hidden')) return;
        activeInstallations = Array.isArray(instRes)
          ? instRes.filter((inst) => !inst.is_archived)
          : [];
      }
      renderCamerasList(container, freshCameras, activeInstallations);
    }
    setCamerasScanStatus('hidden');
  } else {
    const errMsg =
      scanRes && scanRes.__scanError && scanRes.__scanError.message
        ? scanRes.__scanError.message
        : scanRes && scanRes.result_message
          ? scanRes.result_message
          : 'Edge-агент недоступен';
    if (cachedLoaded) {
      setCamerasScanStatus(
        'warning',
        `Edge-агент недоступен — показаны кэшированные данные (${errMsg})`
      );
    } else {
      setCamerasScanStatus('warning', `Edge-агент недоступен (${errMsg})`);
      container.innerHTML = `<div class="form-error-inline">${errMsg}</div>`;
    }
  }
}

export function closeCamerasModal() {
  getCamerasModalCtrl().close();
}

// Инициализация модуля камер
export function initCameras() {
  getCamerasModalCtrl().bind();
  initLiveModalEvents();

  const btnMenu = document.getElementById('btn-cameras-menu');
  const btnMenuDownloadAruco = document.getElementById('btn-menu-download-aruco');
  const btnMenuDownloadColor = document.getElementById('btn-menu-download-color');

  if (btnMenu) {
    btnMenu.addEventListener('click', () => openCamerasModal({ scan: true }));
  }
  if (btnMenuDownloadAruco) {
    btnMenuDownloadAruco.addEventListener('click', () => downloadMarkerSvg('aruco', 50));
  }
  if (btnMenuDownloadColor) {
    btnMenuDownloadColor.addEventListener('click', () => downloadMarkerSvg('color', 50));
  }
}

export {
  setLiveProfileSavedHandler,
  openLiveModal,
  hasUnsavedLiveChanges,
  requestCloseLiveModal,
  closeLiveModal,
  executeQuickSnap,
  downloadSnapImage,
};
