// clients/web/js/cameras/camera_v4l2.js
// Отрисовка и управление V4L2-настройками камеры в Live-окне

import { api } from '../api.js';
import { showError, showToast } from '../dialogs.js';
import { clampRgbValue } from './camera_color_matrix.js';

export const CATEGORY_DEFS = [
  {
    id: 'format',
    title: 'Разрешение и формат',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="2" y="4" width="20" height="16" rx="2"/>
      <path d="M8 9H6v2M16 15h2v-2"/>
    </svg>`,
  },
  {
    id: 'exposure',
    title: 'Экспозиция и яркость',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="4"/>
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>
    </svg>`,
  },
  {
    id: 'color',
    title: 'Цвет и баланс белого',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>
    </svg>`,
  },
  {
    id: 'focus',
    title: 'Фокус и оптика',
    icon: `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>
    </svg>`,
  },
];

export const DEPENDENT_TRIGGERS = new Set([
  'white_balance_automatic',
  'auto_exposure',
  'focus_automatic_continuous',
  'focus_auto',
]);

const RESET_ICON_SVG = `<svg class="icon-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="1 4 1 10 7 10"/>
  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>
</svg>`;

// Прокрутка горизонтального контейнера колёсиком мыши при наведении
function attachHorizontalWheelScroll(el) {
  if (!el) return;
  el.addEventListener(
    'wheel',
    (e) => {
      if (el.scrollWidth > el.clientWidth && e.deltaY !== 0) {
        e.preventDefault();
        el.scrollLeft += e.deltaY;
      }
    },
    { passive: false }
  );
}

function createControlPill(labelText, isDisabled = false, extraClass = '') {
  const pill = document.createElement('div');
  pill.className = `live-ctrl-pill ${isDisabled ? 'is-disabled' : ''} ${extraClass}`.trim();
  if (labelText) {
    const head = document.createElement('div');
    head.className = 'live-pill-head';
    head.innerHTML = `<span class="live-pill-label">${labelText}</span>`;
    pill.appendChild(head);
  }
  return pill;
}

function getResolutionsForFormat(formats, fmt) {
  const matched = (formats || []).find((f) => f.format === fmt);
  if (matched && Array.isArray(matched.resolutions) && matched.resolutions.length > 0) {
    return matched.resolutions;
  }
  const all = [];
  (formats || []).forEach((f) => {
    (f.resolutions || []).forEach((r) => {
      if (!all.includes(r)) all.push(r);
    });
  });
  return all;
}

function isBinaryControl(ctrl) {
  if (!ctrl) return false;
  if (ctrl.type === 'bool') return true;
  if (ctrl.type === 'int' || ctrl.type === 'int64') {
    const minV = Number(ctrl.min);
    const maxV = Number(ctrl.max);
    const stepV = ctrl.step !== undefined && ctrl.step !== null ? Number(ctrl.step) : 1;
    return minV === 0 && maxV === 1 && stepV === 1;
  }
  return false;
}

function formatControlValue(ctrlName, rawVal) {
  const num = Number(rawVal);
  if (ctrlName === 'exposure_time_absolute' && Number.isFinite(num)) {
    const ms = Number((num * 0.1).toFixed(1));
    return `${ms} мс`;
  }
  if (ctrlName === 'white_balance_temperature' && Number.isFinite(num)) {
    return `${Math.round(num)} К`;
  }
  return String(rawVal);
}

function formatMenuItemLabel(ctrlName, label) {
  const raw = String(label || '').trim();
  if (ctrlName === 'power_line_frequency') {
    if (/disabled|off|откл/i.test(raw)) return 'Откл';
    return raw.replace(/\bHz\b/gi, 'Гц');
  }
  if (ctrlName === 'auto_exposure') {
    if (/aperture\s*priority/i.test(raw)) return 'Приоритет';
    if (/manual/i.test(raw)) return 'Ручной';
    if (/auto/i.test(raw)) return 'Авто';
  }
  if (ctrlName === 'led1_mode') {
    if (/^off$/i.test(raw)) return 'Выкл';
    if (/^on$/i.test(raw)) return 'Вкл';
    if (/blink/i.test(raw)) return 'Мигание';
    if (/auto/i.test(raw)) return 'Авто';
  }
  return raw;
}

// Человекочитаемые подписи контролов
export function formatControlTitle(name) {
  const titles = {
    brightness: 'Яркость',
    contrast: 'Контрастность',
    saturation: 'Насыщенность',
    hue: 'Цветовой тон',
    white_balance_automatic: 'Автобаланс белого',
    white_balance_temperature: 'Температура ББ',
    auto_exposure: 'Режим экспозиции',
    exposure_time_absolute: 'Выдержка',
    exposure_dynamic_framerate: 'Динам. FPS',
    backlight_compensation: 'Компенс. засветки',
    gain: 'Усиление (Gain)',
    focus_automatic_continuous: 'Автофокус',
    focus_auto: 'Автофокус',
    focus_absolute: 'Ручной фокус',
    sharpness: 'Резкость',
    zoom_absolute: 'Зум',
    power_line_frequency: 'Частота сети',
    led1_mode: 'Индикатор',
  };
  return titles[name] || name;
}

function createSliderWithDefault({
  min,
  max,
  step,
  value,
  defaultVal,
  disabled,
  ctrlName,
  formatValFn,
  onInputVal,
  onCommitVal,
}) {
  const wrap = document.createElement('div');
  wrap.className = 'live-slider-wrap';

  const slider = document.createElement('input');
  slider.type = 'range';
  slider.className = 'live-pill-slider';
  if (ctrlName) slider.dataset.cname = ctrlName;
  slider.min = String(min);
  slider.max = String(max);
  slider.step = String(step);
  slider.value = String(value);
  if (disabled) slider.disabled = true;

  wrap.appendChild(slider);

  const hasDefault = defaultVal !== null && defaultVal !== undefined && Number.isFinite(Number(defaultVal));
  if (hasDefault && max > min) {
    const defNum = Math.max(min, Math.min(max, Number(defaultVal)));
    const ratio = (defNum - min) / (max - min);
    const mark = document.createElement('span');
    mark.className = 'live-slider-default-mark';
    mark.style.left = `calc(${(ratio * 100).toFixed(2)}% + ${(8 - ratio * 16).toFixed(2)}px)`;
    mark.title = `По умолчанию: ${formatValFn ? formatValFn(defNum) : defNum}`;
    wrap.appendChild(mark);
  }

  slider.addEventListener('input', (e) => {
    if (onInputVal) onInputVal(Number(e.target.value));
  });
  slider.addEventListener('change', (e) => {
    if (onCommitVal) onCommitVal(Number(e.target.value));
  });

  if (hasDefault && !disabled) {
    slider.addEventListener('dblclick', () => {
      const defNum = Math.max(min, Math.min(max, Number(defaultVal)));
      slider.value = String(defNum);
      if (onInputVal) onInputVal(defNum);
      if (onCommitVal) onCommitVal(defNum);
    });
  }

  return { wrap, slider };
}

function appendResetButton(head, defaultVal, disabled, onReset) {
  if (defaultVal === null || defaultVal === undefined || !Number.isFinite(Number(defaultVal))) {
    return null;
  }
  const resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.className = 'live-pill-reset-btn';
  resetBtn.title = 'Сбросить на значение по умолчанию';
  resetBtn.setAttribute('aria-label', 'Сбросить на значение по умолчанию');
  resetBtn.innerHTML = RESET_ICON_SVG;
  if (disabled) resetBtn.disabled = true;
  resetBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!resetBtn.disabled) onReset();
  });
  head.appendChild(resetBtn);
  return resetBtn;
}

export function createV4l2Controller({
  getActiveCamera,
  colorMatrix,
  onControlsLoaded,
}) {
  let currentCameraControls = null;
  let currentActiveTab = null;

  function getControls() {
    return currentCameraControls;
  }

  function reset() {
    currentActiveTab = null;
    currentCameraControls = null;
  }

  // Отправка изменения v4l2 контрола на сервер и синхронизация зависимых контролов
  async function sendControlChange(ctrlName, value) {
    const activeCamera = getActiveCamera();
    if (!activeCamera || !activeCamera.id) return;
    const cameraId = activeCamera.id;
    try {
      const patchRes = await api.updateCameraControls(cameraId, {
        controls: { [ctrlName]: value },
      });
      if (currentCameraControls && currentCameraControls.controls) {
        if (patchRes && Array.isArray(patchRes.controls)) {
          currentCameraControls.controls = patchRes.controls;
        } else {
          const c = currentCameraControls.controls.find((item) => item.name === ctrlName);
          if (c) c.value = value;
        }
        if (DEPENDENT_TRIGGERS.has(ctrlName)) {
          renderCategoryPills();
        }
      }

      // Для зависимых контролов запрашиваем обновлённое реальное состояние с камеры
      if (DEPENDENT_TRIGGERS.has(ctrlName)) {
        const refreshed = await api.getCameraControls(cameraId, { refresh: true });
        const curCam = getActiveCamera();
        if (curCam && curCam.id === cameraId && refreshed) {
          currentCameraControls = refreshed;
          renderCategoryPills();
        }
      }
    } catch (err) {
      showError(err.message || 'Ошибка применения контрола камеры');
    }
  }

  function appendV4L2ControlPills(track, controlsList, allowEmpty = false) {
    if (!controlsList || controlsList.length === 0) {
      if (!allowEmpty) {
        const emptyPill = createControlPill('Настройки', true);
        const span = document.createElement('span');
        span.className = 'live-pill-static';
        span.textContent = 'Не поддерживаются камерой';
        emptyPill.appendChild(span);
        track.appendChild(emptyPill);
      }
      return;
    }

    controlsList.forEach((ctrl) => {
      const isInactive = Boolean(ctrl.is_inactive);
      const labelText = formatControlTitle(ctrl.name);

      if (isBinaryControl(ctrl)) {
        const pill = createControlPill(labelText, isInactive, 'live-pill-toggle');
        const isChecked = Boolean(Number(ctrl.value));

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.role = 'switch';
        toggleBtn.setAttribute('aria-checked', isChecked ? 'true' : 'false');
        toggleBtn.dataset.cname = ctrl.name;
        toggleBtn.className = `live-toggle-btn ${isChecked ? 'is-on' : ''}`;
        if (isInactive) toggleBtn.disabled = true;
        toggleBtn.innerHTML = `
          <span class="live-toggle-track"><span class="live-toggle-thumb"></span></span>
          <span class="live-toggle-text">${isChecked ? 'Вкл' : 'Выкл'}</span>
        `;

        toggleBtn.addEventListener('click', async () => {
          if (toggleBtn.disabled) return;
          const nextChecked = toggleBtn.getAttribute('aria-checked') !== 'true';
          const val = nextChecked ? 1 : 0;
          toggleBtn.setAttribute('aria-checked', nextChecked ? 'true' : 'false');
          toggleBtn.classList.toggle('is-on', nextChecked);
          const txt = toggleBtn.querySelector('.live-toggle-text');
          if (txt) txt.textContent = nextChecked ? 'Вкл' : 'Выкл';
          await sendControlChange(ctrl.name, val);
        });

        pill.appendChild(toggleBtn);
        track.appendChild(pill);
      } else if (ctrl.type === 'menu' && ctrl.menu_items && ctrl.menu_items.length > 0) {
        if (ctrl.menu_items.length <= 3) {
          const pill = createControlPill(labelText, isInactive, 'live-pill-wide');
          const seg = document.createElement('div');
          seg.className = 'segmented-control live-pill-segmented';
          seg.dataset.cname = ctrl.name;

          ctrl.menu_items.forEach((m) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `seg-btn ${m.id === ctrl.value ? 'active' : ''}`;
            btn.dataset.value = String(m.id);
            const shortLabel = formatMenuItemLabel(ctrl.name, m.label);
            btn.textContent = shortLabel;
            btn.title = m.label;
            if (isInactive) btn.disabled = true;
            btn.addEventListener('click', async () => {
              if (isInactive || m.id === ctrl.value) return;
              seg.querySelectorAll('.seg-btn').forEach((b) => b.classList.remove('active'));
              btn.classList.add('active');
              await sendControlChange(ctrl.name, Number(m.id));
            });
            seg.appendChild(btn);
          });

          pill.appendChild(seg);
          track.appendChild(pill);
        } else {
          const pill = createControlPill(labelText, isInactive);
          const sel = document.createElement('select');
          sel.className = 'live-pill-select';
          sel.dataset.cname = ctrl.name;
          if (isInactive) sel.disabled = true;
          sel.innerHTML = ctrl.menu_items
            .map(
              (m) =>
                `<option value="${m.id}" ${m.id === ctrl.value ? 'selected' : ''}>${formatMenuItemLabel(ctrl.name, m.label)}</option>`
            )
            .join('');
          sel.addEventListener('change', async (e) => {
            await sendControlChange(ctrl.name, parseInt(e.target.value, 10));
          });
          pill.appendChild(sel);
          track.appendChild(pill);
        }
      } else {
        const minVal = ctrl.min !== null && ctrl.min !== undefined ? Number(ctrl.min) : 0;
        const maxVal = ctrl.max !== null && ctrl.max !== undefined ? Number(ctrl.max) : 100;
        const stepVal = ctrl.step ? Number(ctrl.step) : 1;
        const curVal = ctrl.value !== null && ctrl.value !== undefined ? Number(ctrl.value) : minVal;
        const defVal = ctrl.default !== null && ctrl.default !== undefined ? Number(ctrl.default) : null;

        const pill = createControlPill(labelText, isInactive);
        const head = pill.querySelector('.live-pill-head');

        const headRight = document.createElement('div');
        headRight.className = 'live-pill-head-right';

        const valDisplay = document.createElement('span');
        valDisplay.className = 'live-pill-val';
        valDisplay.id = `val-${ctrl.name}`;
        valDisplay.textContent = formatControlValue(ctrl.name, curVal);
        headRight.appendChild(valDisplay);

        const { wrap, slider } = createSliderWithDefault({
          min: minVal,
          max: maxVal,
          step: stepVal,
          value: curVal,
          defaultVal: defVal,
          disabled: isInactive,
          ctrlName: ctrl.name,
          formatValFn: (v) => formatControlValue(ctrl.name, v),
          onInputVal: (val) => {
            valDisplay.textContent = formatControlValue(ctrl.name, val);
          },
          onCommitVal: async (val) => {
            valDisplay.textContent = formatControlValue(ctrl.name, val);
            await sendControlChange(ctrl.name, Math.round(val));
          },
        });

        appendResetButton(headRight, defVal, isInactive, async () => {
          const clamped = Math.max(minVal, Math.min(maxVal, defVal));
          slider.value = String(clamped);
          valDisplay.textContent = formatControlValue(ctrl.name, clamped);
          await sendControlChange(ctrl.name, Math.round(clamped));
        });

        head.appendChild(headRight);
        pill.appendChild(wrap);
        track.appendChild(pill);
      }
    });
  }

  // Отрисовка компактных плашек настроек выбранной категории в одну строку
  function renderCategoryPills() {
    const track = document.getElementById('live-pills-track');
    if (!track || !currentActiveTab) return;

    track.innerHTML = '';

    if (!currentCameraControls) {
      const pill = createControlPill('Параметры камеры', true);
      const note = document.createElement('span');
      note.className = 'live-pill-static';
      note.textContent = 'Загрузка...';
      pill.appendChild(note);
      track.appendChild(pill);
      return;
    }

    const activeCamera = getActiveCamera();
    const { formats, controls, current_format, current_resolution } = currentCameraControls;

    if (currentActiveTab === 'format') {
      const availFormats = (formats || []).filter((f) => f && f.format);
      const activeFmt = current_format || (availFormats[0] && availFormats[0].format) || '';
      const resolutions = getResolutionsForFormat(availFormats, activeFmt);

      // Плашка: Разрешение
      const resPill = createControlPill('Разрешение', resolutions.length <= 1);
      if (resolutions.length === 0) {
        const emptyEl = document.createElement('span');
        emptyEl.className = 'live-pill-static';
        emptyEl.textContent = current_resolution || 'Неизвестно';
        resPill.appendChild(emptyEl);
      } else {
        const selRes = document.createElement('select');
        selRes.id = 'ctrl-select-res';
        selRes.className = 'live-pill-select';
        if (resolutions.length <= 1) selRes.disabled = true;
        selRes.innerHTML = resolutions
          .map(
            (r) => `<option value="${r}" ${r === current_resolution ? 'selected' : ''}>${r.replace('x', '\u00d7')}</option>`
          )
          .join('');
        selRes.addEventListener('change', async (e) => {
          const newRes = e.target.value;
          try {
            await api.updateCameraControls(activeCamera.id, { resolution: newRes });
            currentCameraControls.current_resolution = newRes;
            const resEl = document.getElementById('live-res');
            if (resEl) resEl.textContent = newRes;
            showToast(`Разрешение: ${newRes.replace('x', '\u00d7')}`);
          } catch (err) {
            showError(err.message || 'Ошибка применения разрешения');
          }
        });
        resPill.appendChild(selRes);
      }
      track.appendChild(resPill);

      // Плашка: Формат / Кодек
      const fmtPill = createControlPill('Формат', availFormats.length <= 1);
      const applyFormatSelection = async (newFmt) => {
        const fmtResList = getResolutionsForFormat(availFormats, newFmt);
        const nextRes = currentCameraControls.current_resolution;
        const payload = { format: newFmt };
        if (fmtResList.length > 0 && !fmtResList.includes(nextRes)) {
          payload.resolution = fmtResList[0];
        }
        try {
          await api.updateCameraControls(activeCamera.id, payload);
          currentCameraControls.current_format = newFmt;
          if (payload.resolution) {
            currentCameraControls.current_resolution = payload.resolution;
            const resEl = document.getElementById('live-res');
            if (resEl) resEl.textContent = payload.resolution;
          }
          renderCategoryPills();
          showToast(`Формат: ${newFmt}`);
        } catch (err) {
          showError(err.message || 'Ошибка применения формата');
        }
      };

      if (availFormats.length === 0) {
        const emptyEl = document.createElement('span');
        emptyEl.className = 'live-pill-static';
        emptyEl.textContent = activeFmt || 'Неизвестно';
        fmtPill.appendChild(emptyEl);
      } else if (availFormats.length <= 3) {
        const seg = document.createElement('div');
        seg.className = 'segmented-control live-pill-segmented';
        availFormats.forEach((f) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = `seg-btn ${f.format === activeFmt ? 'active' : ''}`;
          btn.textContent = f.format;
          if (availFormats.length <= 1) btn.disabled = true;
          btn.addEventListener('click', () => {
            if (f.format === currentCameraControls.current_format) return;
            applyFormatSelection(f.format);
          });
          seg.appendChild(btn);
        });
        fmtPill.appendChild(seg);
      } else {
        const selFmt = document.createElement('select');
        selFmt.id = 'ctrl-select-fmt';
        selFmt.className = 'live-pill-select';
        selFmt.innerHTML = availFormats
          .map(
            (f) => `<option value="${f.format}" ${f.format === activeFmt ? 'selected' : ''}>${f.format}</option>`
          )
          .join('');
        selFmt.addEventListener('change', (e) => applyFormatSelection(e.target.value));
        fmtPill.appendChild(selFmt);
      }
      track.appendChild(fmtPill);
      return;
    }

    if (currentActiveTab === 'exposure') {
      const exposureControls = (controls || []).filter((c) => c.category === 'exposure');
      appendV4L2ControlPills(track, exposureControls);
      return;
    }

    if (currentActiveTab === 'color') {
      const colorControls = (controls || []).filter((c) => c.category === 'color');
      appendV4L2ControlPills(track, colorControls, true);

      // Компактные плашки программной RGB-коррекции (0.00 - 1.00, шаг 0.01, default 1.00)
      const draftRgb = colorMatrix.getDraftRgb();
      const rgbChannels = [
        { id: 'r', title: 'Канал R', value: clampRgbValue(draftRgb.r, 1.0), accentClass: 'rgb-r' },
        { id: 'g', title: 'Канал G', value: clampRgbValue(draftRgb.g, 1.0), accentClass: 'rgb-g' },
        { id: 'b', title: 'Канал B', value: clampRgbValue(draftRgb.b, 1.0), accentClass: 'rgb-b' },
      ];

      rgbChannels.forEach((ch) => {
        const pill = createControlPill(`<span class="${ch.accentClass}">${ch.title}</span>`);
        const head = pill.querySelector('.live-pill-head');

        const headRight = document.createElement('div');
        headRight.className = 'live-pill-head-right';

        const valSpan = document.createElement('span');
        valSpan.className = 'live-pill-val';
        valSpan.id = `val-rgb-${ch.id}`;
        valSpan.textContent = ch.value.toFixed(2);
        headRight.appendChild(valSpan);

        const { wrap, slider } = createSliderWithDefault({
          min: 0.0,
          max: 1.0,
          step: 0.01,
          value: ch.value,
          defaultVal: 1.0,
          disabled: false,
          ctrlName: `rgb_${ch.id}`,
          formatValFn: (v) => Number(v).toFixed(2),
          onInputVal: (val) => {
            const clamped = clampRgbValue(val, 1.0);
            colorMatrix.setDraftChannel(ch.id, clamped);
            valSpan.textContent = clamped.toFixed(2);
            colorMatrix.scheduleLiveRgbSync(false);
          },
          onCommitVal: (val) => {
            const clamped = clampRgbValue(val, 1.0);
            colorMatrix.setDraftChannel(ch.id, clamped);
            valSpan.textContent = clamped.toFixed(2);
            colorMatrix.scheduleLiveRgbSync(true);
          },
        });
        slider.id = `slider-rgb-${ch.id}`;

        appendResetButton(headRight, 1.0, false, () => {
          slider.value = '1';
          colorMatrix.setDraftChannel(ch.id, 1.0);
          valSpan.textContent = '1.00';
          colorMatrix.scheduleLiveRgbSync(true);
        });

        head.appendChild(headRight);
        pill.appendChild(wrap);
        track.appendChild(pill);
      });

      // Плашка: Автоцветокоррекция по цветовому маркеру
      const autoColorPill = createControlPill('Калибровка цвета', false, 'live-pill-action');
      const autoColorBtn = document.createElement('button');
      autoColorBtn.type = 'button';
      autoColorBtn.id = 'btn-live-auto-color';
      autoColorBtn.className = 'btn btn-primary btn-compact live-auto-color-btn';
      autoColorBtn.textContent = colorMatrix.isCalibrating() ? 'Калибровка...' : 'Автоцветокоррекция';
      autoColorBtn.disabled = !colorMatrix.isMarkerDetected() || colorMatrix.isCalibrating();
      autoColorBtn.title = 'Выровнять RGB по цветовому маркеру в кадре';
      autoColorBtn.addEventListener('click', colorMatrix.handleAutoColorCalibrate);
      autoColorPill.appendChild(autoColorBtn);
      track.appendChild(autoColorPill);
      return;
    }

    if (currentActiveTab === 'focus') {
      const focusControls = (controls || []).filter((c) => c.category === 'focus');
      const hasFocusCtrl = focusControls.some(
        (c) =>
          c.name === 'focus_automatic_continuous' ||
          c.name === 'focus_auto' ||
          c.name === 'focus_absolute'
      );

      if (!hasFocusCtrl) {
        const fixedPill = createControlPill('Фокусировка', true);
        const staticVal = document.createElement('input');
        staticVal.type = 'text';
        staticVal.className = 'live-pill-static-input';
        staticVal.value = 'Фиксированный фокус';
        staticVal.disabled = true;
        fixedPill.appendChild(staticVal);
        track.appendChild(fixedPill);
      }

      appendV4L2ControlPills(track, focusControls, !hasFocusCtrl);
    }
  }

  // Отрисовка ряда квадратных кнопок категорий и выпадающей строки плашек
  function renderLiveSettingsOverlay() {
    const container = document.getElementById('live-settings-content');
    if (!container) return;

    const buttonsHtml = CATEGORY_DEFS.map((cat) => {
      const isActive = currentActiveTab === cat.id;
      return `
        <button type="button"
          class="live-cat-btn cat-${cat.id} ${isActive ? 'active' : ''}"
          data-tab="${cat.id}"
          title="${cat.title}"
          aria-label="${cat.title}"
          aria-pressed="${isActive ? 'true' : 'false'}">
          ${cat.icon}
        </button>
      `;
    }).join('');

    container.innerHTML = `
      <div class="live-cat-bar" id="live-cat-bar">
        <div class="live-cat-track">${buttonsHtml}</div>
      </div>
      <div class="live-pills-bar ${currentActiveTab ? '' : 'hidden'}" id="live-pills-bar">
        <div class="live-pills-track" id="live-pills-track"></div>
      </div>
    `;

    const catBar = container.querySelector('#live-cat-bar');
    const pillsBar = container.querySelector('#live-pills-bar');
    attachHorizontalWheelScroll(catBar);
    attachHorizontalWheelScroll(pillsBar);

    container.querySelectorAll('.live-cat-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        // Повторный клик по активной кнопке скрывает плашки
        currentActiveTab = currentActiveTab === tab ? null : tab;
        container.querySelectorAll('.live-cat-btn').forEach((b) => {
          const active = b.dataset.tab === currentActiveTab;
          b.classList.toggle('active', active);
          b.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        if (pillsBar) {
          pillsBar.classList.toggle('hidden', !currentActiveTab);
        }
        renderCategoryPills();
      });
    });

    if (currentActiveTab) {
      renderCategoryPills();
    }
  }

  // Загрузка доступных контролов камеры и отрисовка панели
  async function loadCameraControls(cameraId, refresh = false) {
    try {
      const data = await api.getCameraControls(cameraId, { refresh });
      const activeCamera = getActiveCamera();
      if (!activeCamera || activeCamera.id !== cameraId) return;
      currentCameraControls = data;

      if (typeof onControlsLoaded === 'function') {
        onControlsLoaded(data);
      }

      const resEl = document.getElementById('live-res');
      if (resEl && data && data.current_resolution && (!resEl.textContent || resEl.textContent === '\u2014')) {
        resEl.textContent = data.current_resolution;
      }
      renderLiveSettingsOverlay();
    } catch (_) {
      renderLiveSettingsOverlay();
    }
  }

  return {
    getControls,
    reset,
    renderLiveSettingsOverlay,
    renderCategoryPills,
    loadCameraControls,
  };
}
