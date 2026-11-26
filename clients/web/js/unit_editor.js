// clients/web/js/unit_editor.js
// Единая модалка создания и редактирования установки (активной и архивной)

import { api } from './api.js';
import { Modal, showToast } from './dialogs.js';
import { getPlaceholderHTML, getUnitEditorPlaceholderHTML } from './placeholders.js';
import {
  ColorPicker,
  hexToRgb,
  hsvToRgb,
  normalizeHexColor,
  rgbToHex,
  rgbToHsv,
} from './components/color_picker.js';

let currentRole = 'guest';
let editingUnit = null;
let loadedIntervalHours = 1;
let initialCameraIds = [];
let onUnitSavedCallback = null;
let isBusy = false;
let unitEditorOpenSeq = 0;
let unitEditorModalCtrl = null;
let colorPicker = null;

let activeColorChannel = null; // 'light' | 'flash' | null
let colorDebounceTimer = null;

export function parseGpioPinsInput(rawValue, label = 'GPIO пин') {
  const raw = String(rawValue ?? '').trim();
  if (!raw) {
    return { error: `${label}: укажите 1 пин (0..53) или 3 уникальных пина через запятую` };
  }
  const parts = raw.split(',').map((p) => p.trim());
  if (parts.some((p) => p === '')) {
    return { error: `${label}: некорректный формат пинов` };
  }
  if (parts.length !== 1 && parts.length !== 3) {
    return {
      error: `${label}: укажите 1 пин (белый свет) или ровно 3 уникальных пина через запятую (RGB)`,
    };
  }
  const pins = [];
  for (const part of parts) {
    if (!/^-?\d+$/.test(part)) {
      return { error: `${label}: '${part}' — некорректное значение. Ожидается целое число от 0 до 53 или последовательность из 3 чисел от 0 до 53` };
    }
    const num = Number(part);
    if (!Number.isInteger(num) || num < 0 || num > 53) {
      return { error: `${label}: пин ${num} вне диапазона. Ожидается целое число от 0 до 53 или последовательность из 3 чисел от 0 до 53` };
    }
    pins.push(num);
  }
  if (pins.length === 3 && new Set(pins).size !== 3) {
    return { error: `${label}: пины RGB-ленты не должны повторяться` };
  }
  return {
    pins,
    formatted: pins.length === 1 ? pins[0] : pins.join(','),
  };
}

export { normalizeHexColor, hexToRgb, rgbToHex, rgbToHsv, hsvToRgb };

function isChannelRgb(unit, isFlash) {
  if (!unit) return false;
  if (isFlash) {
    if (unit.flash_is_rgb !== undefined) return Boolean(unit.flash_is_rgb);
    if (Array.isArray(unit.flash_pins)) return unit.flash_pins.length === 3;
    return String(unit.flash_gpio_pin || '').includes(',');
  }
  if (unit.light_is_rgb !== undefined) return Boolean(unit.light_is_rgb);
  if (Array.isArray(unit.light_pins)) return unit.light_pins.length === 3;
  return String(unit.light_gpio_pin || '').includes(',');
}

function getChannelColor(unit, isFlash) {
  if (!unit) return '#FFFFFF';
  const raw = isFlash ? unit.flash_color : unit.light_color;
  return normalizeHexColor(raw) || '#FFFFFF';
}

export function setUnitEditorRole(role) {
  currentRole = role || 'guest';
  if (currentRole === 'guest') {
    closeUnitEditorModal();
  }
}

export function setUnitSavedHandler(fn) {
  onUnitSavedCallback = fn;
}

function showEditorError(msg) {
  const errEl = document.getElementById('unit-editor-error');
  if (!errEl) return;
  if (msg) {
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  } else {
    errEl.textContent = '';
    errEl.classList.add('hidden');
  }
}

function hideDeleteConfirm() {
  const box = document.getElementById('unit-editor-delete-confirm');
  const delBtn = document.getElementById('unit-editor-delete-btn');
  if (box) box.classList.add('hidden');
  if (delBtn && editingUnit && editingUnit.is_archived && currentRole === 'developer') {
    delBtn.classList.remove('hidden');
  }
}

function showDeleteConfirm() {
  const box = document.getElementById('unit-editor-delete-confirm');
  const delBtn = document.getElementById('unit-editor-delete-btn');
  if (delBtn) delBtn.classList.add('hidden');
  if (box) box.classList.remove('hidden');
}

function formatNextCaptureLabel(unit) {
  if (!unit) return '';
  if (unit.is_archived) return 'Установка в архиве';
  if (!unit.next_capture_at) return '';
  const dt = new Date(unit.next_capture_at);
  const timeStr = !isNaN(dt.getTime())
    ? dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : unit.next_capture_at;
  return `След. кадр: ${timeStr}`;
}

function renderPreview(unit) {
  const mediaEl = document.getElementById('unit-editor-preview-media');
  const nextEl = document.getElementById('unit-editor-next-capture');
  if (mediaEl) {
    const framesCount = unit ? Number(unit.frames_count || 0) : 0;
    const frameUrl = unit ? unit.last_frame_url || unit.last_frame_raw_url : null;
    if (unit && framesCount > 0 && frameUrl) {
      mediaEl.innerHTML = '';
      const img = document.createElement('img');
      img.className = 'unit-editor-preview-img';
      img.alt = unit.name || 'Превью установки';
      img.src = `${frameUrl}?t=${Date.now()}`;
      img.onerror = () => {
        mediaEl.innerHTML = getPlaceholderHTML('D');
      };
      mediaEl.appendChild(img);
    } else {
      mediaEl.innerHTML = getUnitEditorPlaceholderHTML('Растение ещё не готово к фотосессии...');
    }
  }

  if (nextEl) {
    const label = formatNextCaptureLabel(unit);
    if (label) {
      nextEl.textContent = label;
      nextEl.classList.remove('hidden');
    } else {
      nextEl.textContent = '';
      nextEl.classList.add('hidden');
    }
  }
}

