// clients/web/js/profile.js
// Модальные окна «Учётная запись» и «Активные сессии» (для разработчика)

import { api } from './api.js';
import { showConfirm, showToast } from './dialogs.js';

let currentUserData = null;
let profileUpdatedCallback = null;
let selfTerminatedCallback = null;

export function setProfileUser(user) {
  currentUserData = user ? { ...user } : null;
  const devActions = document.getElementById('profile-dev-actions');
  if (devActions) {
    devActions.classList.toggle('hidden', !user || user.role !== 'developer');
  }
  if (!user || user.role === 'guest') {
    closeProfileModal();
    closeSessionsModal();
  }
}

export function setProfileUpdatedHandler(fn) {
  profileUpdatedCallback = fn;
}

export function setSelfSessionTerminatedHandler(fn) {
  selfTerminatedCallback = fn;
}

export function formatRoleLabel(role) {
  if (role === 'developer') return 'Разработчик';
  if (role === 'biologist') return 'Биолог';
  return 'Гость';
}

export function formatClientTypeLabel(clientType) {
  if (clientType === 'web') return 'Веб-интерфейс (web)';
  if (clientType === 'telegram') return 'Telegram-бот (telegram)';
  if (clientType === 'cli') return 'CLI (cli)';
  return clientType || '—';
}

export function formatDateTimeRu(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  if (Number.isNaN(d.getTime())) return String(isoStr);
  return d.toLocaleString('ru-RU');
}

function setInlineError(elementId, msg) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
  } else {
    el.textContent = '';
    el.classList.add('hidden');
  }
}

function renderTelegramValue(container, externalId, telegramUsername) {
  if (!container) return;
  container.textContent = '';

  const cleanUsername = telegramUsername
    ? String(telegramUsername).replace(/^@/, '').trim()
    : '';
  if (cleanUsername) {
    const link = document.createElement('a');
    link.href = `https://t.me/${encodeURIComponent(cleanUsername)}`;
    link.target = '_blank';
    link.rel = 'noopener';
    link.className = 'profile-tg-link';
    link.textContent = `@${cleanUsername}`;
    container.appendChild(link);
    if (externalId) {
      const idSpan = document.createElement('span');
      idSpan.className = 'profile-ext-id';
      idSpan.textContent = ` (ID: ${externalId})`;
      container.appendChild(idSpan);
    }
    return;
  }

  container.textContent = externalId ? String(externalId) : '—';
}

function fillProfileModal(data) {
  if (!data) return;
  const roleEl = document.getElementById('profile-role-val');
  const clientTypeEl = document.getElementById('profile-client-type-val');
  const tgEl = document.getElementById('profile-telegram-val');
  const authAtEl = document.getElementById('profile-authorized-at-val');
  const lastActEl = document.getElementById('profile-last-active-at-val');
  const loginInput = document.getElementById('profile-login-input');
  const devActions = document.getElementById('profile-dev-actions');

  if (roleEl) {
    roleEl.textContent = '';
    const badge = document.createElement('span');
    badge.className = `role-badge role-${data.role || 'guest'}`;
    badge.textContent = formatRoleLabel(data.role);
    roleEl.appendChild(badge);
  }
  if (clientTypeEl) {
    clientTypeEl.textContent = formatClientTypeLabel(data.client_type || 'web');
  }
  renderTelegramValue(tgEl, data.external_id, data.telegram_username);
  if (authAtEl) {
    authAtEl.textContent = formatDateTimeRu(data.authorized_at);
  }
  if (lastActEl) {
    lastActEl.textContent = formatDateTimeRu(data.last_active_at);
  }
  if (loginInput && typeof data.login === 'string') {
    loginInput.value = data.login;
  }
  if (devActions) {
    devActions.classList.toggle('hidden', data.role !== 'developer');
  }
}

export async function openProfileModal() {
  if (!currentUserData || !currentUserData.authenticated || currentUserData.role === 'guest') {
    return;
  }
  const modal = document.getElementById('modal-profile');
  if (!modal) return;

  setInlineError('profile-form-error', '');
  fillProfileModal(currentUserData);

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  try {
    const fresh = await api.getProfile();
    currentUserData = { ...currentUserData, ...fresh, authenticated: true };
    fillProfileModal(currentUserData);
    if (typeof profileUpdatedCallback === 'function') {
      profileUpdatedCallback(currentUserData);
    }
  } catch (err) {
    setInlineError('profile-form-error', err.message || 'Не удалось загрузить данные профиля');
  }
}

export function closeProfileModal() {
  const modal = document.getElementById('modal-profile');
  if (modal) {
    modal.classList.add('hidden');
  }
  const sessionsModal = document.getElementById('modal-sessions');
  if (!sessionsModal || sessionsModal.classList.contains('hidden')) {
    document.body.classList.remove('modal-open');
  }
  setInlineError('profile-form-error', '');
}

