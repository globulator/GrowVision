// clients/web/js/light_control.js
// Панель глобального управления светом (3-позиционный режим), вспышкой, тестом реле и задержкой прогрева

import { api } from './api.js';
import { showToast } from './dialogs.js';

let currentRole = 'guest';
let activeInstallationsCount = 0;
let lastMainMode = 'default';
let lastFlashMode = 'default';
let onLightChangedCallback = null;

export function setLightChangedHandler(fn) {
  onLightChangedCallback = fn;
}

export function updateLightRole(role) {
  currentRole = role || 'guest';
  const btn = document.getElementById('btn-light-menu');
  if (btn) {
    btn.classList.toggle('hidden', currentRole !== 'developer');
  }
  if (currentRole !== 'developer') {
    closeLightControlModal();
  }
}

function normalizeLightMode(rawMode) {
  const norm = String(rawMode || 'default').trim().toLowerCase();
  return norm === 'on' || norm === 'off' ? norm : 'default';
}

function showLightError(msg) {
  const errEl = document.getElementById('light-control-error');
  if (!errEl) return;
  if (msg) {
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  } else {
    errEl.textContent = '';
    errEl.classList.add('hidden');
  }
}

function updatePresetHighlight(delaySec) {
  const presets = document.querySelectorAll('.light-delay-preset');
  presets.forEach((btn) => {
    const val = Number(btn.dataset.delay);
    btn.classList.toggle('active', Number.isFinite(val) && Math.abs(val - Number(delaySec)) < 0.01);
  });
}

function setSegmentedControlState(containerId, activeMode, disabled) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.querySelectorAll('.seg-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === activeMode);
    btn.disabled = Boolean(disabled);
  });
}

function formatGlobalModeSub(mode, isFlash) {
  if (activeInstallationsCount === 0) {
    return 'Нет активных установок';
  }
  if (mode === 'on') {
    return isFlash ? 'Принудительно включена (все установки)' : 'Принудительно включён (все установки)';
  }
  if (mode === 'off') {
    return isFlash ? 'Принудительно выключена (все установки)' : 'Принудительно выключен (все установки)';
  }
  return 'По индивидуальным настройкам установок';
}

function renderRelaysState(relaysData) {
  const summaryEl = document.getElementById('light-control-summary');
  const mainLabel = document.getElementById('light-main-state-label');
  const flashLabel = document.getElementById('light-flash-state-label');
  const delayInput = document.getElementById('light-capture-delay-input');
  const testBtn = document.getElementById('btn-light-relay-test');
  const listEl = document.getElementById('light-control-installations');

  const insts = Array.isArray(relaysData && relaysData.installations)
    ? relaysData.installations
    : [];
  activeInstallationsCount = insts.length;

  const mainMode = normalizeLightMode(relaysData && relaysData.global_light_mode);
  const flashMode = normalizeLightMode(relaysData && relaysData.global_flash_mode);

  lastMainMode = mainMode;
  lastFlashMode = flashMode;

  setSegmentedControlState('light-mode-main', mainMode, activeInstallationsCount === 0);
  setSegmentedControlState('light-mode-flash', flashMode, activeInstallationsCount === 0);

  if (testBtn) {
    testBtn.disabled = activeInstallationsCount === 0;
  }

  if (mainLabel) {
    mainLabel.textContent = formatGlobalModeSub(mainMode, false);
  }
  if (flashLabel) {
    flashLabel.textContent = formatGlobalModeSub(flashMode, true);
  }

  if (relaysData && relaysData.capture_delay_sec !== undefined && delayInput) {
    const delayVal = Number(relaysData.capture_delay_sec);
    if (Number.isFinite(delayVal)) {
      delayInput.value = String(delayVal);
      updatePresetHighlight(delayVal);
    }
  }

  if (summaryEl) {
    summaryEl.textContent =
      activeInstallationsCount === 0
        ? 'Активных установок нет. Добавьте установку для управления реле.'
        : `Активных установок: ${activeInstallationsCount}. Изменения применяются сразу.`;
  }

  if (listEl) {
    listEl.innerHTML = '';
    insts.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'light-inst-chip';
      const lText = item.light_state ? 'ВКЛ' : 'ВЫКЛ';
      const fText =
        item.flash_gpio_pin === item.light_gpio_pin
          ? 'общий пин'
          : item.flash_state
            ? 'ВКЛ'
            : 'ВЫКЛ';
      row.innerHTML = `
        <span class="light-inst-name">${item.name}</span>
        <span class="light-inst-pins">Свет GPIO ${item.light_gpio_pin}: <strong>${lText}</strong> · Вспышка GPIO ${item.flash_gpio_pin}: <strong>${fText}</strong></span>
      `;
      listEl.appendChild(row);
    });
  }
}

async function refreshRelaysState() {
  try {
    const [relaysRes, settingsRes] = await Promise.allSettled([
      api.getRelays(),
      api.getSystemSettings(),
    ]);
    const data = relaysRes.status === 'fulfilled' && relaysRes.value ? relaysRes.value : {};
    if (
      data.capture_delay_sec === undefined &&
      settingsRes.status === 'fulfilled' &&
      settingsRes.value
    ) {
      data.capture_delay_sec = settingsRes.value.capture_delay_sec;
    }
    renderRelaysState(data);
  } catch (err) {
    showLightError(err.message || 'Не удалось загрузить состояние реле');
  }
}