function formatCameraLabel(cam) {
  const name = (cam && cam.auto_name ? String(cam.auto_name) : '').trim();
  if (name && name !== cam.hardware_id) {
    return name;
  }
  return cam && cam.hardware_id ? cam.hardware_id : `Камера #${cam ? cam.id : '?'}`;
}

function populateCameraOptions(cameras, unit) {
  const select = document.getElementById('unit-editor-camera');
  if (!select) return;

  select.innerHTML = '';
  const emptyOpt = document.createElement('option');
  emptyOpt.value = '';
  emptyOpt.textContent = 'Без камеры';
  select.appendChild(emptyOpt);

  const unitId = unit ? Number(unit.id) : null;
  const attachedCams = [];
  const freeCams = [];

  (cameras || []).forEach((cam) => {
    if (unitId !== null && Number(cam.installation_id) === unitId) {
      attachedCams.push(cam);
    } else if (cam.installation_id === null || cam.installation_id === undefined) {
      freeCams.push(cam);
    }
  });

  initialCameraIds = attachedCams.map((c) => Number(c.id));

  attachedCams.forEach((cam) => {
    const opt = document.createElement('option');
    opt.value = String(cam.id);
    const statusLabel = cam.is_online ? 'онлайн' : 'офлайн';
    opt.textContent = `${formatCameraLabel(cam)} (текущая, ${statusLabel})`;
    select.appendChild(opt);
  });

  freeCams.forEach((cam) => {
    const opt = document.createElement('option');
    opt.value = String(cam.id);
    const statusLabel = cam.is_online ? 'онлайн' : 'офлайн';
    opt.textContent = `${formatCameraLabel(cam)} (${statusLabel})`;
    select.appendChild(opt);
  });

  select.value = initialCameraIds.length > 0 ? String(initialCameraIds[0]) : '';
}

function applyRoleAndStatusConstraints(unit) {
  const isDev = currentRole === 'developer';
  const isArchived = Boolean(unit && unit.is_archived);

  const nameInput = document.getElementById('unit-editor-name');
  const cultureInput = document.getElementById('unit-editor-culture');
  const intervalInput = document.getElementById('unit-editor-interval');
  const cameraSelect = document.getElementById('unit-editor-camera');
  const lightPinInput = document.getElementById('unit-editor-light-pin');
  const flashPinInput = document.getElementById('unit-editor-flash-pin');
  const delayInput = document.getElementById('unit-editor-delay');
  const arucoInput = document.getElementById('unit-editor-aruco');
  const roleHintEl = document.getElementById('unit-editor-role-hint');

  if (nameInput) nameInput.disabled = false;
  if (cultureInput) cultureInput.disabled = false;
  if (delayInput) delayInput.disabled = false;

  if (intervalInput) intervalInput.disabled = !isDev;
  if (arucoInput) arucoInput.disabled = !isDev;
  if (lightPinInput) lightPinInput.disabled = !isDev || isArchived;
  if (flashPinInput) flashPinInput.disabled = !isDev || isArchived;
  if (cameraSelect) cameraSelect.disabled = !isDev || isArchived;

  if (roleHintEl) {
    if (!isDev) {
      roleHintEl.textContent =
        'Режим биолога: доступно изменение названия, культуры и задержки съёмки.';
      roleHintEl.classList.remove('hidden');
    } else if (isArchived) {
      roleHintEl.textContent =
        'Установка в архиве: изменение GPIO-пинов и привязка камер доступны после возврата из архива.';
      roleHintEl.classList.remove('hidden');
    } else {
      roleHintEl.textContent = '';
      roleHintEl.classList.add('hidden');
    }
  }

  const archiveBtn = document.getElementById('unit-editor-archive-btn');
  const unarchiveBtn = document.getElementById('unit-editor-unarchive-btn');
  const deleteBtn = document.getElementById('unit-editor-delete-btn');
  const deleteConfirm = document.getElementById('unit-editor-delete-confirm');

  if (deleteConfirm) deleteConfirm.classList.add('hidden');

  if (!unit) {
    if (archiveBtn) archiveBtn.classList.add('hidden');
    if (unarchiveBtn) unarchiveBtn.classList.add('hidden');
    if (deleteBtn) deleteBtn.classList.add('hidden');
  } else if (isArchived) {
    if (archiveBtn) archiveBtn.classList.add('hidden');
    if (unarchiveBtn) unarchiveBtn.classList.toggle('hidden', !isDev);
    if (deleteBtn) deleteBtn.classList.toggle('hidden', !isDev);
  } else {
    if (archiveBtn) archiveBtn.classList.toggle('hidden', !isDev);
    if (unarchiveBtn) unarchiveBtn.classList.add('hidden');
    if (deleteBtn) deleteBtn.classList.add('hidden');
  }

  syncUnitLightControls(unit);
}

