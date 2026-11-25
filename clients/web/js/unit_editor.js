// clients/web/js/unit_editor.js
// Единая модалка создания и редактирования установки (активной и архивной)

import { api } from './api.js';
import { showToast } from './dialogs.js';
import { getPlaceholderHTML, getUnitEditorPlaceholderHTML } from './placeholders.js';

let currentRole = 'guest';
let editingUnit = null;
let loadedIntervalHours = 1;
let initialCameraIds = [];
let onUnitSavedCallback = null;
let isBusy = false;
let unitEditorOpenSeq = 0;

let activeColorChannel = null; // 'light' | 'flash' | null
let colorDebounceTimer = null;
const colorState = {
  h: 0,
  s: 0,
  v: 100,
  r: 255,
  g: 255,
  b: 255,
  hex: '#FFFFFF',
};

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
      return { error: `${label}: '${part}' не является целым числом от 0 до 53` };
    }
    const num = Number(part);
    if (!Number.isInteger(num) || num < 0 || num > 53) {
      return { error: `${label} должен быть целым числом от 0 до 53` };
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

export function normalizeHexColor(raw) {
  if (typeof raw !== 'string') return null;
  let cleaned = raw.trim();
  if (cleaned.startsWith('#')) {
    cleaned = cleaned.slice(1);
  }
  if (!/^[0-9a-fA-F]{6}$/.test(cleaned)) {
    return null;
  }
  return `#${cleaned.toUpperCase()}`;
}

export function hexToRgb(hex) {
  const norm = normalizeHexColor(hex);
  if (!norm) return null;
  return {
    r: parseInt(norm.slice(1, 3), 16),
    g: parseInt(norm.slice(3, 5), 16),
    b: parseInt(norm.slice(5, 7), 16),
  };
}

export function rgbToHex(r, g, b) {
  const clamp = (n) => Math.max(0, Math.min(255, Math.round(Number(n) || 0)));
  const toHex = (n) => clamp(n).toString(16).padStart(2, '0').toUpperCase();
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

export function rgbToHsv(r, g, b) {
  const rn = Math.max(0, Math.min(255, Number(r) || 0)) / 255;
  const gn = Math.max(0, Math.min(255, Number(g) || 0)) / 255;
  const bn = Math.max(0, Math.min(255, Number(b) || 0)) / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const d = max - min;
  let h = 0;
  if (d !== 0) {
    if (max === rn) {
      h = ((gn - bn) / d) % 6;
    } else if (max === gn) {
      h = (bn - rn) / d + 2;
    } else {
      h = (rn - gn) / d + 4;
    }
    h *= 60;
    if (h < 0) h += 360;
  }
  const s = max === 0 ? 0 : (d / max) * 100;
  const v = max * 100;
  return {
    h: Math.round(h),
    s: Math.round(s),
    v: Math.round(v),
  };
}

export function hsvToRgb(h, s, v) {
  const hh = ((Number(h) % 360) + 360) % 360;
  const ss = Math.max(0, Math.min(100, Number(s) || 0)) / 100;
  const vv = Math.max(0, Math.min(100, Number(v) || 0)) / 100;
  const c = vv * ss;
  const x = c * (1 - Math.abs(((hh / 60) % 2) - 1));
  const m = vv - c;
  let rp = 0;
  let gp = 0;
  let bp = 0;
  if (hh < 60) {
    rp = c;
    gp = x;
  } else if (hh < 120) {
    rp = x;
    gp = c;
  } else if (hh < 180) {
    gp = c;
    bp = x;
  } else if (hh < 240) {
    gp = x;
    bp = c;
  } else if (hh < 300) {
    rp = x;
    bp = c;
  } else {
    rp = c;
    bp = x;
  }
  return {
    r: Math.round((rp + m) * 255),
    g: Math.round((gp + m) * 255),
    b: Math.round((bp + m) * 255),
  };
}

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

function drawColorCanvases() {
  const svCanvas = document.getElementById('unit-color-sv-canvas');
  const hueCanvas = document.getElementById('unit-color-hue-canvas');
  const svCursor = document.getElementById('unit-color-sv-cursor');
  const hueCursor = document.getElementById('unit-color-hue-cursor');

  if (hueCanvas && hueCanvas.getContext) {
    const ctx = hueCanvas.getContext('2d');
    if (ctx) {
      const w = hueCanvas.width;
      const h = hueCanvas.height;
      const grad = ctx.createLinearGradient(0, 0, w, 0);
      grad.addColorStop(0, '#FF0000');
      grad.addColorStop(1 / 6, '#FFFF00');
      grad.addColorStop(2 / 6, '#00FF00');
      grad.addColorStop(3 / 6, '#00FFFF');
      grad.addColorStop(4 / 6, '#0000FF');
      grad.addColorStop(5 / 6, '#FF00FF');
      grad.addColorStop(1, '#FF0000');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
    }
  }

  if (svCanvas && svCanvas.getContext) {
    const ctx = svCanvas.getContext('2d');
    if (ctx) {
      const w = svCanvas.width;
      const h = svCanvas.height;
      const pure = hsvToRgb(colorState.h, 100, 100);
      ctx.fillStyle = rgbToHex(pure.r, pure.g, pure.b);
      ctx.fillRect(0, 0, w, h);

      const whiteGrad = ctx.createLinearGradient(0, 0, w, 0);
      whiteGrad.addColorStop(0, 'rgba(255,255,255,1)');
      whiteGrad.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = whiteGrad;
      ctx.fillRect(0, 0, w, h);

      const blackGrad = ctx.createLinearGradient(0, 0, 0, h);
      blackGrad.addColorStop(0, 'rgba(0,0,0,0)');
      blackGrad.addColorStop(1, 'rgba(0,0,0,1)');
      ctx.fillStyle = blackGrad;
      ctx.fillRect(0, 0, w, h);
    }
  }

  if (svCursor) {
    svCursor.style.left = `${Math.max(0, Math.min(100, colorState.s))}%`;
    svCursor.style.top = `${Math.max(0, Math.min(100, 100 - colorState.v))}%`;
  }
  if (hueCursor) {
    hueCursor.style.left = `${(Math.max(0, Math.min(360, colorState.h)) / 360) * 100}%`;
  }
}

function syncColorPanelUI({ skipHexInput = false } = {}) {
  const rEl = document.getElementById('unit-color-r');
  const gEl = document.getElementById('unit-color-g');
  const bEl = document.getElementById('unit-color-b');
  const vEl = document.getElementById('unit-color-v');
  const sEl = document.getElementById('unit-color-s');
  const rVal = document.getElementById('unit-color-r-val');
  const gVal = document.getElementById('unit-color-g-val');
  const bVal = document.getElementById('unit-color-b-val');
  const vVal = document.getElementById('unit-color-v-val');
  const sVal = document.getElementById('unit-color-s-val');
  const hexInput = document.getElementById('unit-color-hex-input');
  const preview = document.getElementById('unit-color-preview');
  const nativePicker = document.getElementById('unit-color-native-picker');

  if (rEl) rEl.value = String(colorState.r);
  if (gEl) gEl.value = String(colorState.g);
  if (bEl) bEl.value = String(colorState.b);
  if (vEl) vEl.value = String(colorState.v);
  if (sEl) sEl.value = String(colorState.s);

  if (rVal) rVal.textContent = String(colorState.r);
  if (gVal) gVal.textContent = String(colorState.g);
  if (bVal) bVal.textContent = String(colorState.b);
  if (vVal) vVal.textContent = `${colorState.v}%`;
  if (sVal) sVal.textContent = `${colorState.s}%`;

  if (hexInput && !skipHexInput) {
    hexInput.value = colorState.hex;
  }
  if (preview) {
    preview.style.backgroundColor = colorState.hex;
  }
  if (nativePicker) {
    nativePicker.value = colorState.hex.toLowerCase();
  }

  if (editingUnit && activeColorChannel) {
    if (activeColorChannel === 'flash') {
      editingUnit.flash_color = colorState.hex;
    } else {
      editingUnit.light_color = colorState.hex;
    }
    updateChannelBadge(activeColorChannel === 'flash', editingUnit);
  }

  drawColorCanvases();
}

function setColorStateFromRgb(r, g, b, options = {}) {
  const clamp255 = (n) => Math.max(0, Math.min(255, Math.round(Number(n) || 0)));
  colorState.r = clamp255(r);
  colorState.g = clamp255(g);
  colorState.b = clamp255(b);
  colorState.hex = rgbToHex(colorState.r, colorState.g, colorState.b);
  const hsv = rgbToHsv(colorState.r, colorState.g, colorState.b);
  if (hsv.s > 0 && hsv.v > 0) {
    colorState.h = hsv.h;
  }
  colorState.s = hsv.s;
  colorState.v = hsv.v;
  syncColorPanelUI(options);
}

function setColorStateFromHsv(h, s, v) {
  colorState.h = Math.max(0, Math.min(360, Math.round(Number(h) || 0)));
  colorState.s = Math.max(0, Math.min(100, Math.round(Number(s) || 0)));
  colorState.v = Math.max(0, Math.min(100, Math.round(Number(v) || 0)));
  const rgb = hsvToRgb(colorState.h, colorState.s, colorState.v);
  colorState.r = rgb.r;
  colorState.g = rgb.g;
  colorState.b = rgb.b;
  colorState.hex = rgbToHex(rgb.r, rgb.g, rgb.b);
  syncColorPanelUI();
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
  setColorStateFromRgb(rgb.r, rgb.g, rgb.b);

  if (panel) {
    panel.classList.remove('hidden');
    requestAnimationFrame(() => {
      panel.classList.add('is-open');
      drawColorCanvases();
    });
  }
  updateChannelBadge(false, editingUnit);
  updateChannelBadge(true, editingUnit);
}

function syncUnitLightControls(unit) {
  const lightCard = document.getElementById('unit-editor-light-card');
  const flashCard = document.getElementById('unit-editor-flash-card');
  const lightToggle = document.getElementById('unit-editor-light-toggle');
  const flashToggle = document.getElementById('unit-editor-flash-toggle');
  const lightLockedEl = document.getElementById('unit-editor-light-locked');
  const flashLockedEl = document.getElementById('unit-editor-flash-locked');

  const visible = Boolean(unit) && currentRole === 'developer';
  if (lightCard) lightCard.classList.toggle('hidden', !visible);
  if (flashCard) flashCard.classList.toggle('hidden', !visible);

  if (!visible) {
    if (lightLockedEl) lightLockedEl.classList.add('hidden');
    if (flashLockedEl) flashLockedEl.classList.add('hidden');
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

  const lightLocked =
    Boolean(unit.light_locked) ||
    (unit.global_light_mode !== undefined && unit.global_light_mode !== 'default');
  const flashLocked =
    Boolean(unit.flash_locked) ||
    (unit.global_flash_mode !== undefined && unit.global_flash_mode !== 'default');

  if (lightToggle) {
    lightToggle.checked = indLight;
    lightToggle.disabled = lightLocked || isArchived;
  }
  if (lightLockedEl) {
    lightLockedEl.classList.toggle('hidden', !lightLocked);
  }

  if (flashToggle) {
    flashToggle.checked = indFlash;
    flashToggle.disabled = flashLocked || isArchived;
  }
  if (flashLockedEl) {
    flashLockedEl.classList.toggle('hidden', !flashLocked);
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

async function openUnitModal(unit = null) {
  if (currentRole === 'guest') return;
  if (!unit && currentRole !== 'developer') return;

  const modal = document.getElementById('modal-unit-editor');
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

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

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
  unitEditorOpenSeq += 1;
  closeColorPanel();
  const modal = document.getElementById('modal-unit-editor');
  if (modal) {
    modal.classList.add('hidden');
  }
  document.body.classList.remove('modal-open');
  editingUnit = null;
  initialCameraIds = [];
  isBusy = false;
  showEditorError('');
  hideDeleteConfirm();
  syncUnitLightControls(null);
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
  const rEl = document.getElementById('unit-color-r');
  const gEl = document.getElementById('unit-color-g');
  const bEl = document.getElementById('unit-color-b');
  const vEl = document.getElementById('unit-color-v');
  const sEl = document.getElementById('unit-color-s');
  const hexInput = document.getElementById('unit-color-hex-input');
  const nativePicker = document.getElementById('unit-color-native-picker');
  const svCanvas = document.getElementById('unit-color-sv-canvas');
  const hueCanvas = document.getElementById('unit-color-hue-canvas');

  if (closeBtn) {
    closeBtn.addEventListener('click', closeColorPanel);
  }

  const onRgbSlider = () => {
    setColorStateFromRgb(
      rEl ? rEl.value : colorState.r,
      gEl ? gEl.value : colorState.g,
      bEl ? bEl.value : colorState.b
    );
    if (activeColorChannel) {
      scheduleColorApiSend(activeColorChannel, colorState.hex);
    }
  };
  [rEl, gEl, bEl].forEach((el) => {
    if (el) el.addEventListener('input', onRgbSlider);
  });

  const onHsvSlider = () => {
    setColorStateFromHsv(
      colorState.h,
      sEl ? sEl.value : colorState.s,
      vEl ? vEl.value : colorState.v
    );
    if (activeColorChannel) {
      scheduleColorApiSend(activeColorChannel, colorState.hex);
    }
  };
  [vEl, sEl].forEach((el) => {
    if (el) el.addEventListener('input', onHsvSlider);
  });

  if (hexInput) {
    const handleHexChange = (immediate) => {
      const norm = normalizeHexColor(hexInput.value);
      if (!norm) return;
      const rgb = hexToRgb(norm);
      if (!rgb) return;
      setColorStateFromRgb(rgb.r, rgb.g, rgb.b, { skipHexInput: true });
      if (immediate) {
        hexInput.value = norm;
      }
      if (activeColorChannel) {
        scheduleColorApiSend(activeColorChannel, norm, { immediate });
      }
    };
    hexInput.addEventListener('input', () => handleHexChange(false));
    hexInput.addEventListener('change', () => handleHexChange(true));
  }

  if (nativePicker) {
    nativePicker.addEventListener('input', () => {
      const norm = normalizeHexColor(nativePicker.value);
      if (!norm) return;
      const rgb = hexToRgb(norm);
      if (!rgb) return;
      setColorStateFromRgb(rgb.r, rgb.g, rgb.b);
      if (activeColorChannel) {
        scheduleColorApiSend(activeColorChannel, norm);
      }
    });
  }

  const bindDragCanvas = (canvasEl, handler) => {
    if (!canvasEl) return;
    let dragging = false;
    const handlePointer = (ev) => {
      const rect = canvasEl.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const x = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      const y = Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height));
      handler(x, y);
      if (activeColorChannel) {
        scheduleColorApiSend(activeColorChannel, colorState.hex);
      }
    };
    canvasEl.addEventListener('mousedown', (ev) => {
      dragging = true;
      handlePointer(ev);
    });
    window.addEventListener('mousemove', (ev) => {
      if (dragging) handlePointer(ev);
    });
    window.addEventListener('mouseup', () => {
      dragging = false;
    });
    canvasEl.addEventListener(
      'touchstart',
      (ev) => {
        if (ev.touches && ev.touches[0]) {
          dragging = true;
          handlePointer(ev.touches[0]);
        }
      },
      { passive: true }
    );
    window.addEventListener(
      'touchmove',
      (ev) => {
        if (dragging && ev.touches && ev.touches[0]) {
          handlePointer(ev.touches[0]);
        }
      },
      { passive: true }
    );
    window.addEventListener('touchend', () => {
      dragging = false;
    });
  };

  bindDragCanvas(svCanvas, (x, y) => {
    setColorStateFromHsv(colorState.h, x * 100, (1 - y) * 100);
  });
  bindDragCanvas(hueCanvas, (x) => {
    setColorStateFromHsv(x * 360, colorState.s, colorState.v);
  });
}

export function initUnitEditor() {
  const modal = document.getElementById('modal-unit-editor');
  const closeBtn = document.getElementById('modal-unit-editor-close');
  const cancelBtn = document.getElementById('unit-editor-cancel-btn');
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

  if (closeBtn) closeBtn.addEventListener('click', closeUnitEditorModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeUnitEditorModal);
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeUnitEditorModal();
    });
  }

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

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
      e.preventDefault();
      closeUnitEditorModal();
    }
  });
}
