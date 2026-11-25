// clients/web/js/light_control.js
// Панель глобального управления светом, вспышкой, тестом реле и задержкой прогрева (для Developer)

import { api } from './api.js';
import { showToast } from './dialogs.js';

let currentRole = 'guest';
let activeInstallationsCount = 0;
let lastMainState = true;
let lastFlashState = false;

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

function renderRelaysState(relaysData) {
  const summaryEl = document.getElementById('light-control-summary');
  const mainToggle = document.getElementById('light-toggle-main');
  const flashToggle = document.getElementById('light-toggle-flash');
  const mainLabel = document.getElementById('light-main-state-label');
  const flashLabel = document.getElementById('light-flash-state-label');
  const delayInput = document.getElementById('light-capture-delay-input');
  const testBtn = document.getElementById('btn-light-relay-test');
  const listEl = document.getElementById('light-control-installations');

  const insts = Array.isArray(relaysData && relaysData.installations)
    ? relaysData.installations
    : [];
  activeInstallationsCount = insts.length;

  const mainOn =
    relaysData && typeof relaysData.light_state === 'boolean'
      ? relaysData.light_state
      : insts.some((i) => i.light_state);
  const flashOn =
    relaysData && typeof relaysData.flash_state === 'boolean'
      ? relaysData.flash_state
      : insts.some((i) => i.flash_gpio_pin !== i.light_gpio_pin && i.flash_state);

  lastMainState = mainOn;
  lastFlashState = flashOn;

  if (mainToggle) {
    mainToggle.checked = mainOn;
    mainToggle.disabled = activeInstallationsCount === 0;
  }
  if (flashToggle) {
    flashToggle.checked = flashOn;
    flashToggle.disabled = activeInstallationsCount === 0;
  }
  if (testBtn) {
    testBtn.disabled = activeInstallationsCount === 0;
  }

  if (mainLabel) {
    mainLabel.textContent =
      activeInstallationsCount === 0
        ? 'Нет активных установок'
        : mainOn
          ? 'Включён'
          : 'Выключен';
  }
  if (flashLabel) {
    flashLabel.textContent =
      activeInstallationsCount === 0
        ? 'Нет активных установок'
        : flashOn
          ? 'Включена'
          : 'Выключена';
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

async function handleMainToggleChange(e) {
  const toggle = e.target;
  const nextState = Boolean(toggle.checked);
  showLightError('');
  toggle.disabled = true;
  try {
    const res = await api.controlLightMain('all', nextState);
    if (res && res.status === 'error') {
      throw new Error(res.result_message || 'Ошибка переключения света');
    }
    showToast(nextState ? 'Основной свет включён для всех установок' : 'Основной свет выключен');
    await refreshRelaysState();
  } catch (err) {
    toggle.checked = lastMainState;
    showLightError(err.message || 'Не удалось переключить основной свет');
  } finally {
    toggle.disabled = activeInstallationsCount === 0;
  }
}

async function handleFlashToggleChange(e) {
  const toggle = e.target;
  const nextState = Boolean(toggle.checked);
  showLightError('');
  toggle.disabled = true;
  try {
    const res = await api.controlLightFlash('all', nextState);
    if (res && res.status === 'error') {
      throw new Error(res.result_message || 'Ошибка переключения вспышки');
    }
    showToast(nextState ? 'Вспышка включена для всех установок' : 'Вспышка выключена');
    await refreshRelaysState();
  } catch (err) {
    toggle.checked = lastFlashState;
    showLightError(err.message || 'Не удалось переключить вспышку');
  } finally {
    toggle.disabled = activeInstallationsCount === 0;
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
  const mainToggle = document.getElementById('light-toggle-main');
  const flashToggle = document.getElementById('light-toggle-flash');
  const testBtn = document.getElementById('btn-light-relay-test');
  const delayInput = document.getElementById('light-capture-delay-input');

  if (btnOpen) btnOpen.addEventListener('click', openLightControlModal);
  if (closeBtn) closeBtn.addEventListener('click', closeLightControlModal);
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeLightControlModal();
    });
  }

  if (mainToggle) mainToggle.addEventListener('change', handleMainToggleChange);
  if (flashToggle) flashToggle.addEventListener('change', handleFlashToggleChange);
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