function updateChannelBadge(isFlash, unit) {
  const prefix = isFlash ? 'unit-editor-flash' : 'unit-editor-light';
  const card = document.getElementById(`${prefix}-card`);
  const swatch = document.getElementById(`${prefix}-swatch`);
  const hexEl = document.getElementById(`${prefix}-hex`);
  const hintEl = document.getElementById(`${prefix}-rgb-hint`);

  const rgb = isChannelRgb(unit, isFlash);
  const color = getChannelColor(unit, isFlash);

  if (card) {
    card.classList.toggle('is-rgb', rgb);
    card.classList.toggle(
      'is-expanded',
      rgb && activeColorChannel === (isFlash ? 'flash' : 'light')
    );
  }
  if (swatch) {
    swatch.classList.toggle('hidden', !rgb);
    swatch.style.backgroundColor = color;
  }
  if (hexEl) {
    hexEl.classList.toggle('hidden', !rgb);
    hexEl.textContent = color;
  }
  if (hintEl) {
    hintEl.classList.toggle('hidden', !rgb);
  }
}

function applySelectedColorToUnit(hex) {
  if (editingUnit && activeColorChannel) {
    if (activeColorChannel === 'flash') {
      editingUnit.flash_color = hex;
    } else {
      editingUnit.light_color = hex;
    }
    updateChannelBadge(activeColorChannel === 'flash', editingUnit);
  }
}

function getColorPicker() {
  if (!colorPicker) {
    colorPicker = new ColorPicker({
      svCanvas: 'unit-color-sv-canvas',
      hueCanvas: 'unit-color-hue-canvas',
      svCursor: 'unit-color-sv-cursor',
      hueCursor: 'unit-color-hue-cursor',
      rInput: 'unit-color-r',
      gInput: 'unit-color-g',
      bInput: 'unit-color-b',
      vInput: 'unit-color-v',
      sInput: 'unit-color-s',
      rVal: 'unit-color-r-val',
      gVal: 'unit-color-g-val',
      bVal: 'unit-color-b-val',
      vVal: 'unit-color-v-val',
      sVal: 'unit-color-s-val',
      hexInput: 'unit-color-hex-input',
      preview: 'unit-color-preview',
      nativePicker: 'unit-color-native-picker',
      onChange: (state) => {
        applySelectedColorToUnit(state.hex);
        if (activeColorChannel) {
          scheduleColorApiSend(activeColorChannel, state.hex);
        }
      },
      onCommit: (state) => {
        applySelectedColorToUnit(state.hex);
        if (activeColorChannel) {
          scheduleColorApiSend(activeColorChannel, state.hex, { immediate: true });
        }
      },
    });
  }
  return colorPicker;
}

function scheduleColorApiSend(channel, hex, { immediate = false } = {}) {
  if (!editingUnit || currentRole !== 'developer' || editingUnit.is_archived) return;
  if (colorDebounceTimer) {
    clearTimeout(colorDebounceTimer);
    colorDebounceTimer = null;
  }
  const targetId = editingUnit.id;
  const isFlash = channel === 'flash';
  const sendNow = async () => {
    if (!editingUnit || Number(editingUnit.id) !== Number(targetId)) return;
    try {
      const res = isFlash
        ? await api.controlLightFlash(targetId, { color: hex })
        : await api.controlLightMain(targetId, { color: hex });
      if (res && res.status === 'error') {
        throw new Error(res.result_message || 'Не удалось применить цвет');
      }
      if (editingUnit && Number(editingUnit.id) === Number(targetId)) {
        if (isFlash) {
          editingUnit.flash_color = res && res.color ? res.color : hex;
        } else {
          editingUnit.light_color = res && res.color ? res.color : hex;
        }
        updateChannelBadge(isFlash, editingUnit);
      }
    } catch (err) {
      showEditorError(err.message || 'Не удалось обновить цвет RGB-ленты');
    }
  };
  if (immediate) {
    sendNow();
  } else {
    colorDebounceTimer = setTimeout(sendNow, 160);
  }
}

function closeColorPanel() {
  activeColorChannel = null;
  if (colorDebounceTimer) {
    clearTimeout(colorDebounceTimer);
    colorDebounceTimer = null;
  }
  const panel = document.getElementById('unit-editor-color-panel');
  if (panel) {
    panel.classList.remove('is-open');
    panel.classList.add('hidden');
  }
  if (editingUnit) {
    updateChannelBadge(false, editingUnit);
    updateChannelBadge(true, editingUnit);
  }
}

function openColorPanel(channel) {
  if (!editingUnit || currentRole !== 'developer' || editingUnit.is_archived) return;
  const isFlash = channel === 'flash';
  if (!isChannelRgb(editingUnit, isFlash)) return;

  if (activeColorChannel === channel) {
    closeColorPanel();
    return;
  }

  activeColorChannel = channel;
  const panel = document.getElementById('unit-editor-color-panel');
  const titleEl = document.getElementById('unit-color-panel-title');
  if (titleEl) {
    titleEl.textContent = isFlash
      ? 'Вспышка — настройка RGB-цвета'
      : 'Основной свет — настройка RGB-цвета';
  }
  const rgb = hexToRgb(getChannelColor(editingUnit, isFlash)) || { r: 255, g: 255, b: 255 };
  const picker = getColorPicker();
  picker.setRgb(rgb.r, rgb.g, rgb.b);
  applySelectedColorToUnit(picker.getState().hex);

  if (panel) {
    panel.classList.remove('hidden');
    requestAnimationFrame(() => {
      panel.classList.add('is-open');
      picker.drawCanvases();
    });
  }
  updateChannelBadge(false, editingUnit);
  updateChannelBadge(true, editingUnit);
}

