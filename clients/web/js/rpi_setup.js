// clients/web/js/rpi_setup.js
// Модуль помощника подключения Raspberry Pi (только для разработчика)

import { api } from './api.js';
import { showErrorDialog, showToast } from './dialogs.js';

let pollTimer = null;
let currentRole = 'guest';

// DOM-элементы
let btnRpiSetup = null;
let rpiStatusDot = null;
let modalRpiSetup = null;
let modalCloseBtn = null;

let rpiLiveDot = null;
let rpiLiveText = null;
let rpiLiveMode = null;
let rpiLivePing = null;

let setupForm = null;
let serverUrlInput = null;
let netbirdKeyInput = null;
let toggleNetbirdKeyBtn = null;
let generateBtn = null;

let resultBlock = null;
let commandInput = null;
let copyBtn = null;
let copyText = null;

export function initRpiSetup() {
  btnRpiSetup = document.getElementById('btn-rpi-setup');
  rpiStatusDot = document.getElementById('rpi-status-dot');
  modalRpiSetup = document.getElementById('modal-rpi-setup');
  modalCloseBtn = document.getElementById('modal-rpi-setup-close');

  rpiLiveDot = document.getElementById('rpi-live-dot');
  rpiLiveText = document.getElementById('rpi-live-status-text');
  rpiLiveMode = document.getElementById('rpi-live-mode');
  rpiLivePing = document.getElementById('rpi-live-last-ping');

  setupForm = document.getElementById('rpi-setup-form');
  serverUrlInput = document.getElementById('rpi-server-url');
  netbirdKeyInput = document.getElementById('rpi-netbird-key');
  toggleNetbirdKeyBtn = document.getElementById('rpi-toggle-netbird-key');
  generateBtn = document.getElementById('rpi-btn-generate');

  resultBlock = document.getElementById('rpi-result-block');
  commandInput = document.getElementById('rpi-command-input');
  copyBtn = document.getElementById('rpi-copy-btn');
  copyText = document.getElementById('rpi-copy-text');

  if (btnRpiSetup) {
    btnRpiSetup.addEventListener('click', openRpiModal);
  }

  if (modalCloseBtn) {
    modalCloseBtn.addEventListener('click', closeRpiModal);
  }

  if (modalRpiSetup) {
    modalRpiSetup.addEventListener('click', (e) => {
      if (e.target === modalRpiSetup) {
        closeRpiModal();
      }
    });
  }

  if (toggleNetbirdKeyBtn && netbirdKeyInput) {
    toggleNetbirdKeyBtn.addEventListener('click', toggleNetbirdKeyVisibility);
  }

  if (setupForm) {
    setupForm.addEventListener('submit', handleGenerateCommand);
  }

  if (copyBtn) {
    copyBtn.addEventListener('click', handleCopyCommand);
  }

  // Закрытие по клавише Esc
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalRpiSetup && !modalRpiSetup.classList.contains('hidden')) {
      closeRpiModal();
    }
  });
}

export function updateRpiRole(role) {
  currentRole = role;
  const devSlot = document.getElementById('header-dev-slot');
  if (devSlot) {
    if (currentRole === 'developer') {
      devSlot.classList.remove('hidden');
      updateStatusSilent();
    } else {
      devSlot.classList.add('hidden');
      closeRpiModal();
    }
  }
}

async function updateStatusSilent() {
  if (currentRole !== 'developer') return;
  try {
    const statusData = await api.getSystemStatus();
    applyStatusData(statusData);
  } catch (_) {
    // В тихом режиме не показываем ошибку
  }
}

function applyStatusData(statusData) {
  const isOnline = statusData && statusData.rpi_connection === 'online';

  // Точка в шапке
  if (rpiStatusDot) {
    rpiStatusDot.className = `status-dot ${isOnline ? 'online' : 'offline'}`;
    rpiStatusDot.title = isOnline ? 'Edge-агент онлайн' : 'Edge-агент офлайн';
  }

  // Индикатор в модалке
  if (rpiLiveDot) {
    rpiLiveDot.className = `status-dot ${isOnline ? 'online' : 'offline'}`;
  }
  if (rpiLiveText) {
    rpiLiveText.textContent = isOnline ? 'Онлайн' : 'Офлайн';
    rpiLiveText.style.color = isOnline ? '#22c55e' : '#94a3b8';
  }

  // Режим
  if (rpiLiveMode) {
    const mode = (statusData && statusData.rpi_telemetry && statusData.rpi_telemetry.hardware_mode) || 'rpi';
    rpiLiveMode.textContent = `Режим: ${mode}`;
  }

  // Время отклика
  if (rpiLivePing) {
    if (statusData && statusData.rpi_last_heartbeat_at) {
      try {
        const d = new Date(statusData.rpi_last_heartbeat_at);
        rpiLivePing.textContent = d.toLocaleString('ru-RU');
      } catch (_) {
        rpiLivePing.textContent = statusData.rpi_last_heartbeat_at;
      }
    } else {
      rpiLivePing.textContent = 'Нет данных';
    }
  }
}

