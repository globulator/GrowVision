// clients/web/js/app.js
// Главный модуль веб-клиента GrowVision SPA: маршрутизация, модалки, тосты и роли.

import { api } from './api.js';
import { UnitsManager } from './units.js';
import { TimelapsePlayer } from './player.js';
import { RelaysManager } from './relays.js';
import { CamerasManager } from './cameras.js';
import { SessionsManager } from './sessions.js';
import { SystemManager } from './system.js';
import { TerminalManager } from './terminal.js';

class App {
  constructor() {
    this.currentUser = { role: 'guest', login: null };
    this.activeTab = 'units';

    this.unitsMgr = null;
    this.player = null;
    this.relaysMgr = null;
    this.camerasMgr = null;
    this.sessionsMgr = null;
    this.systemMgr = null;
    this.terminalMgr = null;

    this.init();
  }

  async init() {
    this.setupGlobalModals();
    this.setupTabNavigation();
    this.setupHeaderActions();

    // Создание менеджеров
    this.unitsMgr = new UnitsManager(document.getElementById('tab-units'), {
      userRole: this.currentUser.role,
      onSelectInstallation: (inst) => this.openPlayerForInstallation(inst),
    });

    this.player = new TimelapsePlayer(document.getElementById('tab-player'), {
      userRole: this.currentUser.role,
      onBack: () => this.switchTab('units'),
    });

    this.relaysMgr = new RelaysManager(document.getElementById('tab-relays'), {
      userRole: this.currentUser.role,
    });

    this.camerasMgr = new CamerasManager(document.getElementById('tab-cameras'), {
      userRole: this.currentUser.role,
    });

    this.sessionsMgr = new SessionsManager(document.getElementById('tab-sessions'), {
      userRole: this.currentUser.role,
    });

    this.systemMgr = new SystemManager(document.getElementById('tab-system'), {
      userRole: this.currentUser.role,
    });

    this.terminalMgr = new TerminalManager(document.getElementById('tab-terminal'), {
      userRole: this.currentUser.role,
    });

    // Проверка текущей сессии
    await this.checkAuth();

    // Загрузка версии в подвал
    this.loadVersion();

    // Открытие первой вкладки
    this.switchTab('units');
  }

  async checkAuth() {
    try {
      const resp = await api.getMe();
      this.currentUser = resp;
    } catch (_) {
      this.currentUser = { role: 'guest', login: null };
    }
    this.updateUserInterface();
  }

  updateUserInterface() {
    const role = this.currentUser.role || 'guest';
    const login = this.currentUser.login;

    // Шапка
    const userBadge = document.getElementById('header-user-badge');
    const userLogin = document.getElementById('header-user-login');
    const btnLogin = document.getElementById('header-btn-login');
    const btnLogout = document.getElementById('header-btn-logout');
    const btnRenameMe = document.getElementById('header-btn-rename-me');

    userBadge.textContent = role === 'developer' ? 'Разработчик' : role === 'biologist' ? 'Биолог' : 'Гость';
    userBadge.className = `badge ${role === 'developer' ? 'badge-primary' : role === 'biologist' ? 'badge-info' : 'badge-secondary'}`;

    if (role === 'guest') {
      userLogin.textContent = '';
      btnLogin.classList.remove('hidden');
      btnLogout.classList.add('hidden');
      btnRenameMe.classList.add('hidden');
    } else {
      userLogin.textContent = login || '';
      btnLogin.classList.add('hidden');
      btnLogout.classList.remove('hidden');
      btnRenameMe.classList.remove('hidden');
    }

    // Вкладки
    const tabRelays = document.getElementById('nav-tab-relays');
    const tabCameras = document.getElementById('nav-tab-cameras');
    const tabSessions = document.getElementById('nav-tab-sessions');
    const tabSystem = document.getElementById('nav-tab-system');
    const tabTerminal = document.getElementById('nav-tab-terminal');

    if (role === 'guest') {
      tabRelays.classList.add('hidden');
      tabCameras.classList.add('hidden');
      tabSessions.classList.add('hidden');
      tabSystem.classList.add('hidden');
      tabTerminal.classList.add('hidden');
    } else if (role === 'biologist') {
      tabRelays.classList.remove('hidden');
      tabCameras.classList.add('hidden');
      tabSessions.classList.add('hidden');
      tabSystem.classList.add('hidden');
      tabTerminal.classList.add('hidden');
    } else if (role === 'developer') {
      tabRelays.classList.remove('hidden');
      tabCameras.classList.remove('hidden');
      tabSessions.classList.remove('hidden');
      tabSystem.classList.remove('hidden');
      tabTerminal.classList.remove('hidden');
    }

    // Обновление менеджеров
    this.unitsMgr?.setRole(role);
    this.player?.setRole(role);
    this.relaysMgr?.setRole(role);
    this.camerasMgr?.setRole(role);
    this.sessionsMgr?.setRole(role);
    this.systemMgr?.setRole(role);
    this.terminalMgr?.setRole(role);

    // Если текущая вкладка стала недоступна — возврат к units
    if (role === 'guest' && !['units', 'player'].includes(this.activeTab)) {
      this.switchTab('units');
    } else if (role === 'biologist' && !['units', 'player', 'relays'].includes(this.activeTab)) {
      this.switchTab('units');
    }
  }