function closeDayNightPanel() {
  const dnCard = document.getElementById('unit-editor-day-night-card');
  const dnHeader = document.getElementById('unit-editor-day-night-header');
  if (dnCard) dnCard.classList.remove('is-open');
  if (dnHeader) dnHeader.setAttribute('aria-expanded', 'false');
}

function toggleDayNightPanel() {
  const dnCard = document.getElementById('unit-editor-day-night-card');
  const dnHeader = document.getElementById('unit-editor-day-night-header');
  if (!dnCard || dnCard.classList.contains('hidden')) return;
  const nextOpen = !dnCard.classList.contains('is-open');
  dnCard.classList.toggle('is-open', nextOpen);
  if (dnHeader) dnHeader.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
}

function syncUnitLightControls(unit) {
  const lightCard = document.getElementById('unit-editor-light-card');
  const flashCard = document.getElementById('unit-editor-flash-card');
  const lightToggle = document.getElementById('unit-editor-light-toggle');
  const flashToggle = document.getElementById('unit-editor-flash-toggle');
  const lightLockedEl = document.getElementById('unit-editor-light-locked');
  const flashLockedEl = document.getElementById('unit-editor-flash-locked');
  const dnCard = document.getElementById('unit-editor-day-night-card');
  const dnLockedEl = document.getElementById('unit-editor-day-night-locked');
  const dnStatusEl = document.getElementById('unit-editor-day-night-status');
  const dnToggle = document.getElementById('unit-editor-day-night-toggle');
  const dnStartInput = document.getElementById('unit-editor-day-night-start');
  const dnEndInput = document.getElementById('unit-editor-day-night-end');
  const dnPhaseBadge = document.getElementById('unit-editor-day-night-phase-badge');

  const visible = Boolean(unit) && currentRole === 'developer';
  if (lightCard) lightCard.classList.toggle('hidden', !visible);
  if (flashCard) flashCard.classList.toggle('hidden', !visible);
  if (dnCard) dnCard.classList.toggle('hidden', !visible);

  if (!visible) {
    if (lightLockedEl) lightLockedEl.classList.add('hidden');
    if (flashLockedEl) flashLockedEl.classList.add('hidden');
    if (dnLockedEl) dnLockedEl.classList.add('hidden');
    closeDayNightPanel();
    closeColorPanel();
    return;
  }

  const isArchived = Boolean(unit.is_archived);
  const indLight =
    unit.individual_light_state !== undefined
      ? Boolean(unit.individual_light_state)
      : Boolean(unit.light_state !== undefined ? unit.light_state : true);
  const indFlash =
    unit.individual_flash_state !== undefined
      ? Boolean(unit.individual_flash_state)
      : Boolean(unit.flash_state !== undefined ? unit.flash_state : false);

  const instDnEnabled = Boolean(unit.day_night_enabled);
  const dayStart = unit.day_start || '08:00';
  const dayEnd = unit.day_end || '22:00';
  const isNightPhase = unit.day_night_phase === 'night';
  const phaseLabel = isNightPhase ? 'Ночь' : 'День';

  const dayNightLocked =
    Boolean(unit.day_night_locked) ||
    Boolean(unit.global_day_night_enabled) ||
    (unit.global_light_mode !== undefined && unit.global_light_mode !== 'default');
  const lightLocked = Boolean(unit.light_locked) || dayNightLocked || instDnEnabled;
  const flashLocked =
    Boolean(unit.flash_locked) ||
    (unit.global_flash_mode !== undefined && unit.global_flash_mode !== 'default');

  if (lightToggle) {
    lightToggle.checked = indLight;
    lightToggle.disabled = lightLocked || isArchived;
  }
  if (lightLockedEl) {
    lightLockedEl.textContent =
      unit.light_lock_reason || (dayNightLocked ? 'Заблокировано' : 'Световой день');
    lightLockedEl.classList.toggle('hidden', !lightLocked);
  }

  if (flashToggle) {
    flashToggle.checked = indFlash;
    flashToggle.disabled = flashLocked || isArchived;
  }
  if (flashLockedEl) {
    flashLockedEl.classList.toggle('hidden', !flashLocked);
  }

  if (dnLockedEl) {
    dnLockedEl.textContent = 'Заблокировано';
    dnLockedEl.classList.toggle('hidden', !dayNightLocked);
  }
  if (dnStatusEl) {
    dnStatusEl.textContent = instDnEnabled
      ? `${dayStart}–${dayEnd} · ${phaseLabel}`
      : 'Выкл';
  }
  if (dnToggle) {
    dnToggle.checked = instDnEnabled;
    dnToggle.disabled = dayNightLocked || isArchived;
  }
  if (dnStartInput) {
    dnStartInput.value = dayStart;
    dnStartInput.disabled = !instDnEnabled || dayNightLocked || isArchived;
  }
  if (dnEndInput) {
    dnEndInput.value = dayEnd;
    dnEndInput.disabled = !instDnEnabled || dayNightLocked || isArchived;
  }
  if (dnPhaseBadge) {
    dnPhaseBadge.textContent = phaseLabel;
    dnPhaseBadge.classList.toggle('is-night', isNightPhase);
    dnPhaseBadge.classList.toggle('is-off', !instDnEnabled);
  }

  updateChannelBadge(false, unit);
  updateChannelBadge(true, unit);
  if (
    activeColorChannel &&
    !isChannelRgb(unit, activeColorChannel === 'flash')
  ) {
    closeColorPanel();
  }
}

