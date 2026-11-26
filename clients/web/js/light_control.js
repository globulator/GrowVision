// clients/web/js/light_control.js
// Панель глобального управления светом (3-позиционный режим), вспышкой, тестом реле и задержкой прогрева

import { api } from './api.js';
import { Modal, showToast } from './dialogs.js';

let currentRole = 'guest';
let lightModalCtrl = null;

function getLightModalCtrl() {
  if (!lightModalCtrl) {
    lightModalCtrl = new Modal('modal-light-control', {
      closeBtn: 'modal-light-control-close',
      onClose: () => {
        showLightError('');
      },
    });
  }
  return lightModalCtrl;
}
let activeInstallationsCount = 0;
let lastMainMode = 'default';
let lastFlashMode = 'default';
let lastDayNightEnabled = false;
let lastDayStart = '08:00';
let lastDayEnd = '22:00';
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
  const mainLockReason = document.getElementById('light-main-lock-reason');
  const flashLabel = document.getElementById('light-flash-state-label');
  const dnToggle = document.getElementById('light-day-night-toggle');
  const dnStartInput = document.getElementById('light-day-night-start');
  const dnEndInput = document.getElementById('light-day-night-end');
  const dnPhaseEl = document.getElementById('light-day-night-phase');
  const dnSubEl = document.getElementById('light-day-night-sub');
  const delayInput = document.getElementById('light-capture-delay-input');
  const testBtn = document.getElementById('btn-light-relay-test');
  const listEl = document.getElementById('light-control-installations');

  const insts = Array.isArray(relaysData && relaysData.installations)
    ? relaysData.installations
    : [];
  activeInstallationsCount = insts.length;

  const mainMode = normalizeLightMode(relaysData && relaysData.global_light_mode);
  const flashMode = normalizeLightMode(relaysData && relaysData.global_flash_mode);
  const globalDnEnabled = Boolean(relaysData && relaysData.global_day_night_enabled);
  const globalDnStart = (relaysData && relaysData.global_day_start) || '08:00';
  const globalDnEnd = (relaysData && relaysData.global_day_end) || '22:00';
  const globalDnPhase =
    relaysData && relaysData.global_day_night_phase === 'night' ? 'night' : 'day';
  const mainLocked =
    Boolean(relaysData && relaysData.global_light_locked) || globalDnEnabled;

  lastMainMode = mainMode;
  lastFlashMode = flashMode;
  lastDayNightEnabled = globalDnEnabled;
  lastDayStart = globalDnStart;
  lastDayEnd = globalDnEnd;

  setSegmentedControlState(
    'light-mode-main',
    mainMode,
    activeInstallationsCount === 0 || mainLocked
  );
  setSegmentedControlState('light-mode-flash', flashMode, activeInstallationsCount === 0);

  if (mainLockReason) {
    mainLockReason.textContent =
      (relaysData && relaysData.global_light_lock_reason) || 'Цикл дня/ночи';
    mainLockReason.classList.toggle('hidden', !mainLocked);
  }

  if (dnToggle) {
    dnToggle.checked = globalDnEnabled;
    dnToggle.disabled = false;
  }
  if (dnStartInput) {
    dnStartInput.value = globalDnStart;
    dnStartInput.disabled = !globalDnEnabled;
  }
  if (dnEndInput) {
    dnEndInput.value = globalDnEnd;
    dnEndInput.disabled = !globalDnEnabled;
  }
  if (dnPhaseEl) {
    dnPhaseEl.textContent = globalDnPhase === 'day' ? 'День' : 'Ночь';
    dnPhaseEl.classList.toggle('is-night', globalDnPhase === 'night');
    dnPhaseEl.classList.toggle('is-off', !globalDnEnabled);
  }
  if (dnSubEl) {
    dnSubEl.textContent = globalDnEnabled
      ? `Активен: ${globalDnStart}–${globalDnEnd}`
      : `Выключен (${globalDnStart}–${globalDnEnd})`;
  }

  if (testBtn) {
    testBtn.disabled = activeInstallationsCount === 0;
  }

  if (mainLabel) {
    mainLabel.textContent = mainLocked
      ? `Цикл дня/ночи (${globalDnPhase === 'day' ? 'День' : 'Ночь'})`
      : formatGlobalModeSub(mainMode, false);
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
  const modalCtrl = getLightModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;

  showLightError('');
  modalCtrl.open();

  await refreshRelaysState();
}