async function handleProfileFormSubmit(e) {
  e.preventDefault();
  setInlineError('profile-form-error', '');

  const input = document.getElementById('profile-login-input');
  const saveBtn = document.getElementById('profile-save-btn');
  const newLogin = input ? input.value.trim() : '';

  if (!newLogin) {
    setInlineError('profile-form-error', 'Имя пользователя не может быть пустым');
    return;
  }
  if (newLogin.length > 64) {
    setInlineError('profile-form-error', 'Длина имени не должна превышать 64 символа');
    return;
  }

  if (saveBtn) saveBtn.disabled = true;
  try {
    const updated = await api.updateProfile(newLogin);
    currentUserData = { ...(currentUserData || {}), ...updated, authenticated: true };
    fillProfileModal(currentUserData);
    if (typeof profileUpdatedCallback === 'function') {
      profileUpdatedCallback(currentUserData);
    }
    showToast('Имя пользователя обновлено');
  } catch (err) {
    setInlineError('profile-form-error', err.message || 'Не удалось сохранить имя пользователя');
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

export async function openSessionsModal() {
  if (!currentUserData || currentUserData.role !== 'developer') return;
  closeProfileModal();

  const modal = document.getElementById('modal-sessions');
  if (!modal) return;

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  await loadAndRenderSessions();
}

export function closeSessionsModal() {
  const modal = document.getElementById('modal-sessions');
  if (modal) {
    modal.classList.add('hidden');
  }
  const profileModal = document.getElementById('modal-profile');
  if (!profileModal || profileModal.classList.contains('hidden')) {
    document.body.classList.remove('modal-open');
  }
  setInlineError('sessions-error', '');
}

async function loadAndRenderSessions() {
  const listEl = document.getElementById('sessions-list');
  const refreshBtn = document.getElementById('sessions-refresh-btn');
  setInlineError('sessions-error', '');
  if (!listEl) return;

  listEl.innerHTML = '<div class="sessions-loading">Загрузка списка сессий...</div>';
  if (refreshBtn) refreshBtn.disabled = true;

  try {
    const sessions = await api.getSessions();
    renderSessionsList(listEl, Array.isArray(sessions) ? sessions : []);
  } catch (err) {
    listEl.textContent = '';
    setInlineError('sessions-error', err.message || 'Ошибка загрузки списка сессий');
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function renderSessionsList(container, sessions) {
  container.textContent = '';
  if (!sessions.length) {
    const empty = document.createElement('div');
    empty.className = 'sessions-empty';
    empty.textContent = 'Сессии не найдены';
    container.appendChild(empty);
    return;
  }

  for (const s of sessions) {
    const card = document.createElement('div');
    card.className = `session-card ${s.is_active ? 'session-active' : 'session-inactive'}`;

    // 1. Шапка карточки: Кто + роль + статус
    const topRow = document.createElement('div');
    topRow.className = 'session-card-top';

    const whoWrap = document.createElement('div');
    whoWrap.className = 'session-who';

    const cleanUsername = s.telegram_username
      ? String(s.telegram_username).replace(/^@/, '').trim()
      : '';
    if (cleanUsername) {
      const userLink = document.createElement('a');
      userLink.href = `https://t.me/${encodeURIComponent(cleanUsername)}`;
      userLink.target = '_blank';
      userLink.rel = 'noopener';
      userLink.className = 'session-user-link';
      userLink.textContent = s.login || `@${cleanUsername}`;
      whoWrap.appendChild(userLink);
    } else {
      const userSpan = document.createElement('span');
      userSpan.className = 'session-user-name';
      userSpan.textContent = s.login || 'Без имени';
      whoWrap.appendChild(userSpan);
    }

    const roleBadge = document.createElement('span');
    roleBadge.className = `role-badge role-${s.role}`;
    roleBadge.textContent = formatRoleLabel(s.role);
    whoWrap.appendChild(roleBadge);

    const isSelf = Boolean(s.is_current || (currentUserData && s.id === currentUserData.id));
    if (isSelf) {
      const curBadge = document.createElement('span');
      curBadge.className = 'session-current-badge';
      curBadge.textContent = 'Текущая';
      whoWrap.appendChild(curBadge);
    }

    const statusBadge = document.createElement('span');
    statusBadge.className = `session-status-badge ${s.is_active ? 'online' : 'offline'}`;
    statusBadge.textContent = s.is_active ? 'Онлайн' : 'Офлайн';

    topRow.appendChild(whoWrap);
    topRow.appendChild(statusBadge);
    card.appendChild(topRow);

    // 2. Метаданные: Где + Когда вошёл
    const metaGrid = document.createElement('div');
    metaGrid.className = 'session-meta-grid';

    const whereItem = document.createElement('div');
    whereItem.className = 'session-meta-item';
    const whereLabel = document.createElement('span');
    whereLabel.className = 'session-meta-label';
    whereLabel.textContent = 'Где';
    const whereVal = document.createElement('span');
    whereVal.className = 'session-meta-val';
    const tgPart = cleanUsername ? ` · @${cleanUsername}` : '';
    whereVal.textContent = `${formatClientTypeLabel(s.client_type)} · ID: ${s.external_id}${tgPart}`;
    whereItem.appendChild(whereLabel);
    whereItem.appendChild(whereVal);

    const authItem = document.createElement('div');
    authItem.className = 'session-meta-item';
    const authLabel = document.createElement('span');
    authLabel.className = 'session-meta-label';
    authLabel.textContent = 'Вход';
    const authVal = document.createElement('span');
    authVal.className = 'session-meta-val';
    authVal.textContent = formatDateTimeRu(s.authorized_at);
    authItem.appendChild(authLabel);
    authItem.appendChild(authVal);

    const actItem = document.createElement('div');
    actItem.className = 'session-meta-item';
    const actLabel = document.createElement('span');
    actLabel.className = 'session-meta-label';
    actLabel.textContent = 'Активность';
    const actVal = document.createElement('span');
    actVal.className = 'session-meta-val';
    actVal.textContent = formatDateTimeRu(s.last_active_at);
    actItem.appendChild(actLabel);
    actItem.appendChild(actVal);

    metaGrid.appendChild(whereItem);
    metaGrid.appendChild(authItem);
    metaGrid.appendChild(actItem);
    card.appendChild(metaGrid);

    // 3. Действие для активных сеансов
    if (s.is_active) {
      const actionsRow = document.createElement('div');
      actionsRow.className = 'session-card-actions';

      const termBtn = document.createElement('button');
      termBtn.type = 'button';
      termBtn.className = 'btn btn-danger btn-compact session-terminate-btn';
      termBtn.textContent = isSelf ? 'Завершить свой сеанс' : 'Завершить сеанс';
      termBtn.addEventListener('click', () => handleTerminateSession(s, isSelf, termBtn));

      actionsRow.appendChild(termBtn);
      card.appendChild(actionsRow);
    }

    container.appendChild(card);
  }
}

async function handleTerminateSession(session, isSelf, btnEl) {
  const promptMsg = isSelf
    ? 'Вы завершаете свой текущий веб-сеанс. После этого потребуется войти снова. Продолжить?'
    : `Принудительно завершить сеанс «${session.login}» (${formatClientTypeLabel(session.client_type)})?`;

  const confirmed = await showConfirm(promptMsg, 'Завершение сеанса', 'Завершить', 'Отмена');
  const modal = document.getElementById('modal-sessions');
  if (modal && !modal.classList.contains('hidden')) {
    document.body.classList.add('modal-open');
  }
  if (!confirmed) return;

  if (btnEl) btnEl.disabled = true;
  try {
    await api.terminateSession(session.id);
    if (isSelf) {
      closeSessionsModal();
      closeProfileModal();
      if (typeof selfTerminatedCallback === 'function') {
        await selfTerminatedCallback();
      }
      return;
    }
    showToast(`Сеанс «${session.login}» завершён`);
    await loadAndRenderSessions();
  } catch (err) {
    setInlineError('sessions-error', err.message || 'Не удалось завершить сеанс');
    if (btnEl) btnEl.disabled = false;
  }
}

export function initProfile() {
  const headerUserBtn = document.getElementById('header-user-name');
  if (headerUserBtn) {
    headerUserBtn.addEventListener('click', openProfileModal);
  }

  const profileModal = document.getElementById('modal-profile');
  const profileCloseBtn = document.getElementById('modal-profile-close');
  if (profileCloseBtn) {
    profileCloseBtn.addEventListener('click', closeProfileModal);
  }
  if (profileModal) {
    profileModal.addEventListener('click', (e) => {
      if (e.target === profileModal) closeProfileModal();
    });
  }

  const profileForm = document.getElementById('profile-form');
  if (profileForm) {
    profileForm.addEventListener('submit', handleProfileFormSubmit);
  }

  const sessionsBtn = document.getElementById('profile-btn-sessions');
  if (sessionsBtn) {
    sessionsBtn.addEventListener('click', openSessionsModal);
  }

  const sessionsModal = document.getElementById('modal-sessions');
  const sessionsCloseBtn = document.getElementById('modal-sessions-close');
  const sessionsRefreshBtn = document.getElementById('sessions-refresh-btn');
  if (sessionsCloseBtn) {
    sessionsCloseBtn.addEventListener('click', closeSessionsModal);
  }
  if (sessionsRefreshBtn) {
    sessionsRefreshBtn.addEventListener('click', loadAndRenderSessions);
  }
  if (sessionsModal) {
    sessionsModal.addEventListener('click', (e) => {
      if (e.target === sessionsModal) closeSessionsModal();
    });
  }

  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (sessionsModal && !sessionsModal.classList.contains('hidden')) {
      closeSessionsModal();
    } else if (profileModal && !profileModal.classList.contains('hidden')) {
      closeProfileModal();
    }
  });
}