async function handleUnitDayNightChange({ toggleChanged = false } = {}) {
  if (!editingUnit || currentRole !== 'developer' || editingUnit.is_archived) return;
  const dnToggle = document.getElementById('unit-editor-day-night-toggle');
  const dnStartInput = document.getElementById('unit-editor-day-night-start');
  const dnEndInput = document.getElementById('unit-editor-day-night-end');
  if (!dnToggle || !dnStartInput || !dnEndInput) return;

  const dayNightLocked =
    Boolean(editingUnit.day_night_locked) ||
    Boolean(editingUnit.global_day_night_enabled) ||
    (editingUnit.global_light_mode !== undefined &&
      editingUnit.global_light_mode !== 'default');
  if (dayNightLocked) {
    syncUnitLightControls(editingUnit);
    return;
  }

  const prevEnabled = Boolean(editingUnit.day_night_enabled);
  const prevStart = editingUnit.day_start || '08:00';
  const prevEnd = editingUnit.day_end || '22:00';
  const nextEnabled = Boolean(dnToggle.checked);
  const nextStart = String(dnStartInput.value || prevStart).trim();
  const nextEnd = String(dnEndInput.value || prevEnd).trim();

  if (!/^\d{2}:\d{2}$/.test(nextStart) || !/^\d{2}:\d{2}$/.test(nextEnd)) {
    showEditorError('Укажите корректное время начала и окончания (ЧЧ:ММ)');
    if (toggleChanged) dnToggle.checked = prevEnabled;
    dnStartInput.value = prevStart;
    dnEndInput.value = prevEnd;
    return;
  }
  if (nextStart === nextEnd) {
    showEditorError('Время начала и окончания светового дня не могут совпадать');
    if (toggleChanged) dnToggle.checked = prevEnabled;
    dnStartInput.value = prevStart;
    dnEndInput.value = prevEnd;
    return;
  }

  const targetId = editingUnit.id;
  showEditorError('');
  dnToggle.disabled = true;
  dnStartInput.disabled = true;
  dnEndInput.disabled = true;

  try {
    const res = await api.controlDayNight({
      target: targetId,
      enabled: nextEnabled,
      day_start: nextStart,
      day_end: nextEnd,
    });
    if (editingUnit && Number(editingUnit.id) === Number(targetId) && res) {
      Object.assign(editingUnit, res);
      syncUnitLightControls(editingUnit);
    }
    showToast(
      nextEnabled
        ? `Световой день «${editingUnit ? editingUnit.name : ''}»: ${nextStart}–${nextEnd}`
        : `Световой день «${editingUnit ? editingUnit.name : ''}»: выключен`
    );
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    if (toggleChanged) dnToggle.checked = prevEnabled;
    dnStartInput.value = prevStart;
    dnEndInput.value = prevEnd;
    showEditorError(err.message || 'Не удалось обновить световой день');
  } finally {
    if (editingUnit && Number(editingUnit.id) === Number(targetId)) {
      syncUnitLightControls(editingUnit);
    }
  }
}

async function handleUnitLightToggle(isFlash) {
  if (!editingUnit || currentRole !== 'developer' || editingUnit.is_archived) return;
  const toggle = document.getElementById(
    isFlash ? 'unit-editor-flash-toggle' : 'unit-editor-light-toggle'
  );
  if (!toggle) return;

  const isLocked = isFlash
    ? Boolean(editingUnit.flash_locked) ||
      (editingUnit.global_flash_mode !== undefined && editingUnit.global_flash_mode !== 'default')
    : Boolean(editingUnit.light_locked) ||
      Boolean(editingUnit.day_night_locked) ||
      Boolean(editingUnit.day_night_enabled) ||
      Boolean(editingUnit.global_day_night_enabled) ||
      (editingUnit.global_light_mode !== undefined && editingUnit.global_light_mode !== 'default');
  if (isLocked) {
    syncUnitLightControls(editingUnit);
    return;
  }

  const nextState = Boolean(toggle.checked);
  const targetId = editingUnit.id;
  showEditorError('');
  toggle.disabled = true;

  try {
    const res = isFlash
      ? await api.controlLightFlash(targetId, nextState)
      : await api.controlLightMain(targetId, nextState);
    if (res && res.status === 'error') {
      throw new Error(res.result_message || 'Ошибка переключения реле');
    }
    if (editingUnit && Number(editingUnit.id) === Number(targetId)) {
      if (isFlash) {
        editingUnit.individual_flash_state = nextState;
        editingUnit.flash_state =
          res && res.effective_state !== undefined ? Boolean(res.effective_state) : nextState;
      } else {
        editingUnit.individual_light_state = nextState;
        editingUnit.light_state =
          res && res.effective_state !== undefined ? Boolean(res.effective_state) : nextState;
      }
      syncUnitLightControls(editingUnit);
    }
    showToast(
      isFlash
        ? `Вспышка «${editingUnit ? editingUnit.name : ''}»: ${nextState ? 'включена' : 'выключена'}`
        : `Основной свет «${editingUnit ? editingUnit.name : ''}»: ${nextState ? 'включён' : 'выключен'}`
    );
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    toggle.checked = !nextState;
    showEditorError(
      err.message ||
        (isFlash ? 'Не удалось переключить вспышку' : 'Не удалось переключить основной свет')
    );
  } finally {
    if (editingUnit && Number(editingUnit.id) === Number(targetId)) {
      syncUnitLightControls(editingUnit);
    }
  }
}

