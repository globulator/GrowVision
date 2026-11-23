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
      <div class="snap-loading-placeholder plant-lifecycle-spinner">
        <svg viewBox="0 0 64 64" width="56" height="56" class="plant-cycle-svg" aria-hidden="true">
          <path class="plant-ground" d="M12 52 H52" stroke="#334155" stroke-width="2" stroke-linecap="round"/>
          <ellipse class="plant-seed" cx="32" cy="50" rx="4" ry="3" fill="#10b981"/>
          <path class="plant-stem" d="M32 50 Q32 36 32 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round"/>
          <path class="plant-sprout-left" d="M32 38 Q22 36 20 28 Q28 28 32 36" fill="#10b981"/>
          <path class="plant-leaf-right" d="M32 30 Q44 26 44 16 Q34 18 32 26" fill="#059669"/>
          <path class="plant-top-leaf" d="M32 24 Q32 10 32 8 Q36 14 32 24" fill="#34d399"/>
        </svg>
        <span class="placeholder-text">Выполнение снимка Edge-агентом...</span>
      </div>
    `;
  }
  if (metaBox) metaBox.textContent = 'Ожидание ответа...';

  try {
    // Внимание: target передается строго как целое число Number(unit.id)
    const res = await api.snap(Number(currentUnit.id));
    if (res.status === 'error') {
      throw new Error(res.result_message || 'Ошибка выполнения снимка Edge-агентом');
    }

    const cap = (res.captures && res.captures[0]) ? res.captures[0] : res;
    currentSnapData = {
      ...res,
      ...cap,
      raw_image_url: cap.raw_url || cap.raw_image_url || res.raw_url || res.raw_image_url,
      annotated_image_url:
        cap.annotated_url || cap.annotated_image_url || res.annotated_url || res.annotated_image_url,
      captured_at: cap.captured_at || res.captured_at,
      area_display: cap.area_display || res.area_display,
    };

    renderSnapImage();
    if (resnapBtn) resnapBtn.disabled = false;
    if (downloadBtn) downloadBtn.disabled = false;
  } catch (err) {
    showError(err.message, 'Ошибка съёмки');
    if (previewBox) {
      previewBox.innerHTML = `
        <div class="snap-loading-placeholder snap-error-placeholder">
          <svg class="placeholder-icon" viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
            <line x1="1" y1="1" x2="23" y2="23"/>
          </svg>
          <span class="placeholder-text error-text">Камера стесняется или недоступна. Проверьте подключение оборудования.</span>
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