export async function openLightControlModal() {
  if (currentRole !== 'developer') return;
  const modal = document.getElementById('modal-light-control');
  if (!modal) return;

  showLightError('');
  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  await refreshRelaysState();
}

export function closeLightControlModal() {
  const modal = document.getElementById('modal-light-control');
  if (modal) {
    modal.classList.add('hidden');
  }
  document.body.classList.remove('modal-open');
  showLightError('');
}

async function handleGlobalModeSelect(modeRaw, isFlash) {
  if (activeInstallationsCount === 0) return;
  const nextMode = normalizeLightMode(modeRaw);
  const containerId = isFlash ? 'light-mode-flash' : 'light-mode-main';
  const prevMode = isFlash ? lastFlashMode : lastMainMode;

  showLightError('');
  setSegmentedControlState(containerId, nextMode, true);

  try {
    const res = isFlash
      ? await api.controlLightFlash('all', nextMode)
      : await api.controlLightMain('all', nextMode);
    if (res && res.status === 'error') {
      throw new Error(res.result_message || 'Ошибка переключения режима');
    }
    const modeLabel =
      nextMode === 'on' ? 'Вкл' : nextMode === 'off' ? 'Выкл' : 'По умолчанию';
    showToast(
      isFlash
        ? `Вспышка (все установки): ${modeLabel}`
        : `Основной свет (все установки): ${modeLabel}`
    );
    await refreshRelaysState();
    if (typeof onLightChangedCallback === 'function') {
      await onLightChangedCallback();
    }
  } catch (err) {
    setSegmentedControlState(containerId, prevMode, activeInstallationsCount === 0);
    showLightError(
      err.message ||
        (isFlash ? 'Не удалось переключить вспышку' : 'Не удалось переключить основной свет')
    );
  } finally {
    const finalMode = isFlash ? lastFlashMode : lastMainMode;
    setSegmentedControlState(containerId, finalMode, activeInstallationsCount === 0);
  }
}

async function handleRelayTestClick() {
  const btn = document.getElementById('btn-light-relay-test');
  if (!btn || btn.disabled) return;
  showLightError('');
  btn.disabled = true;
  const labelSpan = btn.querySelector('span');
  const origLabel = labelSpan ? labelSpan.textContent : 'Тестирование';
  if (labelSpan) labelSpan.textContent = 'Тестирование реле...';

  try {
    const res = await api.controlRelayTest('all');
    if (res && res.status === 'error') {
      throw new Error(res.result_message || 'Ошибка теста реле');
    }
    showToast((res && res.result_message) || 'Тест реле выполнен, состояния возвращены');
    await refreshRelaysState();
  } catch (err) {
    showLightError(err.message || 'Не удалось выполнить тест реле');
  } finally {
    if (labelSpan) labelSpan.textContent = origLabel;
    btn.disabled = activeInstallationsCount === 0;
  }
}

async function applyDelayChange(rawVal) {
  const num = Number(String(rawVal).replace(',', '.'));
  if (!Number.isFinite(num) || num < 0 || num > 30) {
    showLightError('Задержка прогрева должна быть от 0 до 30 с');
    return;
  }
  const rounded = Math.round(num * 100) / 100;
  const delayInput = document.getElementById('light-capture-delay-input');
  if (delayInput) delayInput.value = String(rounded);
  updatePresetHighlight(rounded);
  showLightError('');

  try {
    await api.patchSystemSettings({ capture_delay_sec: rounded });
    showToast(`Задержка прогрева: ${rounded} с`);
  } catch (err) {
    showLightError(err.message || 'Не удалось обновить задержку прогрева');
  }
}

export function initLightControl() {
  const btnOpen = document.getElementById('btn-light-menu');
  const modal = document.getElementById('modal-light-control');
  const closeBtn = document.getElementById('modal-light-control-close');
  const mainControl = document.getElementById('light-mode-main');
  const flashControl = document.getElementById('light-mode-flash');
  const testBtn = document.getElementById('btn-light-relay-test');
  const delayInput = document.getElementById('light-capture-delay-input');

  if (btnOpen) btnOpen.addEventListener('click', openLightControlModal);
  if (closeBtn) closeBtn.addEventListener('click', closeLightControlModal);
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeLightControlModal();
    });
  }

  if (mainControl) {
    mainControl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        handleGlobalModeSelect(btn.dataset.mode, false);
      });
    });
  }

  if (flashControl) {
    flashControl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        handleGlobalModeSelect(btn.dataset.mode, true);
      });
    });
  }

  if (testBtn) testBtn.addEventListener('click', handleRelayTestClick);
  if (delayInput) {
    delayInput.addEventListener('change', (e) => applyDelayChange(e.target.value));
  }

  document.querySelectorAll('.light-delay-preset').forEach((presetBtn) => {
    presetBtn.addEventListener('click', () => {
      applyDelayChange(presetBtn.dataset.delay);
    });
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
      e.preventDefault();
      closeLightControlModal();
    }
  });
}