function setButtonsBusy(busy) {
  isBusy = busy;
  const ids = [
    'unit-editor-save-btn',
    'unit-editor-cancel-btn',
    'unit-editor-archive-btn',
    'unit-editor-unarchive-btn',
    'unit-editor-delete-btn',
    'unit-editor-delete-confirm-yes',
  ];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

function getUnitEditorModalCtrl() {
  if (!unitEditorModalCtrl) {
    unitEditorModalCtrl = new Modal('modal-unit-editor', {
      closeButtons: ['modal-unit-editor-close', 'unit-editor-cancel-btn'],
      onClose: () => {
        unitEditorOpenSeq += 1;
        closeColorPanel();
        editingUnit = null;
        initialCameraIds = [];
        isBusy = false;
        showEditorError('');
        hideDeleteConfirm();
        syncUnitLightControls(null);
      },
    });
  }
  return unitEditorModalCtrl;
}

async function openUnitModal(unit = null) {
  if (currentRole === 'guest') return;
  if (!unit && currentRole !== 'developer') return;

  const modalCtrl = getUnitEditorModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;

  const openSeq = ++unitEditorOpenSeq;
  editingUnit = unit || null;
  initialCameraIds = [];
  loadedIntervalHours = 1;
  showEditorError('');
  setButtonsBusy(false);

  const titleEl = document.getElementById('unit-editor-title');
  if (titleEl) {
    titleEl.textContent = editingUnit
      ? `Редактирование установки: ${editingUnit.name}`
      : 'Создание новой установки';
  }

  renderPreview(editingUnit);

  const nameInput = document.getElementById('unit-editor-name');
  const cultureInput = document.getElementById('unit-editor-culture');
  const intervalInput = document.getElementById('unit-editor-interval');
  const lightPinInput = document.getElementById('unit-editor-light-pin');
  const flashPinInput = document.getElementById('unit-editor-flash-pin');
  const delayInput = document.getElementById('unit-editor-delay');
  const arucoInput = document.getElementById('unit-editor-aruco');

  if (nameInput) nameInput.value = editingUnit ? editingUnit.name || '' : '';
  if (cultureInput) cultureInput.value = editingUnit ? editingUnit.culture_name || '' : '';
  if (lightPinInput) {
    lightPinInput.value =
      editingUnit && editingUnit.light_gpio_pin !== undefined
        ? String(editingUnit.light_gpio_pin)
        : '17';
  }
  if (flashPinInput) {
    flashPinInput.value =
      editingUnit && editingUnit.flash_gpio_pin !== undefined
        ? String(editingUnit.flash_gpio_pin)
        : '27';
  }
  if (delayInput) {
    delayInput.value =
      editingUnit &&
      editingUnit.capture_delay_sec !== null &&
      editingUnit.capture_delay_sec !== undefined
        ? String(editingUnit.capture_delay_sec)
        : '';
  }
  if (arucoInput) {
    arucoInput.value =
      editingUnit &&
      editingUnit.aruco_size_mm !== null &&
      editingUnit.aruco_size_mm !== undefined
        ? String(editingUnit.aruco_size_mm)
        : '';
  }
  if (intervalInput) {
    intervalInput.value = '1';
  }

  // Предварительно заполняем select из unit.cameras до ответа /api/cameras
  populateCameraOptions(
    editingUnit && Array.isArray(editingUnit.cameras)
      ? editingUnit.cameras.map((c) => ({ ...c, installation_id: editingUnit.id }))
      : [],
    editingUnit
  );

  applyRoleAndStatusConstraints(editingUnit);

  modalCtrl.open();

  // Подгружаем настройки расписания, список камер и свежее состояние установки с сервера
  try {
    const requests = [
      api.getSystemSettings(),
      api.getCameras(),
    ];
    if (editingUnit && currentRole === 'developer' && typeof api.getInstallation === 'function') {
      requests.push(api.getInstallation(editingUnit.id));
    }
    const [settingsRes, camerasRes, freshUnitRes] = await Promise.allSettled(requests);

    if (openSeq !== unitEditorOpenSeq || modal.classList.contains('hidden')) return;

    if (settingsRes.status === 'fulfilled' && settingsRes.value) {
      const s = settingsRes.value;
      loadedIntervalHours = Number(s.capture_interval_hours || 1);
      if (intervalInput) {
        intervalInput.value = String(loadedIntervalHours);
      }
      if (delayInput) {
        const globalDelay =
          s.capture_delay_sec !== undefined && s.capture_delay_sec !== null
            ? s.capture_delay_sec
            : 1.5;
        delayInput.placeholder = `По умолчанию (${globalDelay} с)`;
      }
    }

    if (camerasRes.status === 'fulfilled' && Array.isArray(camerasRes.value)) {
      populateCameraOptions(camerasRes.value, editingUnit);
    }

    if (
      freshUnitRes &&
      freshUnitRes.status === 'fulfilled' &&
      freshUnitRes.value &&
      editingUnit &&
      Number(freshUnitRes.value.id) === Number(editingUnit.id)
    ) {
      editingUnit = { ...editingUnit, ...freshUnitRes.value };
      syncUnitLightControls(editingUnit);
    }
  } catch (_) {
    // Ошибки фоновой подгрузки не блокируют форму
  }
}

export function openUnitCreateModal() {
  return openUnitModal(null);
}

export function openUnitEditModal(unit) {
  return openUnitModal(unit);
}

export function closeUnitEditorModal() {
  getUnitEditorModalCtrl().close();
}

function showWarningsIfAny(warnings) {
  if (!Array.isArray(warnings) || warnings.length === 0) return;
  warnings.forEach((w) => showToast(w, 'info', 5500));
}

async function handleSaveSubmit(e) {
  e.preventDefault();
  if (isBusy || currentRole === 'guest') return;
  showEditorError('');

  const isDev = currentRole === 'developer';
  const name = (document.getElementById('unit-editor-name')?.value || '').trim();
  const culture = (document.getElementById('unit-editor-culture')?.value || '').trim();
  const rawInterval = (document.getElementById('unit-editor-interval')?.value || '').trim();
  const rawLightPin = (document.getElementById('unit-editor-light-pin')?.value || '').trim();
  const rawFlashPin = (document.getElementById('unit-editor-flash-pin')?.value || '').trim();
  const rawDelay = (document.getElementById('unit-editor-delay')?.value || '').trim();
  const rawAruco = (document.getElementById('unit-editor-aruco')?.value || '').trim();
  const selectedCamVal = document.getElementById('unit-editor-camera')?.value ?? '';

  if (!name) {
    showEditorError('Укажите название установки');
    return;
  }
  if (!culture) {
    showEditorError('Укажите выращиваемую культуру');
    return;
  }

  let delayVal = null;
  if (rawDelay !== '') {
    const parsed = Number(rawDelay.replace(',', '.'));
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 30) {
      showEditorError('Задержка прогрева должна быть от 0 до 30 секунд');
      return;
    }
    delayVal = Math.round(parsed * 100) / 100;
  }

  let arucoVal = null;
  if (isDev && rawAruco !== '') {
    const parsed = Number(rawAruco.replace(',', '.'));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      showEditorError('Размер маркера ArUco должен быть положительным числом');
      return;
    }
    arucoVal = parsed;
  }

  let intervalVal = loadedIntervalHours;
  if (isDev && rawInterval !== '') {
    const parsed = Number(rawInterval);
    if (!Number.isInteger(parsed) || parsed < 1) {
      showEditorError('Расписание съёмки должно быть целым числом часов (≥ 1)');
      return;
    }
    intervalVal = parsed;
  }

  let lightPinVal = editingUnit ? editingUnit.light_gpio_pin : 17;
  let flashPinVal = editingUnit ? editingUnit.flash_gpio_pin : 27;
  if (isDev && (!editingUnit || !editingUnit.is_archived)) {
    const parsedLight = parseGpioPinsInput(rawLightPin, 'GPIO пин света');
    if (parsedLight.error) {
      showEditorError(parsedLight.error);
      return;
    }
    const parsedFlash = parseGpioPinsInput(rawFlashPin, 'GPIO пин вспышки');
    if (parsedFlash.error) {
      showEditorError(parsedFlash.error);
      return;
    }
    const overlap = parsedLight.pins.filter((p) => parsedFlash.pins.includes(p));
    if (overlap.length > 0) {
      showEditorError(
        `Пины света и вспышки не должны пересекаться (совпадают: ${overlap.join(', ')})`
      );
      return;
    }
    lightPinVal = parsedLight.formatted;
    flashPinVal = parsedFlash.formatted;
  }

  setButtonsBusy(true);
  try {
    let savedUnit = null;
    if (!editingUnit) {
      if (!isDev) {
        showEditorError('Создавать установки может только разработчик');
        setButtonsBusy(false);
        return;
      }
      savedUnit = await api.createInstallation({
        name,
        culture_name: culture,
        light_gpio_pin: lightPinVal,
        flash_gpio_pin: flashPinVal,
        aruco_size_mm: arucoVal,
        capture_delay_sec: delayVal,
      });

      if (intervalVal !== loadedIntervalHours) {
        await api.patchSystemSettings({ capture_interval_hours: intervalVal });
      }
      if (selectedCamVal !== '') {
        await api.bindCamera(Number(selectedCamVal), savedUnit.id);
      }
      showToast(`Установка «${savedUnit.name}» создана`);
      showWarningsIfAny(savedUnit.warnings);
    } else {
      const patchPayload = {
        name,
        culture_name: culture,
        capture_delay_sec: delayVal,
      };
      if (isDev) {
        patchPayload.aruco_size_mm = arucoVal;
        if (!editingUnit.is_archived) {
          const curLightNorm = parseGpioPinsInput(
            String(editingUnit.light_gpio_pin ?? '')
          ).formatted;
          const curFlashNorm = parseGpioPinsInput(
            String(editingUnit.flash_gpio_pin ?? '')
          ).formatted;
          if (String(lightPinVal) !== String(curLightNorm)) {
            patchPayload.light_gpio_pin = lightPinVal;
          }
          if (String(flashPinVal) !== String(curFlashNorm)) {
            patchPayload.flash_gpio_pin = flashPinVal;
          }
        }
      }

      savedUnit = await api.patchInstallation(editingUnit.id, patchPayload);

      if (isDev && intervalVal !== loadedIntervalHours) {
        await api.patchSystemSettings({ capture_interval_hours: intervalVal });
      }

      if (isDev && !editingUnit.is_archived) {
        const prevPrimary = initialCameraIds.length > 0 ? String(initialCameraIds[0]) : '';
        if (selectedCamVal !== prevPrimary) {
          for (const prevId of initialCameraIds) {
            if (String(prevId) !== selectedCamVal) {
              await api.bindCamera(prevId, null);
            }
          }
          if (selectedCamVal !== '') {
            await api.bindCamera(Number(selectedCamVal), editingUnit.id);
          }
        }
      }

      showToast(`Установка «${savedUnit.name}» сохранена`);
      showWarningsIfAny(savedUnit.warnings);
    }

    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Не удалось сохранить установку');
  } finally {
    setButtonsBusy(false);
  }
}

