// clients/web/js/app.js
// Главная точка входа Web SPA GrowVision

import { api, setUnauthorizedHandler } from './api.js';
import { initAuth, openLoginModal, setAuthSuccessHandler } from './auth.js';
import { initDialogs, showConfirm, showError, showToast } from './dialogs.js';
import {
  closeGalleryModal,
  initGallery,
  openGalleryModal,
  setGalleryRole,
} from './gallery.js';
import {
  closeTestSnapModal,
  initTestSnap,
  openTestSnapModal,
  setTestSnapRole,
} from './test_snap.js';
import { initRpiSetup, updateRpiRole } from './rpi_setup.js';
import {
  initUnits,
  loadAndRenderUnits,
  setUnitActionHandlers,
  updateUnitsRole,
} from './units.js';

let currentUser = null; // { authenticated: bool, role: 'guest'|'biologist'|'developer', login: string|null }

// Обновление отображения роли, имени и кнопок в шапке
function updateHeaderUi() {
  const roleBadge = document.getElementById('header-role-badge');
  const userNameEl = document.getElementById('header-user-name');
  const loginBtn = document.getElementById('header-btn-login');
  const logoutBtn = document.getElementById('header-btn-logout');
  const devSlot = document.getElementById('header-dev-slot');

  const role = currentUser ? currentUser.role : 'guest';
  const name = currentUser && currentUser.login ? currentUser.login : '';

  // Бейдж роли
  if (roleBadge) {
    roleBadge.className = `role-badge role-${role}`;
    if (role === 'developer') {
      roleBadge.textContent = 'Разработчик';
    } else if (role === 'biologist') {
      roleBadge.textContent = 'Биолог';
    } else {
      roleBadge.textContent = 'Гость';
    }
  }

  // Имя пользователя
  if (userNameEl) {
    userNameEl.textContent = name;
    userNameEl.classList.toggle('hidden', !name);
  }

  // Кнопки Вход / Выход
  if (loginBtn) {
    loginBtn.classList.toggle('hidden', role !== 'guest');
  }
  if (logoutBtn) {
    logoutBtn.classList.toggle('hidden', role === 'guest');
  }

  // Слот для разработчика (Подключить RPi - задел на след. этап)
  if (devSlot) {
    devSlot.classList.toggle('hidden', role !== 'developer');
  }
}

// Применение роли ко всем компонентам
function applyRole(role) {
  updateUnitsRole(role);
  setGalleryRole(role);
  setTestSnapRole(role);
  updateRpiRole(role);
  updateHeaderUi();
}

// Обработка выхода из системы
async function handleLogout() {
  const confirmed = await showConfirm(
    'Вы уверены, что хотите выйти из системы?',
    'Выход из профиля',
    'Выйти',
    'Отмена'
  );
  if (!confirmed) return;

  try {
    await api.logout();
    currentUser = { authenticated: false, role: 'guest', login: null };
    applyRole('guest');
    closeGalleryModal();
    closeTestSnapModal();
    await loadAndRenderUnits();
    showToast('Вы вышли из системы');
  } catch (err) {
    showError(err.message, 'Ошибка выхода');
  }
}

// Обработка потери сессии (HTTP 401)
function handleSessionExpired() {
  if (currentUser && currentUser.role !== 'guest') {
    currentUser = { authenticated: false, role: 'guest', login: null };
    applyRole('guest');
    closeGalleryModal();
    closeTestSnapModal();
    loadAndRenderUnits();
    showError('Сессия завершена. Войдите снова.', 'Сессия');
  }
}

// Загрузка версии сервера для подвала
async function loadFooterVersion() {
  const footerEl = document.getElementById('footer-version');
  if (!footerEl) return;

  try {
    const v = await api.getVersion();
    if (v && v.server_version) {
      footerEl.textContent = `GrowVision v${v.server_version}`;
      footerEl.classList.remove('hidden');
    }
  } catch (_) {
    footerEl.classList.add('hidden');
  }
}

// Первоначальная проверка сессии
async function initSession() {
  try {
    const me = await api.getMe();
    if (me && me.authenticated) {
      currentUser = me;
    } else {
      currentUser = { authenticated: false, role: 'guest', login: null };
    }
  } catch (_) {
    currentUser = { authenticated: false, role: 'guest', login: null };
  }
  applyRole(currentUser.role);
}

// Запуск приложения
async function bootstrap() {
  initDialogs();
  initAuth();
  initUnits();
  initGallery();
  initTestSnap();
  initRpiSetup();

  setUnauthorizedHandler(handleSessionExpired);

  setAuthSuccessHandler(async (res) => {
    currentUser = {
      authenticated: true,
      role: res.role,
      login: res.login,
    };
    applyRole(res.role);
    await loadAndRenderUnits();
  });

  setUnitActionHandlers({
    onOpenGallery: (unit) => openGalleryModal(unit),
    onOpenTestSnap: (unit) => openTestSnapModal(unit),
  });

  const logoutBtn = document.getElementById('header-btn-logout');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', handleLogout);
  }

  await initSession();
  await loadAndRenderUnits();
  await loadFooterVersion();
}

window.addEventListener('DOMContentLoaded', bootstrap);
