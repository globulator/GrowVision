// clients/web/js/test_snap.js
// Модальное окно тестового снимка с фиксом передачи target, слоями и единым механизмом съёмки/скачивания

import { Modal, showError, showToast } from './dialogs.js';
import { getLoadingSpinnerHTML, getPlaceholderHTML } from './placeholders.js';
import { downloadSnapImage, executeQuickSnap } from './cameras.js';

let currentUnit = null;
let currentRole = 'guest';
let currentSnapData = null;
let currentLayer = 'raw'; // 'raw' или 'ann'
let testSnapModalCtrl = null;

function getTestSnapModalCtrl() {
  if (!testSnapModalCtrl) {
    testSnapModalCtrl = new Modal('modal-test-snap', {
      closeBtn: 'modal-test-snap-close',
      onClose: () => {
        currentUnit = null;
        currentSnapData = null;
      },
    });
  }
  return testSnapModalCtrl;
}

export function setTestSnapRole(role) {
  currentRole = role;
}

export function openTestSnapModal(unit) {
  currentUnit = unit;
  currentSnapData = null;
  currentLayer = 'raw';

  const modalCtrl = getTestSnapModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;

  const titleEl = document.getElementById('test-snap-title');
  if (titleEl) {
    titleEl.textContent = `Тестовый снимок: ${unit.name}`;
  }

  // Слой: показываем тумблер только для разработчика и сбрасываем на оригинал
  const layerToggle = document.getElementById('test-snap-layer-container');
  if (layerToggle) {
    layerToggle.classList.toggle('hidden', currentRole !== 'developer');
  }
  const layerRawBtn = document.getElementById('test-snap-layer-raw');
  const layerAnnBtn = document.getElementById('test-snap-layer-ann');
  if (layerRawBtn && layerAnnBtn) {
    layerRawBtn.classList.add('active');
    layerAnnBtn.classList.remove('active');
  }

  modalCtrl.open();

  // Сразу запускаем съемку
  triggerSnap();
}

export function closeTestSnapModal() {
  getTestSnapModalCtrl().close();
}

async function triggerSnap() {
  if (!currentUnit) return;

  const previewBox = document.getElementById('test-snap-preview-box');
  const metaBox = document.getElementById('test-snap-meta');
  const resnapBtn = document.getElementById('test-snap-resnap-btn');
  const downloadBtn = document.getElementById('test-snap-download-btn');

  if (resnapBtn) resnapBtn.disabled = true;
  if (downloadBtn) downloadBtn.disabled = true;

  if (previewBox) {
    previewBox.innerHTML = getLoadingSpinnerHTML();
  }
  if (metaBox) metaBox.textContent = 'Ожидание ответа от камеры...';

  try {
    // Внимание: target передается строго как целое число Number(unit.id) через общий механизм в cameras.js
    const snapData = await executeQuickSnap(currentUnit.id);
    if (!currentUnit) return;
    currentSnapData = snapData;

    renderSnapImage();
    if (resnapBtn) resnapBtn.disabled = false;
    if (downloadBtn) downloadBtn.disabled = false;
  } catch (err) {
    // Мягкая обработка: не закрывать модалку, не показывать showError попап
    const isNetworkErr =
      err.status === 0 ||
      (typeof err.message === 'string' &&
        (err.message.includes('недоступен') || err.message.includes('fetch') || err.message.includes('NetworkError')));

    if (previewBox) {
      previewBox.innerHTML = getPlaceholderHTML(isNetworkErr ? 'C' : 'B');
    }
    if (metaBox) {
      metaBox.textContent = isNetworkErr ? 'Связь с сервером оборвалась' : 'Камера или плата недоступна';
    }
    if (resnapBtn) resnapBtn.disabled = false;
  }
}

function renderSnapImage() {
  if (!currentSnapData) return;

  const previewBox = document.getElementById('test-snap-preview-box');
  const metaBox = document.getElementById('test-snap-meta');
  if (!previewBox) return;

  const url =
    currentLayer === 'ann' && currentSnapData.annotated_image_url
      ? currentSnapData.annotated_image_url
      : currentSnapData.raw_image_url;

  previewBox.innerHTML = '';
  const img = document.createElement('img');
  img.className = 'snap-result-img';
  const sep = url.includes('?') ? '&' : '?';
  img.src = `${url}${sep}t=${Date.now()}`;
  img.alt = 'Тестовый снимок';
  img.onerror = () => {
    previewBox.innerHTML = getPlaceholderHTML('D');
  };
  previewBox.appendChild(img);

  // Метаданные: локальное время браузера и площадь
  if (metaBox) {
    let dateStr = '—';
    if (currentSnapData.captured_at) {
      const dt = new Date(currentSnapData.captured_at);
      dateStr = `${dt.toLocaleString('ru-RU')} (местное время)`;
    }
    const areaStr = currentSnapData.area_display || '—';
    metaBox.textContent = `Время: ${dateStr} | Площадь: ${areaStr}`;
  }
}

// Скачивание тестового снимка (оригинал или разметка) из эфемерного кэша через api.downloadFile
async function downloadSnapPng() {
  if (!currentSnapData || !currentUnit) return;

  const url =
    currentLayer === 'ann' && currentSnapData.annotated_image_url
      ? currentSnapData.annotated_image_url
      : currentSnapData.raw_image_url;
  if (!url) return;

  try {
    const layerTag = currentLayer === 'ann' ? 'ann' : 'raw';
    await downloadSnapImage(url, currentUnit.name, layerTag);
    showToast('Снимок успешно скачан');
  } catch (err) {
    showError('Не удалось скачать снимок: ' + err.message, 'Ошибка скачивания');
  }
}

export function initTestSnap() {
  const modalCtrl = getTestSnapModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;
  modalCtrl.bind();

  const resnapBtn = document.getElementById('test-snap-resnap-btn');
  if (resnapBtn) resnapBtn.addEventListener('click', triggerSnap);

  const downloadBtn = document.getElementById('test-snap-download-btn');
  if (downloadBtn) downloadBtn.addEventListener('click', downloadSnapPng);

  // Переключатель слоев
  const layerRawBtn = document.getElementById('test-snap-layer-raw');
  const layerAnnBtn = document.getElementById('test-snap-layer-ann');

  if (layerRawBtn && layerAnnBtn) {
    layerRawBtn.addEventListener('click', () => {
      currentLayer = 'raw';
      layerRawBtn.classList.add('active');
      layerAnnBtn.classList.remove('active');
      renderSnapImage();
    });

    layerAnnBtn.addEventListener('click', () => {
      currentLayer = 'ann';
      layerAnnBtn.classList.add('active');
      layerRawBtn.classList.remove('active');
      renderSnapImage();
    });
  }
}