export function closeLightControlModal() {
  getLightModalCtrl().close();
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
    const isLocked = !isFlash && lastDayNightEnabled;
    setSegmentedControlState(
      containerId,
      finalMode,
      activeInstallationsCount === 0 || isLocked
    );
  }
}

async function handleGlobalDayNightChange({ toggleChanged = false } = {}) {
  const dnToggle = document.getElementById('light-day-night-toggle');
  const dnStartInput = document.getElementById('light-day-night-start');
  const dnEndInput = document.getElementById('light-day-night-end');
  if (!dnToggle || !dnStartInput || !dnEndInput) return;

  const nextEnabled = Boolean(dnToggle.checked);
  const nextStart = String(dnStartInput.value || lastDayStart).trim();
  const nextEnd = String(dnEndInput.value || lastDayEnd).trim();

  if (!/^\d{2}:\d{2}$/.test(nextStart) || !/^\d{2}:\d{2}$/.test(nextEnd)) {
    showLightError('Укажите корректное время начала и окончания (ЧЧ:ММ)');
    dnStartInput.value = lastDayStart;
    dnEndInput.value = lastDayEnd;
    if (toggleChanged) dnToggle.checked = lastDayNightEnabled;
    return;
  }
  if (nextStart === nextEnd) {
    showLightError('Время начала и окончания светового дня не могут совпадать');
    dnStartInput.value = lastDayStart;
    dnEndInput.value = lastDayEnd;
    if (toggleChanged) dnToggle.checked = lastDayNightEnabled;
    return;
  }

  showLightError('');
  dnToggle.disabled = true;
  dnStartInput.disabled = true;
  dnEndInput.disabled = true;

  try {
    await api.controlDayNight({
      target: 'all',
      enabled: nextEnabled,
      day_start: nextStart,
      day_end: nextEnd,
    });
    showToast(
      nextEnabled
        ? `Световой день (все установки): включён (${nextStart}–${nextEnd})`
        : 'Световой день (все установки): выключен'
    );
    await refreshRelaysState();
    if (typeof onLightChangedCallback === 'function') {
      await onLightChangedCallback();
    }
  } catch (err) {
    dnToggle.checked = lastDayNightEnabled;
    dnStartInput.value = lastDayStart;
    dnEndInput.value = lastDayEnd;
    showLightError(err.message || 'Не удалось обновить цикл дня и ночи');
  } finally {
    dnToggle.disabled = false;
    dnStartInput.disabled = !Boolean(dnToggle.checked);
    dnEndInput.disabled = !Boolean(dnToggle.checked);
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
  const mainControl = document.getElementById('light-mode-main');
  const flashControl = document.getElementById('light-mode-flash');
  const testBtn = document.getElementById('btn-light-relay-test');
  const delayInput = document.getElementById('light-capture-delay-input');
  const dnToggle = document.getElementById('light-day-night-toggle');
  const dnStartInput = document.getElementById('light-day-night-start');
  const dnEndInput = document.getElementById('light-day-night-end');

  getLightModalCtrl().bind();
  if (btnOpen) btnOpen.addEventListener('click', openLightControlModal);

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
  if (dnToggle) {
    dnToggle.addEventListener('change', () =>
      handleGlobalDayNightChange({ toggleChanged: true })
    );
  }
  [dnStartInput, dnEndInput].forEach((inputEl) => {
    if (inputEl) {
      inputEl.addEventListener('change', () => handleGlobalDayNightChange());
    }
  });
  if (delayInput) {
    delayInput.addEventListener('change', (e) => applyDelayChange(e.target.value));
  }

  document.querySelectorAll('.light-delay-preset').forEach((presetBtn) => {
    presetBtn.addEventListener('click', () => {
      applyDelayChange(presetBtn.dataset.delay);
    });
  });
}
