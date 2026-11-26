// clients/web/js/profile.js
// Модальные окна «Учётная запись» и «Активные сессии» (для разработчика)

import { api } from './api.js';
import { Modal, showConfirm, showToast } from './dialogs.js';

let currentUserData = null;
let profileUpdatedCallback = null;
let selfTerminatedCallback = null;
let profileModalCtrl = null;
let sessionsModalCtrl = null;

function getProfileModalCtrl() {
  if (!profileModalCtrl) {
    profileModalCtrl = new Modal('modal-profile', {
      closeBtn: 'modal-profile-close',
      onClose: () => {
        setInlineError('profile-form-error', '');
      },
    });
  }
  return profileModalCtrl;
}

function getSessionsModalCtrl() {
  if (!sessionsModalCtrl) {
    sessionsModalCtrl = new Modal('modal-sessions', {
      closeBtn: 'modal-sessions-close',
      onClose: () => {
        setInlineError('sessions-error', '');
      },
    });
  }
  return sessionsModalCtrl;
}

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

function splitFullName(fullName) {
  const cleaned = (fullName || '').trim();
  if (!cleaned) return { first: '', last: '' };
  const idx = cleaned.indexOf(' ');
  if (idx === -1) return { first: cleaned, last: '' };
  return {
    first: cleaned.slice(0, idx).trim(),
    last: cleaned.slice(idx + 1).trim(),
  };
}

function resolveTelegramUrl(externalId, telegramUsername, telegramUrl) {
  if (telegramUrl) return String(telegramUrl);
  const cleanUsername = telegramUsername
    ? String(telegramUsername).replace(/^@/, '').trim()
    : '';
  if (cleanUsername) {
    return `https://t.me/${encodeURIComponent(cleanUsername)}`;
  }
  const ext = externalId ? String(externalId).trim() : '';
  if (/^-?\d+$/.test(ext)) {
    return `tg://user?id=${ext}`;
  }
  return null;
}

function renderTelegramValue(container, externalId, telegramUsername, telegramUrl) {
  if (!container) return;
  container.textContent = '';

  const cleanUsername = telegramUsername
    ? String(telegramUsername).replace(/^@/, '').trim()
    : '';
  const url = resolveTelegramUrl(externalId, cleanUsername, telegramUrl);
  const extStr = externalId ? String(externalId).trim() : '';

  if (url) {
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.className = 'profile-tg-link';
    link.textContent = cleanUsername ? `@${cleanUsername}` : `ID: ${extStr}`;
    container.appendChild(link);
    if (cleanUsername && extStr && !extStr.startsWith('@') && !extStr.startsWith('web:')) {
      const idSpan = document.createElement('span');
      idSpan.className = 'profile-ext-id';
      idSpan.textContent = ` (ID: ${extStr})`;
      container.appendChild(idSpan);
    }
    return;
  }

  if (extStr.startsWith('web:')) {
    container.textContent = 'Не указан';
    return;
  }

  container.textContent = extStr || '—';
}

function fillProfileModal(data) {
  if (!data) return;
  const roleEl = document.getElementById('profile-role-val');
  const clientTypeEl = document.getElementById('profile-client-type-val');
  const tgEl = document.getElementById('profile-telegram-val');
  const authAtEl = document.getElementById('profile-authorized-at-val');
  const lastActEl = document.getElementById('profile-last-active-at-val');
  const firstNameInput = document.getElementById('profile-first-name-input');
  const lastNameInput = document.getElementById('profile-last-name-input');
  const tgInput = document.getElementById('profile-telegram-input');
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
  renderTelegramValue(tgEl, data.external_id, data.telegram_username, data.telegram_url);
  if (authAtEl) {
    authAtEl.textContent = formatDateTimeRu(data.authorized_at);
  }
  if (lastActEl) {
    lastActEl.textContent = formatDateTimeRu(data.last_active_at);
  }
  if (typeof data.login === 'string') {
    const { first, last } = splitFullName(data.login);
    if (firstNameInput) firstNameInput.value = first;
    if (lastNameInput) lastNameInput.value = last;
  }
  if (tgInput) {
    const cleanUsername = data.telegram_username
      ? String(data.telegram_username).replace(/^@/, '').trim()
      : '';
    const extStr = data.external_id ? String(data.external_id).trim() : '';
    if (cleanUsername) {
      tgInput.value = `@${cleanUsername}`;
    } else if (extStr && !extStr.startsWith('web:')) {
      tgInput.value = extStr;
    } else {
      tgInput.value = '';
    }
    tgInput.disabled = data.client_type && data.client_type !== 'web';
  }
  if (devActions) {
    devActions.classList.toggle('hidden', data.role !== 'developer');
  }
}

