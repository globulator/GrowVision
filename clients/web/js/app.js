// clients/web/js/app.js
// Главная точка входа Web SPA GrowVision

import { api, setUnauthorizedHandler } from './api.js';
import {
  initAuth,
  openLoginModal,
  setAuthSuccessHandler,
  updateBotLinks,
} from './auth.js';
import {
  closeProfileModal,
  closeSessionsModal,
  initProfile,
  setProfileUpdatedHandler,
  setProfileUser,
  setSelfSessionTerminatedHandler,
} from './profile.js';
import { initDialogs, showError, showToast } from './dialogs.js';
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
  closeLightControlModal,
  initLightControl,
  setLightChangedHandler,
  updateLightRole,
} from './light_control.js';
import {
  initCameras,
  updateCamerasRole,
  openLiveModal,
  closeCamerasModal,
  closeLiveModal,
  setCamerasChangedHandler,
  setLiveProfileSavedHandler,
} from './cameras.js';
import {
  initUnits,
  loadAndRenderUnits,
  setUnitActionHandlers,
  updateUnitsRole,
} from './units.js';
import {
  closeUnitEditorModal,
  initUnitEditor,
  openUnitCreateModal,
  openUnitEditModal,
  setUnitEditorRole,
  setUnitSavedHandler,
} from './unit_editor.js';

let currentUser = null; // { authenticated: bool, role: 'guest'|'biologist'|'developer', login: string|null }
let backgroundPollingTimer = null;

// Обновление отображения роли, имени и кнопок в шапке
function updateHeaderUi() {
  const roleBadge = document.getElementById('header-role-badge');
  const userNameEl = document.getElementById('header-user-name');
  const loginBtn = document.getElementById('header-btn-login');
  const logoutBtn = document.getElementById('header-btn-logout');
  const logoutConfirm = document.getElementById('header-logout-confirm');
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
    userNameEl.classList.toggle('hidden', !name || role === 'guest');
  }

  // Кнопки Вход / Выход
  if (loginBtn) {
    loginBtn.classList.toggle('hidden', role !== 'guest');
  }
  if (logoutBtn) {
    logoutBtn.classList.toggle('hidden', role === 'guest');
  }
  if (logoutConfirm) {
    logoutConfirm.classList.add('hidden');
  }

  // Слот для разработчика
  if (devSlot) {
    devSlot.classList.toggle('hidden', role !== 'developer');
  }
}

// Применение роли ко всем компонентам
function applyRole(role) {
  updateUnitsRole(role);
  setUnitEditorRole(role);
  setGalleryRole(role);
  setTestSnapRole(role);
  updateRpiRole(role);
  updateLightRole(role);
  updateCamerasRole(role);
  setProfileUser(currentUser);
  updateHeaderUi();
}

function closeAllModals() {
  closeGalleryModal();
  closeTestSnapModal();
  closeCamerasModal();
  closeLiveModal();
  closeUnitEditorModal();
  closeLightControlModal();
  closeProfileModal();
  closeSessionsModal();
}

// Показ компактного inline-подтверждения выхода в шапке
function showLogoutConfirm() {
  const confirmBox = document.getElementById('header-logout-confirm');
  const logoutBtn = document.getElementById('header-btn-logout');
  if (confirmBox) confirmBox.classList.remove('hidden');
  if (logoutBtn) logoutBtn.classList.add('hidden');
}

function hideLogoutConfirm() {
  const confirmBox = document.getElementById('header-logout-confirm');
  const logoutBtn = document.getElementById('header-btn-logout');
  if (confirmBox) confirmBox.classList.add('hidden');
  if (logoutBtn && currentUser && currentUser.role !== 'guest') {
    logoutBtn.classList.remove('hidden');
  }
}

// Обработка подтверждённого выхода из системы
async function performLogout() {
  hideLogoutConfirm();
  try {
    await api.logout();
    currentUser = { authenticated: false, role: 'guest', login: null };
    applyRole('guest');
    closeAllModals();
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
    closeAllModals();
    loadAndRenderUnits();
    showError('Сессия завершена. Войдите снова.', 'Сессия');
  }
}

// Обработка принудительного завершения собственного сеанса из «Активных сессий»
async function handleSelfSessionTerminated() {
  try {
    await api.logout();
  } catch (_) {
    // Игнорируем ошибку, если сервер уже деактивировал сессию
  }
  currentUser = { authenticated: false, role: 'guest', login: null };
  applyRole('guest');
  closeAllModals();
  await loadAndRenderUnits();
  openLoginModal();
  showToast('Текущий сеанс завершён. Войдите снова.');
}

