// clients/web/js/units.js
// Отображение установок с точечным diff-обновлением DOM-карточек по data-unit-id, фильтры/сортировка и кнопки действий

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

  const existingEmpty = container.querySelector('.empty-state-container');
  if (existingEmpty) {
    const titleEl = existingEmpty.querySelector('.empty-state-title');
    const descEl = existingEmpty.querySelector('.empty-state-desc');
    if (titleEl && titleEl.textContent !== title) titleEl.textContent = title;
    if (descEl && descEl.textContent !== desc) descEl.textContent = desc;
    return;
  }

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

function formatNextCaptureTime(nextCaptureAt) {
  const d = new Date(nextCaptureAt);
  return !isNaN(d.getTime())
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : String(nextCaptureAt);
}

function buildUnitActionsElement(card) {
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
  liveBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const u = card._unitData;
    if (!u || liveBtn.disabled) return;
    if (typeof onOpenLiveCallback === 'function') {
      onOpenLiveCallback(u);
    }
  });
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
  galleryBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const u = card._unitData;
    if (!u || galleryBtn.disabled) return;
    if (typeof onOpenGalleryCallback === 'function') {
      onOpenGalleryCallback(u);
    }
  });
  actionsDiv.appendChild(galleryBtn);

  // Кнопка «Снимок» (тестовый снимок)
  const snapBtn = document.createElement('button');
  snapBtn.type = 'button';
  snapBtn.className = 'btn btn-primary unit-snap-btn';
  snapBtn.innerHTML = `
    <svg class="icon-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
      <circle cx="12" cy="13" r="4"/>
    </svg>
    <span>Снимок</span>
  `;
  snapBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const u = card._unitData;
    if (!u || snapBtn.disabled) return;
    if (typeof onOpenTestSnapCallback === 'function') {
      onOpenTestSnapCallback(u);
    }
  });
  actionsDiv.appendChild(snapBtn);

  return actionsDiv;
}

function createUnitCardElement(unit) {
  const card = document.createElement('div');
  card.className = 'unit-card glass-panel';
  card.dataset.unitId = String(unit.id);
  card.dataset.id = String(unit.id);

  const previewWrapper = document.createElement('div');
  previewWrapper.className = 'unit-preview-wrapper';
  card.appendChild(previewWrapper);

  const infoDiv = document.createElement('div');
  infoDiv.className = 'unit-info';

  const titleEl = document.createElement('h3');
  titleEl.className = 'unit-name';
  infoDiv.appendChild(titleEl);

  const cultureEl = document.createElement('div');
  cultureEl.className = 'unit-culture';
  infoDiv.appendChild(cultureEl);

  card.appendChild(infoDiv);

  card.addEventListener('click', (e) => {
    if (currentRole !== 'biologist' && currentRole !== 'developer') return;
    if (e.target.closest('button, .unit-actions, select, a, input')) return;
    const u = card._unitData;
    if (u && typeof onEditUnitCallback === 'function') {
      onEditUnitCallback(u);
    }
  });

  return card;
}

function updateUnitCardPreview(previewWrapper, unit) {
  const framesCount = Number(unit.frames_count || 0);
  const frameUrl = unit.last_frame_url || unit.last_frame_raw_url || '';
  const archBadge = previewWrapper.querySelector('.unit-archived-badge');

  if (framesCount > 0 && frameUrl) {
    const frameStamp = unit.last_captured_at || unit.updated_at || '';
    const nextFrameKey = `${frameUrl}|${frameStamp}|${framesCount}|${cacheBuster}`;
    let img = previewWrapper.querySelector('img.unit-preview-img');

    if (!img) {
      previewWrapper.querySelectorAll('.media-placeholder').forEach((el) => el.remove());
      img = document.createElement('img');
      img.className = 'unit-preview-img';
      img.loading = 'lazy';
      img.onerror = () => {
        const badge = previewWrapper.querySelector('.unit-archived-badge');
        previewWrapper.innerHTML = getPlaceholderHTML('D');
        if (badge) previewWrapper.appendChild(badge);
      };
      if (archBadge) {
        previewWrapper.insertBefore(img, archBadge);
      } else {
        previewWrapper.appendChild(img);
      }
    }

    const nextAlt = unit.name || '';
    if (img.alt !== nextAlt) {
      img.alt = nextAlt;
    }
    if (img.dataset.frameKey !== nextFrameKey) {
      img.dataset.frameKey = nextFrameKey;
      const sep = frameUrl.includes('?') ? '&' : '?';
      img.src = `${frameUrl}${sep}t=${cacheBuster}`;
    }
  } else {
    const existingImg = previewWrapper.querySelector('img.unit-preview-img');
    const existingPlaceholderA = previewWrapper.querySelector('.media-placeholder.category-a');
    if (existingImg) existingImg.remove();
    if (!existingPlaceholderA) {
      previewWrapper.querySelectorAll('.media-placeholder').forEach((el) => el.remove());
      const tpl = document.createElement('div');
      tpl.innerHTML = getPlaceholderHTML('A');
      const placeholderNode = tpl.firstElementChild;
      if (placeholderNode) {
        if (archBadge) {
          previewWrapper.insertBefore(placeholderNode, archBadge);
        } else {
          previewWrapper.appendChild(placeholderNode);
        }
      }
    }
  }

  if (unit.is_archived) {
    if (!archBadge) {
      const badge = document.createElement('span');
      badge.className = 'unit-archived-badge';
      badge.textContent = 'Архив';
      previewWrapper.appendChild(badge);
    }
  } else if (archBadge) {
    archBadge.remove();
  }
}

