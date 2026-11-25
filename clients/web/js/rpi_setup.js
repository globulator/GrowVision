// clients/web/js/rpi_setup.js
// Модуль помощника подключения Raspberry Pi (только для разработчика)

import { api } from './api.js';
import { showError, showToast } from './dialogs.js';

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

let rpiTelemBoard = null;
let rpiTelemVersions = null;
let rpiTelemModel = null;
let rpiTelemCpu = null;
let rpiTelemRam = null;
let rpiTelemDisk = null;
let rpiTelemUptime = null;
let rpiTelemQueue = null;
let rpiTelemCameras = null;
let rpiTelemCaptureSettings = null;

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

  rpiTelemBoard = document.getElementById('rpi-telem-board');
  rpiTelemVersions = document.getElementById('rpi-telem-versions');
  rpiTelemModel = document.getElementById('rpi-telem-model');
  rpiTelemCpu = document.getElementById('rpi-telem-cpu');
  rpiTelemRam = document.getElementById('rpi-telem-ram');
  rpiTelemDisk = document.getElementById('rpi-telem-disk');
  rpiTelemUptime = document.getElementById('rpi-telem-uptime');
  rpiTelemQueue = document.getElementById('rpi-telem-queue');
  rpiTelemCameras = document.getElementById('rpi-telem-cameras');
  rpiTelemCaptureSettings = document.getElementById('rpi-telem-capture-settings');

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

