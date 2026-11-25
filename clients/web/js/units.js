// clients/web/js/units.js
// Отображение установок, фильтры/сортировка, карточка создания и кнопки действий

import { api } from './api.js';
import { getPlaceholderHTML } from './placeholders.js';

let currentRole = 'guest';
let onOpenGalleryCallback = null;
let onOpenTestSnapCallback = null;
let onOpenLiveCallback = null;
let onCreateUnitCallback = null;
let onEditUnitCallback = null;
let cacheBuster = Date.now();

const unitsFilterState = {
  status: 'active', // 'active' | 'archived'
  sort: 'newest', // 'newest' | 'oldest'
  camera: 'all', // 'all' | 'with_camera' | 'without_camera'
};

export function getUnitsFilterState() {
  return { ...unitsFilterState };
}

export function setUnitActionHandlers({
  onOpenGallery,
  onOpenTestSnap,
  onOpenLive,
  onCreateUnit,
  onEditUnit,
}) {
  onOpenGalleryCallback = onOpenGallery;
  onOpenTestSnapCallback = onOpenTestSnap;
  onOpenLiveCallback = onOpenLive;
  onCreateUnitCallback = onCreateUnit;
  onEditUnitCallback = onEditUnit;
}

function syncFilterButtonsUi() {
  const statusGroup = document.getElementById('units-filter-status');
  const sortGroup = document.getElementById('units-filter-sort');
  const cameraGroup = document.getElementById('units-filter-camera');

  if (statusGroup) {
    statusGroup.classList.toggle('hidden', currentRole === 'guest');
    statusGroup.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.filterStatus === unitsFilterState.status);
    });
  }
  if (sortGroup) {
    sortGroup.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.filterSort === unitsFilterState.sort);
    });
  }
  if (cameraGroup) {
    cameraGroup.classList.toggle('hidden', currentRole === 'guest');
    cameraGroup.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.filterCamera === unitsFilterState.camera);
    });
  }
}

export function updateUnitsRole(role) {
  const nextRole = role || 'guest';
  const changed = nextRole !== currentRole;
  currentRole = nextRole;
  if (currentRole === 'guest' && changed) {
    unitsFilterState.status = 'active';
    unitsFilterState.camera = 'all';
  }
  syncFilterButtonsUi();
}

function renderEmptyList(container) {
  const isArch = unitsFilterState.status === 'archived';
  const title = isArch ? 'Архив пуст' : 'Нет подходящих установок';
  const desc = isArch
    ? 'В архиве пока нет ни одной установки.'
    : unitsFilterState.camera !== 'all'
      ? 'Попробуйте изменить фильтр по камерам.'
      : 'Установки пока не созданы или находятся в архиве.';

  container.innerHTML = `
    <div class="empty-state-container">
      <svg class="empty-state-svg" viewBox="0 0 64 64" width="64" height="64" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M32 8c-12 0-22 10-22 22 0 16 22 26 22 26s22-10 22-26c0-12-10-22-22-22z" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M32 20c-6 0-10 4-10 10s10 14 10 14 10-8 10-14-4-10-10-10z" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <h3 class="empty-state-title">${title}</h3>
      <p class="empty-state-desc">${desc}</p>
    </div>
  `;
}

function createNewUnitCard() {
  const card = document.createElement('div');
  card.id = 'unit-create-card';
  card.className = 'unit-card unit-card-create glass-panel';
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  card.setAttribute('aria-label', 'Создать новую установку');
  card.innerHTML = `
    <div class="unit-create-icon-wrap">
      <svg class="unit-create-plus-svg" viewBox="0 0 48 48" width="56" height="56" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">
        <line x1="24" y1="10" x2="24" y2="38"/>
        <line x1="10" y1="24" x2="38" y2="24"/>
      </svg>
    </div>
    <div class="unit-create-label">Создать новую установку</div>
  `;

  const triggerCreate = () => {
    if (typeof onCreateUnitCallback === 'function') {
      onCreateUnitCallback();
    }
  };
  card.addEventListener('click', triggerCreate);
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      triggerCreate();
    }
  });
  return card;
}

export function filterAndSortUnits(installations, role = currentRole, state = unitsFilterState) {
  const showArchived = role !== 'guest' && state.status === 'archived';
  let list = (installations || []).filter((u) =>
    showArchived ? Boolean(u.is_archived) : !u.is_archived
  );

  if (role !== 'guest') {
    if (state.camera === 'with_camera') {
      list = list.filter((u) => Array.isArray(u.cameras) && u.cameras.length > 0);
    } else if (state.camera === 'without_camera') {
      list = list.filter((u) => !Array.isArray(u.cameras) || u.cameras.length === 0);
    }
  }

  list.sort((a, b) => {
    const ta = a && a.created_at ? Date.parse(a.created_at) : NaN;
    const tb = b && b.created_at ? Date.parse(b.created_at) : NaN;
    const hasDates = Number.isFinite(ta) && Number.isFinite(tb) && ta !== tb;
    if (state.sort === 'oldest') {
      return hasDates ? ta - tb : Number(a.id) - Number(b.id);
    }
    return hasDates ? tb - ta : Number(b.id) - Number(a.id);
  });

  return list;
}