function updateUnitCardElement(card, unit) {
  card._unitData = unit;
  card.dataset.unitId = String(unit.id);
  card.dataset.id = String(unit.id);

  const isAuth = currentRole === 'biologist' || currentRole === 'developer';
  card.classList.toggle('unit-card-archived', Boolean(unit.is_archived));
  card.classList.toggle('unit-card-clickable', isAuth);

  // Превью
  const previewWrapper = card.querySelector('.unit-preview-wrapper');
  if (previewWrapper) {
    updateUnitCardPreview(previewWrapper, unit);
  }

  // Информация об установке
  const infoDiv = card.querySelector('.unit-info');
  if (infoDiv) {
    const titleEl = infoDiv.querySelector('.unit-name');
    const nextName = unit.name || '';
    if (titleEl && titleEl.textContent !== nextName) {
      titleEl.textContent = nextName;
    }

    const cultureEl = infoDiv.querySelector('.unit-culture');
    const nextCulture = unit.culture_name || '';
    if (cultureEl && cultureEl.textContent !== nextCulture) {
      cultureEl.textContent = nextCulture;
    }

    let nextCaptureEl = infoDiv.querySelector('.unit-next-capture');
    if (isAuth && unit.next_capture_at) {
      const nextText = `След. кадр: ${formatNextCaptureTime(unit.next_capture_at)}`;
      if (!nextCaptureEl) {
        nextCaptureEl = document.createElement('div');
        nextCaptureEl.className = 'unit-next-capture';
        nextCaptureEl.innerHTML = `
          <svg class="icon-svg" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
          <span></span>
        `;
        infoDiv.appendChild(nextCaptureEl);
      }
      const spanEl = nextCaptureEl.querySelector('span');
      if (spanEl && spanEl.textContent !== nextText) {
        spanEl.textContent = nextText;
      }
    } else if (nextCaptureEl) {
      nextCaptureEl.remove();
    }
  }

  // Кнопки действий (только для авторизованных)
  let actionsDiv = card.querySelector('.unit-actions');
  if (isAuth) {
    if (!actionsDiv) {
      actionsDiv = buildUnitActionsElement(card);
      card.appendChild(actionsDiv);
    }

    const framesCount = Number(unit.frames_count || 0);
    const hasCameras = Array.isArray(unit.cameras) && unit.cameras.length > 0;
    const liveBtn = actionsDiv.querySelector('.unit-live-btn');
    const galleryBtn = actionsDiv.querySelector('.unit-gallery-btn');
    const snapBtn = actionsDiv.querySelector('.unit-snap-btn');

    if (liveBtn) {
      const liveDisabled = Boolean(unit.is_archived || !hasCameras);
      const liveTitle = unit.is_archived
        ? 'Установка в архиве'
        : !hasCameras
          ? 'Камеры не привязаны'
          : '';
      if (liveBtn.disabled !== liveDisabled) liveBtn.disabled = liveDisabled;
      if (liveBtn.title !== liveTitle) liveBtn.title = liveTitle;
    }

    if (galleryBtn) {
      const galleryDisabled = framesCount === 0;
      const galleryTitle = framesCount === 0 ? 'Кадров пока нет' : '';
      if (galleryBtn.disabled !== galleryDisabled) galleryBtn.disabled = galleryDisabled;
      if (galleryBtn.title !== galleryTitle) galleryBtn.title = galleryTitle;
    }

    if (snapBtn) {
      const snapDisabled = Boolean(unit.is_archived);
      const snapTitle = unit.is_archived ? 'Установка в архиве' : 'Тестовый снимок';
      if (snapBtn.disabled !== snapDisabled) snapBtn.disabled = snapDisabled;
      if (snapBtn.title !== snapTitle) snapBtn.title = snapTitle;
    }
  } else if (actionsDiv) {
    actionsDiv.remove();
  }
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

    if (filteredUnits.length === 0 && !showCreateCard) {
      container.querySelectorAll('.unit-card, .empty-state').forEach((el) => el.remove());
      renderEmptyList(container);
      return;
    }

    // Убираем заглушки пустого состояния, если нужно отображать карточки
    container.querySelectorAll('.empty-state-container, .empty-state').forEach((el) => el.remove());

    // Индексируем существующие карточки по data-unit-id для точечного diff-обновления
    const existingById = new Map();
    container.querySelectorAll('.unit-card[data-unit-id]').forEach((card) => {
      existingById.set(String(card.dataset.unitId), card);
    });

    const desiredNodes = [];
    filteredUnits.forEach((unit) => {
      const key = String(unit.id);
      let card = existingById.get(key);
      if (card) {
        existingById.delete(key);
      } else {
        card = createUnitCardElement(unit);
      }
      updateUnitCardElement(card, unit);
      desiredNodes.push(card);
    });

    // Удаляем карточки исчезнувших установок
    existingById.forEach((obsoleteCard) => {
      obsoleteCard.remove();
    });

    // Карточка создания установки для разработчика
    let createCard = container.querySelector('#unit-create-card');
    if (showCreateCard) {
      if (!createCard) {
        createCard = createNewUnitCard();
      }
      desiredNodes.push(createCard);
    } else if (createCard) {
      createCard.remove();
    }

    // Синхронизируем порядок дочерних элементов без пересоздания узлов и потери фокуса/скролла
    desiredNodes.forEach((node, index) => {
      const currentAtIdx = container.children[index];
      if (currentAtIdx !== node) {
        container.insertBefore(node, currentAtIdx || null);
      }
    });
  } catch (_err) {
    if (container && !container.querySelector('.unit-card')) {
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