async function handleArchiveClick() {
  if (!editingUnit || isBusy || currentRole !== 'developer') return;
  showEditorError('');
  setButtonsBusy(true);
  try {
    await api.archiveInstallation(editingUnit.id);
    showToast('Установка переведена в архив');
    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Ошибка перевода в архив');
  } finally {
    setButtonsBusy(false);
  }
}

async function handleUnarchiveClick() {
  if (!editingUnit || isBusy || currentRole !== 'developer') return;
  showEditorError('');
  setButtonsBusy(true);
  try {
    const res = await api.unarchiveInstallation(editingUnit.id);
    showToast('Установка возвращена из архива');
    showWarningsIfAny(res && res.warnings);
    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Ошибка восстановления из архива');
  } finally {
    setButtonsBusy(false);
  }
}

async function handleDeleteConfirmYes() {
  if (!editingUnit || !editingUnit.is_archived || isBusy || currentRole !== 'developer') return;
  showEditorError('');
  setButtonsBusy(true);
  try {
    await api.deleteInstallation(editingUnit.id);
    showToast('Установка удалена');
    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Не удалось удалить установку');
    hideDeleteConfirm();
  } finally {
    setButtonsBusy(false);
  }
}

function initColorPickerEvents() {
  const closeBtn = document.getElementById('unit-color-panel-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', closeColorPanel);
  }
  getColorPicker().init();
}