  setupTabNavigation() {
    document.querySelectorAll('.nav-tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        const target = tab.dataset.tab;
        this.switchTab(target);
      });
    });
  }

  switchTab(tabName) {
    this.activeTab = tabName;

    document.querySelectorAll('.nav-tab').forEach((t) => {
      if (t.dataset.tab === tabName) {
        t.classList.add('active');
      } else {
        t.classList.remove('active');
      }
    });

    document.querySelectorAll('.tab-content').forEach((tc) => {
      if (tc.id === `tab-${tabName}`) {
        tc.classList.remove('hidden');
      } else {
        tc.classList.add('hidden');
      }
    });

    // Загрузка данных при активации вкладки
    if (tabName === 'units') {
      this.unitsMgr?.loadUnits();
    } else if (tabName === 'relays') {
      this.relaysMgr?.loadRelays();
    } else if (tabName === 'cameras') {
      this.camerasMgr?.loadData();
    } else if (tabName === 'sessions') {
      this.sessionsMgr?.loadData();
    } else if (tabName === 'system') {
      this.systemMgr?.loadData();
    }
  }

  openPlayerForInstallation(installation) {
    this.switchTab('player');
    this.player.loadInstallation(installation);
  }

  setupHeaderActions() {
    document.getElementById('header-btn-login').addEventListener('click', () => {
      this.showLoginModal();
    });

    document.getElementById('header-btn-logout').addEventListener('click', async () => {
      if (!confirm('Вы действительно хотите выйти из системы?')) return;
      try {
        await api.logout();
        this.showToast('Вы вышли из системы', 'info');
        await this.checkAuth();
      } catch (err) {
        alert('Ошибка при выходе: ' + err.message);
      }
    });

    document.getElementById('header-btn-rename-me').addEventListener('click', async () => {
      const currentName = this.currentUser.login || '';
      const newName = prompt('Введите новое имя и фамилию для вашего профиля:', currentName);
      if (newName === null) return;
      if (!newName.trim()) {
        alert('Имя не может быть пустым.');
        return;
      }
      try {
        await api.updateMe(newName.trim());
        this.showToast('Имя успешно обновлено', 'success');
        await this.checkAuth();
      } catch (err) {
        alert('Ошибка изменения имени: ' + err.message);
      }
    });
  }

  async loadVersion() {
    try {
      const v = await api.getVersion();
      const el = document.getElementById('footer-version');
      if (el && v && v.server_version) {
        el.textContent = `Версия системы: ${v.server_version}`;
      }
    } catch (_) {
      // Скрываем без ошибки согласно WEB_SPEC
      const el = document.getElementById('footer-version');
      if (el) el.textContent = '';
    }
  }

  showLoginModal() {
    const modal = document.getElementById('modal-login');
    const pwdInput = document.getElementById('login-password-input');
    const loginInput = document.getElementById('login-name-input');
    const nameRow = document.getElementById('login-name-row');
    const alertBox = document.getElementById('login-alert-box');

    pwdInput.value = '';
    loginInput.value = '';
    nameRow.classList.add('hidden');
    alertBox.classList.add('hidden');
    modal.classList.remove('hidden');

    pwdInput.focus();
  }

  setupGlobalModals() {
    // 1. Модалка входа
    const modalLogin = document.getElementById('modal-login');
    document.getElementById('login-modal-close').addEventListener('click', () => {
      modalLogin.classList.add('hidden');
    });

    document.getElementById('login-btn-submit').addEventListener('click', async () => {
      const pwd = document.getElementById('login-password-input').value.trim();
      const name = document.getElementById('login-name-input').value.trim();
      const nameRow = document.getElementById('login-name-row');
      const alertBox = document.getElementById('login-alert-box');
      alertBox.classList.add('hidden');

      if (!pwd) {
        alertBox.textContent = 'Пожалуйста, введите пароль или одноразовый код.';
        alertBox.classList.remove('hidden');
        return;
      }

      try {
        const resp = await api.login(pwd, name || null);
        if (resp.status === 'needs_login') {
          // Первый вход: запрашиваем имя и фамилию БЕЗ сжигания кода
          nameRow.classList.remove('hidden');
          document.getElementById('login-name-input').focus();
          alertBox.textContent = 'Для завершения первого входа укажите ваше Имя и Фамилию.';
          alertBox.className = 'alert alert-info';
          alertBox.classList.remove('hidden');
          return;
        }

        modalLogin.classList.add('hidden');
        this.showToast(`Успешный вход! Роль: ${resp.role}`, 'success');
        await this.checkAuth();
      } catch (err) {
        alertBox.textContent = err.message;
        alertBox.className = 'alert alert-error';
        alertBox.classList.remove('hidden');
      }
    });

    // 2. Модалка создания/редактирования установки
    window.showUnitEditModal = (unit, onSaved) => {
      const modal = document.getElementById('modal-unit-edit');
      const title = document.getElementById('unit-edit-modal-title');
      const nameInput = document.getElementById('unit-edit-name');
      const cultureInput = document.getElementById('unit-edit-culture');
      const lightPinInput = document.getElementById('unit-edit-light-pin');
      const flashPinInput = document.getElementById('unit-edit-flash-pin');
      const arucoInput = document.getElementById('unit-edit-aruco');
      const delayInput = document.getElementById('unit-edit-delay');
      const pinsWrap = document.getElementById('unit-edit-pins-wrap');
      const alertBox = document.getElementById('unit-edit-alert');
      const warnBox = document.getElementById('unit-edit-warn');

      alertBox.classList.add('hidden');
      warnBox.classList.add('hidden');

      const isDev = this.currentUser.role === 'developer';
      if (!isDev) {
        pinsWrap.classList.add('hidden');
      } else {
        pinsWrap.classList.remove('hidden');
      }

      if (unit) {
        title.textContent = `Редактирование: ${unit.name}`;
        nameInput.value = unit.name;
        cultureInput.value = unit.culture_name;
        lightPinInput.value = unit.light_gpio_pin;
        flashPinInput.value = unit.flash_gpio_pin;
        arucoInput.value = unit.aruco_size_mm || '';
        delayInput.value = unit.capture_delay_sec !== null && unit.capture_delay_sec !== undefined ? unit.capture_delay_sec : '';
      } else {
        title.textContent = 'Создание новой установки';
        nameInput.value = '';
        cultureInput.value = '';
        lightPinInput.value = '';
        flashPinInput.value = '';
        arucoInput.value = '';
        delayInput.value = '';
      }

      modal.classList.remove('hidden');

      const saveBtn = document.getElementById('unit-edit-btn-save');
      const newSaveBtn = saveBtn.cloneNode(true);
      saveBtn.parentNode.replaceChild(newSaveBtn, saveBtn);

      newSaveBtn.addEventListener('click', async () => {
        alertBox.classList.add('hidden');
        warnBox.classList.add('hidden');

        const name = nameInput.value.trim();
        const culture = cultureInput.value.trim();
        if (!name || !culture) {
          alertBox.textContent = 'Название установки и культура обязательны для заполнения.';
          alertBox.classList.remove('hidden');
          return;
        }

        const payload = { name, culture_name: culture };

        if (isDev) {
          const lPin = parseInt(lightPinInput.value, 10);
          const fPin = parseInt(flashPinInput.value, 10);

          if (isNaN(lPin) || lPin < 0 || lPin > 53) {
            alertBox.textContent = 'Пин света должен быть числом от 0 до 53.';
            alertBox.classList.remove('hidden');
            return;
          }
          if (isNaN(fPin) || fPin < 0 || fPin > 53) {
            alertBox.textContent = 'Пин вспышки должен быть числом от 0 до 53.';
            alertBox.classList.remove('hidden');
            return;
          }
          if (lPin === fPin) {
            alertBox.textContent = 'Пин света и вспышки не могут совпадать внутри одной установки.';
            alertBox.classList.remove('hidden');
            return;
          }

          payload.light_gpio_pin = lPin;
          payload.flash_gpio_pin = fPin;

          const arucoVal = parseFloat(arucoInput.value);
          if (!isNaN(arucoVal) && arucoVal > 0) {
            payload.aruco_size_mm = arucoVal;
          }

          const delayVal = parseFloat(delayInput.value);
          if (!isNaN(delayVal) && delayVal >= 0 && delayVal <= 30) {
            payload.capture_delay_sec = delayVal;
          }
        }

        try {
          let resp;
          if (unit) {
            resp = await api.updateInstallation(unit.id, payload);
          } else {
            resp = await api.createInstallation(payload);
          }

          if (resp.warnings && resp.warnings.length > 0) {
            this.showToast(resp.warnings.join('; '), 'warning');
          } else {
            this.showToast(unit ? 'Установка сохранена' : 'Установка создана', 'success');
          }

          modal.classList.add('hidden');
          onSaved?.();
        } catch (err) {
          alertBox.textContent = err.message;
          alertBox.classList.remove('hidden');
        }
      });
    };

    document.getElementById('unit-edit-modal-close').addEventListener('click', () => {
      document.getElementById('modal-unit-edit').classList.add('hidden');
    });

    // 3. Модалка ручного снимка
    window.showSnapModal = async (unit, userRole) => {
      const modal = document.getElementById('modal-snap');
      const title = document.getElementById('snap-modal-title');
      const loader = document.getElementById('snap-loader');
      const resultBox = document.getElementById('snap-results');
      const debugBox = document.getElementById('snap-debug-box');

      title.textContent = `Ручной снимок: ${unit.name}`;
      loader.classList.remove('hidden');
      resultBox.classList.add('hidden');
      debugBox.classList.add('hidden');
      modal.classList.remove('hidden');

      try {
        const resp = await api.snap(unit.id);
        loader.classList.add('hidden');
        resultBox.classList.remove('hidden');
        resultBox.innerHTML = '';

        if (!resp.captures || resp.captures.length === 0) {
          resultBox.innerHTML = '<div class="text-muted">Снимок выполнен, но кадры не получены.</div>';
        } else {
          resp.captures.forEach((cap) => {
            const wrap = document.createElement('div');
            wrap.className = 'snap-card';
            const isDev = userRole === 'developer';

            wrap.innerHTML = `
              <div class="snap-card-header">
                <strong>Камера #${cap.camera_id}</strong>
                <span class="text-muted">${cap.captured_at ? new Date(cap.captured_at).toLocaleTimeString('ru-RU') : ''}</span>
              </div>
              <div class="snap-card-img-wrap">
                <img src="${cap.raw_url}" alt="Снимок" id="snap-img-${cap.capture_id}" class="snap-img" />
              </div>
              ${
                isDev
                  ? `
                <div class="snap-layer-toggles">
                  <button type="button" class="btn btn-secondary btn-xs active" id="btn-snap-raw-${cap.capture_id}">Оригинал</button>
                  <button type="button" class="btn btn-secondary btn-xs" id="btn-snap-ann-${cap.capture_id}">Разметка</button>
                </div>
              `
                  : ''
              }
              <div class="snap-meta">Площадь растения: <strong>${cap.area_display || 'не определена'}</strong></div>
            `;

            if (isDev) {
              const img = wrap.querySelector(`#snap-img-${cap.capture_id}`);
              const btnRaw = wrap.querySelector(`#btn-snap-raw-${cap.capture_id}`);
              const btnAnn = wrap.querySelector(`#btn-snap-ann-${cap.capture_id}`);

              btnRaw.addEventListener('click', () => {
                btnRaw.classList.add('active');
                btnAnn.classList.remove('active');
                img.src = cap.raw_url;
              });
              btnAnn.addEventListener('click', () => {
                btnAnn.classList.add('active');
                btnRaw.classList.remove('active');
                img.src = cap.annotated_url;
              });
            }

            resultBox.appendChild(wrap);
          });
        }

        if (resp.debug_info?.log_line) {
          debugBox.innerHTML = `<strong>🐞 Лог:</strong> <code>${escapeHtml(resp.debug_info.log_line)}</code>`;
          debugBox.classList.remove('hidden');
        }
      } catch (err) {
        loader.classList.add('hidden');
        resultBox.classList.remove('hidden');
        resultBox.innerHTML = `<div class="alert alert-error">Ошибка съёмки: ${escapeHtml(err.message)}</div>`;
      }
    };

    document.getElementById('snap-modal-close').addEventListener('click', () => {
      document.getElementById('modal-snap').classList.add('hidden');
    });

    // 4. Модалка экспорта видео
    window.showVideoExportModal = (installation, preselectedCameraId) => {
      const modal = document.getElementById('modal-video');
      const title = document.getElementById('video-modal-title');
      const camSelect = document.getElementById('video-cam-select');
      const durationInput = document.getElementById('video-duration-input');
      const stepInput = document.getElementById('video-step-input');
      const alertBox = document.getElementById('video-alert-box');
      const submitBtn = document.getElementById('video-btn-submit');

      title.textContent = `Экспорт видео: ${installation.name}`;
      alertBox.classList.add('hidden');
      durationInput.value = '5.0';
      stepInput.value = '1';

      // Наполнение камер
      camSelect.innerHTML = '<option value="">Авто / Все камеры</option>';
      if (this.player && this.player.availableCameraIds) {
        this.player.availableCameraIds.forEach((cid) => {
          const opt = document.createElement('option');
          opt.value = cid;
          opt.textContent = `Камера ${cid}`;
          if (cid === preselectedCameraId) opt.selected = true;
          camSelect.appendChild(opt);
        });
      }

      modal.classList.remove('hidden');

      const newSubmitBtn = submitBtn.cloneNode(true);
      submitBtn.parentNode.replaceChild(newSubmitBtn, submitBtn);

      newSubmitBtn.addEventListener('click', async () => {
        alertBox.classList.add('hidden');
        newSubmitBtn.disabled = true;
        newSubmitBtn.textContent = 'Сборка видео (ffmpeg)...';

        const camId = camSelect.value ? parseInt(camSelect.value, 10) : null;
        const dur = parseFloat(durationInput.value) || 5.0;
        const step = parseInt(stepInput.value, 10) || 1;

        try {
          const blob = await api.buildVideo(installation.id, {
            cameraId: camId,
            step,
            durationSec: dur,
          });

          // Скачивание файла в браузере
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `timelapse_inst${installation.id}_${Date.now()}.mp4`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(url);

          modal.classList.add('hidden');
          this.showToast('Видео успешно собрано и скачано!', 'success');
        } catch (err) {
          alertBox.textContent = 'Ошибка сборки видео: ' + err.message;
          alertBox.classList.remove('hidden');
        } finally {
          newSubmitBtn.disabled = false;
          newSubmitBtn.textContent = 'Собрать и скачать MP4';
        }
      });
    };

    document.getElementById('video-modal-close').addEventListener('click', () => {
      document.getElementById('modal-video').classList.add('hidden');
    });
  }

  showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('toast-fade');
      setTimeout(() => toast.remove(), 400);
    }, 4000);
  }
}

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

window.addEventListener('DOMContentLoaded', () => {
  window.app = new App();
  window.showToast = (msg, type) => window.app.showToast(msg, type);
});