// Загрузка версии сервера для подвала и ссылки на бота
async function loadFooterVersion() {
  const footerEl = document.getElementById('footer-version');

  try {
    const v = await api.getVersion();
    if (v && v.server_version) {
      if (footerEl) {
        footerEl.textContent = `GrowVision v${v.server_version}`;
        footerEl.classList.remove('hidden');
      }
    }
    if (v && v.bot_username) {
      updateBotLinks(v.bot_username);
    }
  } catch (_) {
    if (footerEl) footerEl.classList.add('hidden');
  }
}

// Первоначальная проверка сессии
async function initSession() {
  try {
    const me = await api.getMe();
    if (me && me.authenticated) {
      currentUser = me;
      if (me.bot_username) {
        updateBotLinks(me.bot_username);
      }
    } else {
      currentUser = { authenticated: false, role: 'guest', login: null };
      if (me && me.bot_username) {
        updateBotLinks(me.bot_username);
      }
    }
  } catch (_) {
    currentUser = { authenticated: false, role: 'guest', login: null };
  }
  applyRole(currentUser.role);
}

// Фоновый опрос каждые 5 с (с проверкой document.hidden)
async function pollBackground() {
  if (document.hidden) return;

  // 1. Проверка сессии
  try {
    const me = await api.getMe();
    if (me) {
      if (me.bot_username) {
        updateBotLinks(me.bot_username);
      }
      const wasAuth = currentUser && currentUser.authenticated;
      const wasRole = currentUser && currentUser.role;
      const wasLogin = currentUser && currentUser.login;
      if (
        me.authenticated !== wasAuth ||
        (me.authenticated && (me.role !== wasRole || me.login !== wasLogin))
      ) {
        currentUser = me.authenticated ? me : { authenticated: false, role: 'guest', login: null };
        applyRole(currentUser.role);
      } else if (me.authenticated) {
        currentUser = { ...currentUser, ...me };
        setProfileUser(currentUser);
      }
    }
  } catch (_) {
    // Сетевая ошибка отрабатывается в api.js
  }

  // 2. Список активных установок
  try {
    await loadAndRenderUnits();
  } catch (_) {}
}

function startBackgroundPolling() {
  if (backgroundPollingTimer) clearInterval(backgroundPollingTimer);
  backgroundPollingTimer = setInterval(pollBackground, 5000);
}

// Запуск приложения
async function bootstrap() {
  initDialogs();
  initAuth();
  initProfile();
  initUnits();
  initUnitEditor();
  initGallery();
  initTestSnap();
  initRpiSetup();
  initLightControl();
  setLightChangedHandler(async () => {
    await loadAndRenderUnits({ bustCache: true });
  });
  initCameras();
  setUnitSavedHandler(async () => {
    await loadAndRenderUnits({ bustCache: true });
  });
  setCamerasChangedHandler(async () => {
    await loadAndRenderUnits();
  });
  setLiveProfileSavedHandler(async (scope) => {
    if (scope === 'all_gallery') {
      await loadAndRenderUnits();
    }
  });

  setUnauthorizedHandler(handleSessionExpired);

  setProfileUpdatedHandler((updatedUser) => {
    if (updatedUser && currentUser && currentUser.authenticated) {
      currentUser = { ...currentUser, ...updatedUser };
      updateHeaderUi();
    }
  });
  setSelfSessionTerminatedHandler(handleSelfSessionTerminated);

  setAuthSuccessHandler(async (res) => {
    currentUser = {
      authenticated: true,
      role: res.role,
      login: res.login,
    };
    applyRole(res.role);
    await initSession();
    await loadAndRenderUnits();
  });

  setUnitActionHandlers({
    onOpenGallery: (unit) => openGalleryModal(unit),
    onOpenTestSnap: (unit) => openTestSnapModal(unit),
    onOpenLive: (unit) => {
      if (unit.cameras && unit.cameras.length > 0) {
        openLiveModal(unit.cameras[0]);
      }
    },
    onCreateUnit: () => openUnitCreateModal(),
    onEditUnit: (unit) => openUnitEditModal(unit),
  });

  const logoutBtn = document.getElementById('header-btn-logout');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', showLogoutConfirm);
  }

  const confirmYesBtn = document.getElementById('header-logout-confirm-yes');
  if (confirmYesBtn) {
    confirmYesBtn.addEventListener('click', performLogout);
  }

  const confirmNoBtn = document.getElementById('header-logout-confirm-no');
  if (confirmNoBtn) {
    confirmNoBtn.addEventListener('click', hideLogoutConfirm);
  }

  await initSession();
  await loadAndRenderUnits();
  await loadFooterVersion();

  startBackgroundPolling();
}

window.addEventListener('DOMContentLoaded', bootstrap);