export function initUnitEditor() {
  const modalCtrl = getUnitEditorModalCtrl();
  modalCtrl.bind();

  const form = document.getElementById('unit-editor-form');
  const archiveBtn = document.getElementById('unit-editor-archive-btn');
  const unarchiveBtn = document.getElementById('unit-editor-unarchive-btn');
  const deleteBtn = document.getElementById('unit-editor-delete-btn');
  const deleteYesBtn = document.getElementById('unit-editor-delete-confirm-yes');
  const deleteNoBtn = document.getElementById('unit-editor-delete-confirm-no');
  const lightToggle = document.getElementById('unit-editor-light-toggle');
  const flashToggle = document.getElementById('unit-editor-flash-toggle');
  const lightCard = document.getElementById('unit-editor-light-card');
  const flashCard = document.getElementById('unit-editor-flash-card');
  const dnHeader = document.getElementById('unit-editor-day-night-header');
  const dnToggle = document.getElementById('unit-editor-day-night-toggle');
  const dnStartInput = document.getElementById('unit-editor-day-night-start');
  const dnEndInput = document.getElementById('unit-editor-day-night-end');

  if (form) form.addEventListener('submit', handleSaveSubmit);
  if (archiveBtn) archiveBtn.addEventListener('click', handleArchiveClick);
  if (unarchiveBtn) unarchiveBtn.addEventListener('click', handleUnarchiveClick);
  if (deleteBtn) deleteBtn.addEventListener('click', showDeleteConfirm);
  if (deleteYesBtn) deleteYesBtn.addEventListener('click', handleDeleteConfirmYes);
  if (deleteNoBtn) deleteNoBtn.addEventListener('click', hideDeleteConfirm);
  if (lightToggle) {
    lightToggle.addEventListener('change', () => handleUnitLightToggle(false));
  }
  if (flashToggle) {
    flashToggle.addEventListener('change', () => handleUnitLightToggle(true));
  }
  if (dnHeader) {
    dnHeader.addEventListener('click', toggleDayNightPanel);
    dnHeader.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggleDayNightPanel();
      }
    });
  }
  if (dnToggle) {
    dnToggle.addEventListener('change', () =>
      handleUnitDayNightChange({ toggleChanged: true })
    );
  }
  [dnStartInput, dnEndInput].forEach((inputEl) => {
    if (inputEl) {
      inputEl.addEventListener('change', () => handleUnitDayNightChange());
    }
  });

  [lightCard, flashCard].forEach((card, idx) => {
    if (!card) return;
    const toggleWrap = card.querySelector('.toggle-switch');
    if (toggleWrap) {
      toggleWrap.addEventListener('click', (e) => e.stopPropagation());
    }
    card.addEventListener('click', (e) => {
      if (e.target && e.target.closest && e.target.closest('.toggle-switch')) {
        return;
      }
      openColorPanel(idx === 1 ? 'flash' : 'light');
    });
  });

  initColorPickerEvents();
}