async function openRpiModal() {
  if (currentRole !== 'developer') return;
  if (!modalRpiSetup) return;

  modalRpiSetup.classList.remove('hidden');
  document.body.classList.add('modal-open');

  // Предзаполнение адреса сервера, если пусто
  if (serverUrlInput && !serverUrlInput.value.trim()) {
    try {
      const defaults = await api.getEdgeSetupDefaults();
      if (defaults && defaults.default_server_url) {
        serverUrlInput.value = defaults.default_server_url;
      } else {
        serverUrlInput.value = window.location.origin;
      }
    } catch (_) {
      serverUrlInput.value = window.location.origin;
    }
  }

  // Обновление статуса сразу и запуск таймера автообновления раз в 3 сек
  await updateStatusSilent();
  clearInterval(pollTimer);
  pollTimer = setInterval(updateStatusSilent, 3000);
}

function closeRpiModal() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (modalRpiSetup) {
    modalRpiSetup.classList.add('hidden');
  }
  document.body.classList.remove('modal-open');
}

function toggleNetbirdKeyVisibility() {
  if (!netbirdKeyInput) return;
  const isPass = netbirdKeyInput.type === 'password';
  netbirdKeyInput.type = isPass ? 'text' : 'password';

  if (toggleNetbirdKeyBtn) {
    toggleNetbirdKeyBtn.innerHTML = isPass
      ? `<svg class="icon-svg icon-eye-off" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
           <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
           <line x1="1" y1="1" x2="23" y2="23"/>
         </svg>`
      : `<svg class="icon-svg icon-eye" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
           <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
           <circle cx="12" cy="12" r="3"/>
         </svg>`;
  }
}

async function handleGenerateCommand(e) {
  e.preventDefault();
  if (!serverUrlInput) return;

  const serverUrl = serverUrlInput.value.trim();
  if (!serverUrl) {
    showErrorDialog('Пожалуйста, укажите адрес сервера');
    return;
  }

  const netbirdKey = netbirdKeyInput ? netbirdKeyInput.value.trim() : '';
  const hwModeRadio = document.querySelector('input[name="rpi-hw-mode"]:checked');
  const hwMode = hwModeRadio ? hwModeRadio.value : 'rpi';

  if (generateBtn) {
    generateBtn.disabled = true;
    generateBtn.classList.add('loading');
  }

  try {
    const res = await api.createEdgeSetupToken({
      server_url: serverUrl,
      netbird_setup_key: netbirdKey || null,
      hardware_mode: hwMode,
    });

    const cleanServerUrl = serverUrl.replace(/\/+$/, '');
    const scriptUrl = `${cleanServerUrl}${res.script_path}`;
    const curlCommand = `curl --noproxy '*' -fsSL ${scriptUrl} | bash`;

    if (commandInput) {
      commandInput.value = curlCommand;
    }
    if (resultBlock) {
      resultBlock.classList.remove('hidden');
      resultBlock.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    showToast('Команда настройки Edge успешно сформирована');
  } catch (err) {
    showErrorDialog(err.message || 'Ошибка генерации токена установки');
  } finally {
    if (generateBtn) {
      generateBtn.disabled = false;
      generateBtn.classList.remove('loading');
    }
  }
}

async function handleCopyCommand() {
  if (!commandInput || !commandInput.value) return;
  const text = commandInput.value;

  let copied = false;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch (_) {
      // fallback
    }
  }

  if (!copied) {
    try {
      commandInput.select();
      commandInput.setSelectionRange(0, 99999);
      document.execCommand('copy');
      copied = true;
    } catch (_) {
      copied = false;
    }
  }

  if (copied) {
    if (copyText) {
      const orig = copyText.textContent;
      copyText.textContent = 'Скопировано!';
      setTimeout(() => {
        if (copyText) copyText.textContent = orig;
      }, 2000);
    }
    showToast('Команда скопирована в буфер обмена');
  } else {
    showErrorDialog('Не удалось скопировать команду в буфер обмена');
  }
}
