// clients/web/js/test_snap.js
// Модальное окно тестового снимка с фиксом передачи target и слоями

import { api } from './api.js';
import { showError, showToast } from './dialogs.js';

let currentUnit = null;
let currentRole = 'guest';
let currentSnapData = null;
let currentLayer = 'raw'; // 'raw' или 'ann'

export function setTestSnapRole(role) {
  currentRole = role;
}

export function openTestSnapModal(unit) {
  currentUnit = unit;
  currentSnapData = null;
  currentLayer = 'raw';

  const modal = document.getElementById('modal-test-snap');
  if (!modal) return;

  const titleEl = document.getElementById('test-snap-title');
  if (titleEl) {
    titleEl.textContent = `Тестовый снимок: ${unit.name}`;
  }

  // Слой: показываем тумблер только для разработчика
  const layerToggle = document.getElementById('test-snap-layer-container');
  if (layerToggle) {
    layerToggle.classList.toggle('hidden', currentRole !== 'developer');
  }

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  // Сразу запускаем съемку
  triggerSnap();
}

export function closeTestSnapModal() {
  const modal = document.getElementById('modal-test-snap');
  if (modal) {
    modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }
  currentUnit = null;
  currentSnapData = null;
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
    previewBox.innerHTML = `
      <div class="snap-loading-placeholder">
        <svg class="placeholder-icon rotating" viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="12" y1="2" x2="12" y2="6"/>
          <line x1="12" y1="18" x2="12" y2="22"/>
          <line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/>
          <line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/>
          <line x1="2" y1="12" x2="6" y2="12"/>
          <line x1="18" y1="12" x2="22" y2="12"/>
          <line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/>
          <line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/>
        </svg>
        <span class="placeholder-text">Выполнение снимка Edge-агентом...</span>
      </div>
    `;
  }
  if (metaBox) metaBox.textContent = 'Ожидание ответа...';

  try {
    // Внимание: target передается строго как целое число Number(unit.id)
    const res = await api.snap(Number(currentUnit.id));
    currentSnapData = res;
    renderSnapImage();
    if (resnapBtn) resnapBtn.disabled = false;
    if (downloadBtn) downloadBtn.disabled = false;
  } catch (err) {
    showError(err.message, 'Ошибка съёмки');
    if (previewBox) {
      previewBox.innerHTML = `
        <div class="snap-loading-placeholder">
          <span class="placeholder-text error-text">Не удалось получить снимок</span>
        </div>
      `;
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
  img.src = `${url}?t=${Date.now()}`;
  img.alt = 'Тестовый снимок';
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

// Скачивание снимка как PNG
async function downloadSnapPng() {
  if (!currentSnapData || !currentUnit) return;

  const url =
    currentLayer === 'ann' && currentSnapData.annotated_image_url
      ? currentSnapData.annotated_image_url
      : currentSnapData.raw_image_url;

  try {
    const res = await fetch(url);
    const blob = await res.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);

    const now = new Date();
    const datePart = now.toISOString().slice(0, 10);
    const timePart = `${String(now.getHours()).padStart(2, '0')}-${String(now.getMinutes()).padStart(2, '0')}`;
    const safeName = (currentUnit.name || 'unit').replace(/[^a-zA-Z0-9а-яА-Я_-]/g, '_');
    link.download = `growvision_${safeName}_snap_${datePart}_${timePart}.png`;

    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    showToast('Снимок успешно скачан');
  } catch (err) {
    showError('Не удалось скачать снимок: ' + err.message, 'Ошибка скачивания');
  }
}

export function initTestSnap() {
  const modal = document.getElementById('modal-test-snap');
  if (!modal) return;

  const closeBtn = document.getElementById('modal-test-snap-close');
  if (closeBtn) closeBtn.addEventListener('click', closeTestSnapModal);

  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeTestSnapModal();
  });

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

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeTestSnapModal();
    }
  });
}
