// clients/web/js/components/color_picker.js
// Переиспользуемый Canvas-компонент выбора цвета (SV-палитра, Hue-шкала, ползунки RGB/HSV, HEX и системная палитра)

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

function resolveEl(ref) {
  if (!ref) return null;
  if (typeof ref === 'string') {
    return typeof document !== 'undefined' ? document.getElementById(ref) : null;
  }
  return ref;
}

/**
 * Переиспользуемый контроллер Canvas-палитры и синхронизированных RGB/HSV/HEX элементов.
 * Подвешивает mousemove/touchmove на window только на время активного перетаскивания.
 */
export class ColorPicker {
  constructor(config = {}) {
    this.elements = {
      svCanvas: config.svCanvas || null,
      hueCanvas: config.hueCanvas || null,
      svCursor: config.svCursor || null,
      hueCursor: config.hueCursor || null,
      rInput: config.rInput || null,
      gInput: config.gInput || null,
      bInput: config.bInput || null,
      vInput: config.vInput || null,
      sInput: config.sInput || null,
      rVal: config.rVal || null,
      gVal: config.gVal || null,
      bVal: config.bVal || null,
      vVal: config.vVal || null,
      sVal: config.sVal || null,
      hexInput: config.hexInput || null,
      preview: config.preview || null,
      nativePicker: config.nativePicker || null,
    };

    this.onChange = typeof config.onChange === 'function' ? config.onChange : null;
    this.onCommit = typeof config.onCommit === 'function' ? config.onCommit : null;

    this.state = {
      h: 0,
      s: 0,
      v: 100,
      r: 255,
      g: 255,
      b: 255,
      hex: '#FFFFFF',
    };

    this._cleanups = [];
    this._bound = false;
  }

  _el(key) {
    return resolveEl(this.elements[key]);
  }

  getState() {
    return { ...this.state };
  }

