// clients/web/js/units.js
// Отображение активных установок, карточек, превью и кнопок действий

import { api } from './api.js';
import { showError } from './dialogs.js';

let currentRole = 'guest';
let onOpenGalleryCallback = null;
let onOpenTestSnapCallback = null;
let cacheBuster = Date.now();

export function setUnitActionHandlers({ onOpenGallery, onOpenTestSnap }) {
  onOpenGalleryCallback = onOpenGallery;
  onOpenTestSnapCallback = onOpenTestSnap;
}

export function updateUnitsRole(role) {
  currentRole = role || 'guest';
}

// Генерация SVG-заглушки ожидания кадров (0 кадров)
function createNoFramesSvg() {
  return `
    <div class="card-empty-placeholder">
      <svg class="placeholder-icon pulse-anim" viewBox="0 0 48 48" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 36c4-12 16-16 24-20-4 12-16 16-24 20z" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M24 24v16" stroke-linecap="round"/>
        <circle cx="36" cy="14" r="3" fill="currentColor"/>
      </svg>
      <span class="placeholder-text">Ожидание первого кадра</span>
    </div>
  `;
}

// Генерация SVG-заглушки ошибки загрузки кадра
function createErrorFramesSvg() {
  return `
    <div class="card-empty-placeholder">
      <svg class="placeholder-icon" viewBox="0 0 48 48" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="6" y="8" width="36" height="28" rx="4"/>
        <circle cx="16" cy="18" r="4"/>
        <path d="M42 30l-10-8-12 10-6-4-8 8" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="12" y1="36" x2="36" y2="12" stroke="#ef4444" stroke-width="2"/>
      </svg>
      <span class="placeholder-text">Кадр недоступен</span>
    </div>
  `;
}

// Отрисовка пустого списка (0 установок)
function renderEmptyList(container) {
  container.innerHTML = `
    <div class="empty-state-container">
      <svg class="empty-state-svg" viewBox="0 0 64 64" width="64" height="64" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M32 8c-12 0-22 10-22 22 0 16 22 26 22 26s22-10 22-26c0-12-10-22-22-22z" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M32 20c-6 0-10 4-10 10s10 14 10 14 10-8 10-14-4-10-10-10z" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <h3 class="empty-state-title">Нет активных установок</h3>
      <p class="empty-state-desc">Установки пока не созданы или находятся в архиве.</p>
    </div>
  `;
}

export async function loadAndRenderUnits() {
  const container = document.getElementById('units-grid');
  const refreshBtn = document.getElementById('units-refresh-btn');
  if (!container) return;

  if (refreshBtn) {
    refreshBtn.classList.add('rotating');
  }

  try {
    const installations = await api.getInstallations();
    // Фильтруем только активные (не архивные)
    const activeUnits = (installations || []).filter((u) => !u.is_archived);

    container.innerHTML = '';

    if (activeUnits.length === 0) {
      renderEmptyList(container);
      return;
    }

    activeUnits.forEach((unit) => {
      const card = document.createElement('div');
      card.className = 'unit-card glass-panel';
      card.dataset.id = unit.id;

      // Превью
      const previewWrapper = document.createElement('div');
      previewWrapper.className = 'unit-preview-wrapper';

      const framesCount = Number(unit.frames_count || 0);
      const frameUrl = unit.last_frame_url || unit.last_frame_raw_url;

      if (framesCount > 0 && frameUrl) {
        const img = document.createElement('img');
        img.className = 'unit-preview-img';
        img.alt = unit.name;
        img.loading = 'lazy';
        img.src = `${frameUrl}?t=${cacheBuster}`;
        img.onerror = () => {
          previewWrapper.innerHTML = createErrorFramesSvg();
        };
        previewWrapper.appendChild(img);
      } else {
        previewWrapper.innerHTML = createNoFramesSvg();
      }

      card.appendChild(previewWrapper);

      // Информация об установке
      const infoDiv = document.createElement('div');
      infoDiv.className = 'unit-info';

      const titleEl = document.createElement('h3');
      titleEl.className = 'unit-name';
      titleEl.textContent = unit.name;
      infoDiv.appendChild(titleEl);

      const cultureEl = document.createElement('div');
      cultureEl.className = 'unit-culture';
      cultureEl.textContent = unit.culture_name;
      infoDiv.appendChild(cultureEl);

      card.appendChild(infoDiv);

      // Кнопки действий (только для авторизованных)
      if (currentRole === 'biologist' || currentRole === 'developer') {
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'unit-actions';

        // Кнопка «Галерея» (disabled если 0 кадров)
        const galleryBtn = document.createElement('button');
        galleryBtn.type = 'button';
        galleryBtn.className = 'btn btn-secondary unit-gallery-btn';
        galleryBtn.innerHTML = `
          <svg class="icon-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <circle cx="8.5" cy="8.5" r="1.5"/>
            <polyline points="21 15 16 10 5 21"/>
          </svg>
          <span>Галерея</span>
        `;
        if (framesCount === 0) {
          galleryBtn.disabled = true;
          galleryBtn.title = 'Кадров пока нет';
        } else {
          galleryBtn.addEventListener('click', () => {
            if (typeof onOpenGalleryCallback === 'function') {
              onOpenGalleryCallback(unit);
            }
          });
        }
        actionsDiv.appendChild(galleryBtn);

        // Кнопка «Тестовый снимок»
        const snapBtn = document.createElement('button');
        snapBtn.type = 'button';
        snapBtn.className = 'btn btn-primary unit-snap-btn';
        snapBtn.innerHTML = `
          <svg class="icon-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
            <circle cx="12" cy="13" r="4"/>
          </svg>
          <span>Тестовый снимок</span>
        `;
        snapBtn.addEventListener('click', () => {
          if (typeof onOpenTestSnapCallback === 'function') {
            onOpenTestSnapCallback(unit);
          }
        });
        actionsDiv.appendChild(snapBtn);

        card.appendChild(actionsDiv);
      }

      container.appendChild(card);
    });
  } catch (err) {
    showError(err.message, 'Ошибка загрузки установок');
  } finally {
    if (refreshBtn) {
      setTimeout(() => refreshBtn.classList.remove('rotating'), 400);
    }
  }
}

export function initUnits() {
  const refreshBtn = document.getElementById('units-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      cacheBuster = Date.now();
      loadAndRenderUnits();
    });
  }
}