export function formatUptime(uptimeSec) {
  if (uptimeSec === null || uptimeSec === undefined) return '—';
  const total = Number(uptimeSec);
  if (!Number.isFinite(total) || total < 0) return '—';
  const sec = Math.floor(total);
  const days = Math.floor(sec / 86400);
  const hours = Math.floor((sec % 86400) / 3600);
  const mins = Math.floor((sec % 3600) / 60);
  const secs = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  if (days > 0) return `${days}д ${pad(hours)}ч ${pad(mins)}м`;
  if (hours > 0) return `${hours}ч ${pad(mins)}м`;
  if (mins > 0) return `${mins}м ${pad(secs)}с`;
  return `${secs}с`;
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

  // Режим (read-only бейдж)
  if (rpiLiveMode) {
    if (!isOnline) {
      rpiLiveMode.textContent = 'Режим: Не подключено';
    } else {
      const hwMode =
        (statusData &&
          statusData.rpi_telemetry &&
          statusData.rpi_telemetry.hardware_mode) ||
        (statusData && statusData.is_real_rpi ? 'rpi' : 'mock');
      if (hwMode === 'mock') {
        rpiLiveMode.textContent = 'Режим: Эмуляция (mock)';
      } else {
        rpiLiveMode.textContent = 'Режим: Реальная плата (RPi)';
      }
    }
  }

  // Метрики телеметрии платы
  const telem = statusData ? statusData.rpi_telemetry : null;
  const hw = telem && telem.hardware ? telem.hardware : null;
  const sw = telem && telem.software ? telem.software : null;
  const serverVer = (statusData && statusData.server_version) || '—';
  const edgeVer = (sw && sw.edge_version) || '—';
  const modelName = (sw && sw.model_name) || '—';
  const boardModel = (hw && hw.board_model) || (telem && telem.board_model) || '';

  if (rpiTelemBoard) {
    if (isOnline && boardModel) {
      rpiTelemBoard.textContent = boardModel;
      rpiTelemBoard.title = boardModel;
    } else {
      rpiTelemBoard.textContent = isOnline ? 'Raspberry Pi' : 'Офлайн';
    }
  }

  if (rpiTelemVersions) {
    rpiTelemVersions.textContent = `Агент: v${edgeVer} / Сервер: v${serverVer}`;
    rpiTelemVersions.title = `Версия Edge: ${edgeVer}, Версия сервера: ${serverVer}`;
  }

  if (rpiTelemModel) {
    rpiTelemModel.textContent = isOnline ? modelName : '—';
    rpiTelemModel.title = isOnline ? modelName : '';
  }

  if (rpiTelemCpu) {
    if (
      isOnline &&
      hw &&
      (typeof hw.cpu_usage_percent === 'number' || typeof hw.cpu_temp_c === 'number')
    ) {
      const cpuStr =
        typeof hw.cpu_usage_percent === 'number' ? `${hw.cpu_usage_percent}%` : '—';
      const tempStr =
        typeof hw.cpu_temp_c === 'number' ? ` / ${hw.cpu_temp_c}°C` : '';
      rpiTelemCpu.textContent = `${cpuStr}${tempStr}`;
    } else {
      rpiTelemCpu.textContent = '—';
    }
  }

  if (rpiTelemRam) {
    if (
      isOnline &&
      hw &&
      typeof hw.ram_used_mb === 'number' &&
      typeof hw.ram_total_mb === 'number' &&
      hw.ram_total_mb > 0
    ) {
      const pct = Math.round((hw.ram_used_mb / hw.ram_total_mb) * 100);
      rpiTelemRam.textContent = `${hw.ram_used_mb} / ${hw.ram_total_mb} МБ (${pct}%)`;
    } else {
      rpiTelemRam.textContent = '—';
    }
  }

  if (rpiTelemDisk) {
    if (isOnline && hw && typeof hw.disk_free_gb === 'number') {
      rpiTelemDisk.textContent = `${hw.disk_free_gb} ГБ свободно`;
    } else {
      rpiTelemDisk.textContent = '—';
    }
  }

  if (rpiTelemUptime) {
    const uptimeRaw =
      hw && hw.uptime_sec !== undefined && hw.uptime_sec !== null
        ? hw.uptime_sec
        : telem && telem.uptime_sec;
    rpiTelemUptime.textContent = isOnline ? formatUptime(uptimeRaw) : '—';
  }

  if (rpiTelemQueue) {
    if (isOnline && telem && typeof telem.offline_queue_count === 'number') {
      const q = telem.offline_queue_count;
      rpiTelemQueue.textContent = `${q} кадров`;
      rpiTelemQueue.style.color = q > 0 ? '#f59e0b' : 'var(--text-main)';
    } else {
      rpiTelemQueue.textContent = '0 кадров';
      rpiTelemQueue.style.color = 'var(--text-main)';
    }
  }

  if (rpiTelemCameras) {
    const cams = statusData && statusData.cameras_summary;
    if (cams && typeof cams.total === 'number') {
      rpiTelemCameras.textContent = `${cams.total} / ${cams.online ?? 0} / ${cams.bound ?? 0}`;
      rpiTelemCameras.title = `Всего: ${cams.total}, онлайн: ${cams.online ?? 0}, офлайн: ${cams.offline ?? 0}, привязано: ${cams.bound ?? 0}, свободно: ${cams.unbound ?? 0}`;
    } else {
      rpiTelemCameras.textContent = '—';
      rpiTelemCameras.title = '';
    }
  }

  if (rpiTelemCaptureSettings) {
    const cfg = statusData && statusData.settings;
    if (cfg) {
      const interval = cfg.capture_interval_hours ?? 1;
      const delay = cfg.capture_delay_sec ?? 1.5;
      const dbg = cfg.debug_mode ? 'debug вкл' : 'debug выкл';
      const summary = `${interval} ч · задержка ${delay} с · ${dbg}`;
      rpiTelemCaptureSettings.textContent = summary;
      rpiTelemCaptureSettings.title = summary;
    } else {
      rpiTelemCaptureSettings.textContent = '—';
      rpiTelemCaptureSettings.title = '';
    }
  }

  // Время отклика
  if (rpiLivePing) {
    if (isOnline && statusData && statusData.rpi_last_heartbeat_at) {
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

  const errEl = document.getElementById('rpi-setup-error');
  if (errEl) {
    errEl.textContent = '';
    errEl.classList.add('hidden');
  }

  const serverUrl = serverUrlInput.value.trim();
  if (!serverUrl) {
    if (errEl) {
      errEl.textContent = 'Пожалуйста, укажите адрес сервера';
      errEl.classList.remove('hidden');
    }
    return;
  }

  const netbirdKey = netbirdKeyInput ? netbirdKeyInput.value.trim() : '';

  if (generateBtn) {
    generateBtn.disabled = true;
    generateBtn.classList.add('loading');
  }

  try {
    const res = await api.createEdgeSetupToken({
      server_url: serverUrl,
      netbird_setup_key: netbirdKey || null,
      hardware_mode: 'rpi',
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
    if (errEl) {
      errEl.textContent = err.message || 'Ошибка генерации токена установки';
      errEl.classList.remove('hidden');
    }
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
    showToast('Не удалось скопировать команду в буфер обмена');
  }
}