export async function loadAndRenderUnits(options = {}) {
  const container = document.getElementById('units-grid');
  const refreshBtn = document.getElementById('units-refresh-btn');
  if (!container) return;

  if (options && options.bustCache) {
    cacheBuster = Date.now();
  }

  if (refreshBtn) {
    refreshBtn.classList.add('rotating');
  }

  try {
    const includeArchived = currentRole !== 'guest' && unitsFilterState.status === 'archived';
    const installations = await api.getInstallations(includeArchived);
    const filteredUnits = filterAndSortUnits(installations, currentRole, unitsFilterState);
    const showCreateCard =
      currentRole === 'developer' && unitsFilterState.status === 'active';

    container.innerHTML = '';

    if (filteredUnits.length === 0 && !showCreateCard) {
      renderEmptyList(container);
      return;
    }

    filteredUnits.forEach((unit) => {
      const card = document.createElement('div');
      card.className = 'unit-card glass-panel';
      if (unit.is_archived) {
        card.classList.add('unit-card-archived');
      }
      if (currentRole === 'biologist' || currentRole === 'developer') {
        card.classList.add('unit-card-clickable');
      }
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
          previewWrapper.innerHTML = getPlaceholderHTML('D');
        };
        previewWrapper.appendChild(img);
      } else {
        previewWrapper.innerHTML = getPlaceholderHTML('A');
      }

      if (unit.is_archived) {
        const archBadge = document.createElement('span');
        archBadge.className = 'unit-archived-badge';
        archBadge.textContent = 'Архив';
        previewWrapper.appendChild(archBadge);
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

      // Время следующего планового кадра для Biologist и Developer
      if (
        (currentRole === 'biologist' || currentRole === 'developer') &&
        unit.next_capture_at
      ) {
        const nextCaptureEl = document.createElement('div');
        nextCaptureEl.className = 'unit-next-capture';
        const d = new Date(unit.next_capture_at);
        const timeStr = !isNaN(d.getTime())
          ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
          : unit.next_capture_at;
        nextCaptureEl.innerHTML = `
          <svg class="icon-svg" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
          <span>След. кадр: ${timeStr}</span>
        `;
        infoDiv.appendChild(nextCaptureEl);
      }

      card.appendChild(infoDiv);

      // Кнопки действий (только для авторизованных)
      if (currentRole === 'biologist' || currentRole === 'developer') {
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'unit-actions';

        // Кнопка «Live»
        const liveBtn = document.createElement('button');
        liveBtn.type = 'button';
        liveBtn.className = 'btn btn-secondary unit-live-btn';
        liveBtn.innerHTML = `
          <svg class="icon-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="23 7 16 12 23 17 23 7"/>
            <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
          </svg>
          <span>Live</span>
        `;
        const hasCameras = unit.cameras && unit.cameras.length > 0;
        if (unit.is_archived || !hasCameras) {
          liveBtn.disabled = true;
          liveBtn.title = unit.is_archived ? 'Установка в архиве' : 'Камеры не привязаны';
        } else {
          liveBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (typeof onOpenLiveCallback === 'function') {
              onOpenLiveCallback(unit);
            }
          });
        }
        actionsDiv.appendChild(liveBtn);

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
          galleryBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (typeof onOpenGalleryCallback === 'function') {
              onOpenGalleryCallback(unit);
            }
          });
        }
        actionsDiv.appendChild(galleryBtn);

        // Кнопка «Снимок» (тестовый снимок)
        const snapBtn = document.createElement('button');
        snapBtn.type = 'button';
        snapBtn.className = 'btn btn-primary unit-snap-btn';
        snapBtn.title = unit.is_archived ? 'Установка в архиве' : 'Тестовый снимок';
        snapBtn.innerHTML = `
          <svg class="icon-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
            <circle cx="12" cy="13" r="4"/>
          </svg>
          <span>Снимок</span>
        `;
        if (unit.is_archived) {
          snapBtn.disabled = true;
        } else {
          snapBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (typeof onOpenTestSnapCallback === 'function') {
              onOpenTestSnapCallback(unit);
            }
          });
        }
        actionsDiv.appendChild(snapBtn);

        card.appendChild(actionsDiv);

        card.addEventListener('click', (e) => {
          if (e.target.closest('button, .unit-actions, select, a, input')) return;
          if (typeof onEditUnitCallback === 'function') {
            onEditUnitCallback(unit);
          }
        });
      }

      container.appendChild(card);
    });

    if (showCreateCard) {
      container.appendChild(createNewUnitCard());
    }
  } catch (_err) {
    if (container && (!container.children || container.children.length === 0)) {
      container.innerHTML = `
        <div class="empty-state glass-panel">
          ${getPlaceholderHTML('C')}
        </div>
      `;
    }
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
      loadAndRenderUnits({ bustCache: true });
    });
  }

  const statusGroup = document.getElementById('units-filter-status');
  if (statusGroup) {
    statusGroup.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const nextStatus = btn.dataset.filterStatus;
        if (!nextStatus || unitsFilterState.status === nextStatus) return;
        unitsFilterState.status = nextStatus;
        syncFilterButtonsUi();
        loadAndRenderUnits();
      });
    });
  }

  const sortGroup = document.getElementById('units-filter-sort');
  if (sortGroup) {
    sortGroup.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const nextSort = btn.dataset.filterSort;
        if (!nextSort || unitsFilterState.sort === nextSort) return;
        unitsFilterState.sort = nextSort;
        syncFilterButtonsUi();
        loadAndRenderUnits();
      });
    });
  }

  const cameraGroup = document.getElementById('units-filter-camera');
  if (cameraGroup) {
    cameraGroup.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const nextCamera = btn.dataset.filterCamera;
        if (!nextCamera || unitsFilterState.camera === nextCamera) return;
        unitsFilterState.camera = nextCamera;
        syncFilterButtonsUi();
        loadAndRenderUnits();
      });
    });
  }

  syncFilterButtonsUi();
}