  drawCanvases() {
    const svCanvas = this._el('svCanvas');
    const hueCanvas = this._el('hueCanvas');
    const svCursor = this._el('svCursor');
    const hueCursor = this._el('hueCursor');

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
        const pure = hsvToRgb(this.state.h, 100, 100);
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
      svCursor.style.left = `${Math.max(0, Math.min(100, this.state.s))}%`;
      svCursor.style.top = `${Math.max(0, Math.min(100, 100 - this.state.v))}%`;
    }
    if (hueCursor) {
      hueCursor.style.left = `${(Math.max(0, Math.min(360, this.state.h)) / 360) * 100}%`;
    }
  }

  syncUI({ skipHexInput = false } = {}) {
    const rEl = this._el('rInput');
    const gEl = this._el('gInput');
    const bEl = this._el('bInput');
    const vEl = this._el('vInput');
    const sEl = this._el('sInput');
    const rVal = this._el('rVal');
    const gVal = this._el('gVal');
    const bVal = this._el('bVal');
    const vVal = this._el('vVal');
    const sVal = this._el('sVal');
    const hexInput = this._el('hexInput');
    const preview = this._el('preview');
    const nativePicker = this._el('nativePicker');

    if (rEl) rEl.value = String(this.state.r);
    if (gEl) gEl.value = String(this.state.g);
    if (bEl) bEl.value = String(this.state.b);
    if (vEl) vEl.value = String(this.state.v);
    if (sEl) sEl.value = String(this.state.s);

    if (rVal) rVal.textContent = String(this.state.r);
    if (gVal) gVal.textContent = String(this.state.g);
    if (bVal) bVal.textContent = String(this.state.b);
    if (vVal) vVal.textContent = `${this.state.v}%`;
    if (sVal) sVal.textContent = `${this.state.s}%`;

    if (hexInput && !skipHexInput) {
      hexInput.value = this.state.hex;
    }
    if (preview) {
      preview.style.backgroundColor = this.state.hex;
    }
    if (nativePicker) {
      nativePicker.value = this.state.hex.toLowerCase();
    }

    this.drawCanvases();
  }

  setRgb(r, g, b, options = {}) {
    const clamp255 = (n) => Math.max(0, Math.min(255, Math.round(Number(n) || 0)));
    this.state.r = clamp255(r);
    this.state.g = clamp255(g);
    this.state.b = clamp255(b);
    this.state.hex = rgbToHex(this.state.r, this.state.g, this.state.b);
    const hsv = rgbToHsv(this.state.r, this.state.g, this.state.b);
    if (hsv.s > 0 && hsv.v > 0) {
      this.state.h = hsv.h;
    }
    this.state.s = hsv.s;
    this.state.v = hsv.v;
    this.syncUI(options);
    return this.getState();
  }

  setHsv(h, s, v, options = {}) {
    this.state.h = Math.max(0, Math.min(360, Math.round(Number(h) || 0)));
    this.state.s = Math.max(0, Math.min(100, Math.round(Number(s) || 0)));
    this.state.v = Math.max(0, Math.min(100, Math.round(Number(v) || 0)));
    const rgb = hsvToRgb(this.state.h, this.state.s, this.state.v);
    this.state.r = rgb.r;
    this.state.g = rgb.g;
    this.state.b = rgb.b;
    this.state.hex = rgbToHex(rgb.r, rgb.g, rgb.b);
    this.syncUI(options);
    return this.getState();
  }

  setHex(hex, options = {}) {
    const rgb = hexToRgb(hex);
    if (!rgb) return null;
    return this.setRgb(rgb.r, rgb.g, rgb.b, options);
  }

  _emitChange() {
    if (this.onChange) {
      this.onChange(this.getState());
    }
  }

  _emitCommit() {
    if (this.onCommit) {
      this.onCommit(this.getState());
    }
  }

  _bindDragCanvas(canvasEl, handler) {
    if (!canvasEl) return;

    const handlePointer = (ev) => {
      const rect = canvasEl.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const x = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      const y = Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height));
      handler(x, y);
      this._emitChange();
    };

    const onMouseMove = (ev) => {
      handlePointer(ev);
    };

    const onMouseUp = () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      this._emitCommit();
    };

    const onMouseDown = (ev) => {
      handlePointer(ev);
      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    };

    const onTouchMove = (ev) => {
      if (ev.touches && ev.touches[0]) {
        handlePointer(ev.touches[0]);
      }
    };

    const onTouchEnd = () => {
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
      window.removeEventListener('touchcancel', onTouchEnd);
      this._emitCommit();
    };

    const onTouchStart = (ev) => {
      if (ev.touches && ev.touches[0]) {
        handlePointer(ev.touches[0]);
        window.addEventListener('touchmove', onTouchMove, { passive: true });
        window.addEventListener('touchend', onTouchEnd);
        window.addEventListener('touchcancel', onTouchEnd);
      }
    };

    canvasEl.addEventListener('mousedown', onMouseDown);
    canvasEl.addEventListener('touchstart', onTouchStart, { passive: true });

    this._cleanups.push(() => {
      canvasEl.removeEventListener('mousedown', onMouseDown);
      canvasEl.removeEventListener('touchstart', onTouchStart);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
      window.removeEventListener('touchcancel', onTouchEnd);
    });
  }

  init() {
    if (this._bound) return;
    this._bound = true;

    const rEl = this._el('rInput');
    const gEl = this._el('gInput');
    const bEl = this._el('bInput');
    const vEl = this._el('vInput');
    const sEl = this._el('sInput');
    const hexInput = this._el('hexInput');
    const nativePicker = this._el('nativePicker');
    const svCanvas = this._el('svCanvas');
    const hueCanvas = this._el('hueCanvas');

    const onRgbSlider = () => {
      this.setRgb(
        rEl ? rEl.value : this.state.r,
        gEl ? gEl.value : this.state.g,
        bEl ? bEl.value : this.state.b
      );
      this._emitChange();
    };
    [rEl, gEl, bEl].forEach((el) => {
      if (!el) return;
      el.addEventListener('input', onRgbSlider);
      this._cleanups.push(() => el.removeEventListener('input', onRgbSlider));
    });

    const onHsvSlider = () => {
      this.setHsv(
        this.state.h,
        sEl ? sEl.value : this.state.s,
        vEl ? vEl.value : this.state.v
      );
      this._emitChange();
    };
    [vEl, sEl].forEach((el) => {
      if (!el) return;
      el.addEventListener('input', onHsvSlider);
      this._cleanups.push(() => el.removeEventListener('input', onHsvSlider));
    });

    if (hexInput) {
      const handleHexChange = (immediate) => {
        const norm = normalizeHexColor(hexInput.value);
        if (!norm) return;
        const rgb = hexToRgb(norm);
        if (!rgb) return;
        this.setRgb(rgb.r, rgb.g, rgb.b, { skipHexInput: true });
        if (immediate) {
          hexInput.value = norm;
          this._emitCommit();
        } else {
          this._emitChange();
        }
      };
      const onHexInput = () => handleHexChange(false);
      const onHexCommit = () => handleHexChange(true);
      hexInput.addEventListener('input', onHexInput);
      hexInput.addEventListener('change', onHexCommit);
      this._cleanups.push(() => {
        hexInput.removeEventListener('input', onHexInput);
        hexInput.removeEventListener('change', onHexCommit);
      });
    }

    if (nativePicker) {
      const onNativeInput = () => {
        const norm = normalizeHexColor(nativePicker.value);
        if (!norm) return;
        const rgb = hexToRgb(norm);
        if (!rgb) return;
        this.setRgb(rgb.r, rgb.g, rgb.b);
        this._emitChange();
      };
      nativePicker.addEventListener('input', onNativeInput);
      this._cleanups.push(() => nativePicker.removeEventListener('input', onNativeInput));
    }

    this._bindDragCanvas(svCanvas, (x, y) => {
      this.setHsv(this.state.h, x * 100, (1 - y) * 100);
    });
    this._bindDragCanvas(hueCanvas, (x) => {
      this.setHsv(x * 360, this.state.s, this.state.v);
    });
  }

  destroy() {
    this._cleanups.forEach((fn) => fn());
    this._cleanups = [];
    this._bound = false;
  }
}