export async function openProfileModal() {
  if (!currentUserData || !currentUserData.authenticated || currentUserData.role === 'guest') {
    return;
  }
  const modalCtrl = getProfileModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;

  setInlineError('profile-form-error', '');
  fillProfileModal(currentUserData);

  modalCtrl.open();

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
  getProfileModalCtrl().close();
}

async function handleProfileFormSubmit(e) {
  e.preventDefault();
  setInlineError('profile-form-error', '');

  const fnInput = document.getElementById('profile-first-name-input');
  const lnInput = document.getElementById('profile-last-name-input');
  const tgInput = document.getElementById('profile-telegram-input');
  const saveBtn = document.getElementById('profile-save-btn');

  const firstName = fnInput ? fnInput.value.trim() : '';
  const lastName = lnInput ? lnInput.value.trim() : '';
  const telegram = tgInput ? tgInput.value.trim() : '';
  const fullName = [firstName, lastName].filter(Boolean).join(' ');

  if (!fullName) {
    setInlineError('profile-form-error', 'Имя пользователя не может быть пустым');
    return;
  }
  if (fullName.length > 64) {
    setInlineError('profile-form-error', 'Длина имени не должна превышать 64 символа');
    return;
  }

  const payload = {
    login: fullName,
    first_name: firstName,
    last_name: lastName || null,
  };
  if (tgInput && !tgInput.disabled) {
    payload.telegram = telegram;
  }

  if (saveBtn) saveBtn.disabled = true;
  try {
    const updated = await api.updateProfile(payload);
    currentUserData = { ...(currentUserData || {}), ...updated, authenticated: true };
    fillProfileModal(currentUserData);
    if (typeof profileUpdatedCallback === 'function') {
      profileUpdatedCallback(currentUserData);
    }
    showToast('Профиль обновлён');
  } catch (err) {
    setInlineError('profile-form-error', err.message || 'Не удалось сохранить профиль');
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

export async function openSessionsModal() {
  if (!currentUserData || currentUserData.role !== 'developer') return;
  closeProfileModal();

  const modalCtrl = getSessionsModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;

  modalCtrl.open();

  await loadAndRenderSessions();
}

export function closeSessionsModal() {
  getSessionsModalCtrl().close();
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
    const tgUrl = resolveTelegramUrl(s.external_id, cleanUsername, s.telegram_url);
    if (tgUrl) {
      const userLink = document.createElement('a');
      userLink.href = tgUrl;
      userLink.target = '_blank';
      userLink.rel = 'noopener noreferrer';
      userLink.className = 'session-user-link';
      userLink.textContent = s.login || (cleanUsername ? `@${cleanUsername}` : `ID: ${s.external_id}`);
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
    const extStr = s.external_id ? String(s.external_id) : '';
    const idPart = extStr.startsWith('web:') ? 'Без Telegram' : `ID: ${extStr}`;
    const tgPart = cleanUsername ? ` · @${cleanUsername}` : '';
    whereVal.textContent = `${formatClientTypeLabel(s.client_type)} · ${idPart}${tgPart}`;
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

  getProfileModalCtrl().bind();
  getSessionsModalCtrl().bind();

  const profileForm = document.getElementById('profile-form');
  if (profileForm) {
    profileForm.addEventListener('submit', handleProfileFormSubmit);
  }

  const sessionsBtn = document.getElementById('profile-btn-sessions');
  if (sessionsBtn) {
    sessionsBtn.addEventListener('click', openSessionsModal);
  }

  const sessionsRefreshBtn = document.getElementById('sessions-refresh-btn');
  if (sessionsRefreshBtn) {
    sessionsRefreshBtn.addEventListener('click', loadAndRenderSessions);
  }
}
