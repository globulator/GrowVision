// clients/web/js/dialogs.js
// Модальные уведомления, ошибки, тосты и единый базовый класс Modal без системных alert/confirm/prompt

let activeErrorResolve = null;
let activeConfirmResolve = null;

// Стек открытых модальных окон (LIFO) для корректного управления modal-open и Escape
const openModalStack = [];
const modalStateListeners = new Set();
let escapeListenerBound = false;

let errorModalInstance = null;
let confirmModalInstance = null;

function syncBodyModalClass() {
  if (typeof document === 'undefined' || !document.body) return;
  if (openModalStack.length > 0) {
    document.body.classList.add('modal-open');
  } else {
    document.body.classList.remove('modal-open');
  }
}

function notifyModalStateChange() {
  const hasOpen = openModalStack.length > 0;
  modalStateListeners.forEach((listener) => {
    try {
      listener(hasOpen, openModalStack.length);
    } catch (_) {
      // Игнорируем ошибки слушателей
    }
  });
}

function hasOpenModals() {
  return openModalStack.length > 0;
}

function onModalStateChange(listener) {
  if (typeof listener !== 'function') {
    return () => {};
  }
  modalStateListeners.add(listener);
  return () => {
    modalStateListeners.delete(listener);
  };
}

function ensureGlobalEscapeListener() {
  if (escapeListenerBound || typeof window === 'undefined') return;
  escapeListenerBound = true;

  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (openModalStack.length === 0) return;

    const topModal = openModalStack[openModalStack.length - 1];
    if (!topModal || !topModal.isOpen()) return;

    if (
      topModal.ignoreEscapeWhenInput &&
      e.target &&
      (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')
    ) {
      return;
    }

    if (typeof topModal.onEscape === 'function') {
      topModal.onEscape(e);
      if (e.defaultPrevented) return;
    }

    if (topModal.closeOnEscape) {
      e.preventDefault();
      topModal.requestClose();
    }
  });
}

/**
 * Единый базовый контроллер модального окна.
 * Управляет классом hidden, стеком document.body.modal-open, кликом по backdrop и клавишей Escape.
 */
class Modal {
  constructor(elementOrId, options = {}) {
    this._target = elementOrId;
    this.closeOnBackdrop = options.closeOnBackdrop !== false;
    this.closeOnEscape = options.closeOnEscape !== false;
    this.ignoreEscapeWhenInput = Boolean(options.ignoreEscapeWhenInput);
    this.onOpen = typeof options.onOpen === 'function' ? options.onOpen : null;
    this.onClose = typeof options.onClose === 'function' ? options.onClose : null;
    this.onRequestClose = typeof options.onRequestClose === 'function' ? options.onRequestClose : null;
    this.onEscape = typeof options.onEscape === 'function' ? options.onEscape : null;

    if (Array.isArray(options.closeButtons)) {
      this._closeButtons = options.closeButtons;
    } else if (options.closeBtn) {
      this._closeButtons = [options.closeBtn];
    } else {
      this._closeButtons = [];
    }

    this._bound = false;
    ensureGlobalEscapeListener();
    this.bind();
  }

  getElement() {
    if (typeof this._target === 'string') {
      return typeof document !== 'undefined' ? document.getElementById(this._target) : null;
    }
    return this._target || null;
  }

  bind() {
    if (this._bound) return;
    const el = this.getElement();
    if (!el) return;
    this._bound = true;

    if (this.closeOnBackdrop) {
      el.addEventListener('click', (e) => {
        if (e.target === el) {
          this.requestClose();
        }
      });
    }

    this._closeButtons.forEach((btnRef) => {
      const btn =
        typeof btnRef === 'string'
          ? typeof document !== 'undefined'
            ? document.getElementById(btnRef)
            : null
          : btnRef;
      if (btn) {
        btn.addEventListener('click', () => this.requestClose());
      }
    });
  }

  isOpen() {
    const el = this.getElement();
    return Boolean(el && !el.classList.contains('hidden'));
  }

  open(context) {
    this.bind();
    const el = this.getElement();
    if (!el) return false;

    const wasOpen = this.isOpen();
    el.classList.remove('hidden');

    const idx = openModalStack.indexOf(this);
    if (idx !== -1) {
      openModalStack.splice(idx, 1);
    }
    openModalStack.push(this);

    syncBodyModalClass();
    notifyModalStateChange();

    if (this.onOpen) {
      this.onOpen(context, { wasOpen });
    }
    return true;
  }

  requestClose() {
    if (this.onRequestClose) {
      return this.onRequestClose(this);
    }
    return this.close();
  }

  close(context) {
    const el = this.getElement();
    const inStack = openModalStack.includes(this);
    const wasVisible = Boolean(el && !el.classList.contains('hidden'));
    const wasOpen = wasVisible || inStack;

    if (el) {
      el.classList.add('hidden');
    }

    const idx = openModalStack.indexOf(this);
    if (idx !== -1) {
      openModalStack.splice(idx, 1);
    }

    syncBodyModalClass();
    notifyModalStateChange();

    if (wasOpen && this.onClose) {
      this.onClose(context);
    }
    return wasOpen;
  }
}

function getErrorModal() {
  if (!errorModalInstance) {
    errorModalInstance = new Modal('modal-error', {
      closeBtn: 'modal-error-ok-btn',
      onClose: () => {
        if (activeErrorResolve) {
          const resolve = activeErrorResolve;
          activeErrorResolve = null;
          resolve();
        }
      },
    });
  }
  return errorModalInstance;
}

function getConfirmModal() {
  if (!confirmModalInstance) {
    confirmModalInstance = new Modal('modal-confirm', {
      closeButtons: ['modal-confirm-cancel-btn', 'modal-confirm-close'],
      onClose: () => {
        if (activeConfirmResolve) {
          const resolve = activeConfirmResolve;
          activeConfirmResolve = null;
          resolve(false);
        }
      },
    });
  }
  return confirmModalInstance;
}

export function showConfirm(
  message,
  title = 'Подтверждение',
  confirmText = 'Подтвердить',
  cancelText = 'Отмена'
) {
  return new Promise((resolve) => {
    activeConfirmResolve = resolve;
    const modalCtrl = getConfirmModal();
    const modal = modalCtrl.getElement();
    if (!modal) {
      activeConfirmResolve = null;
      resolve(false);
      return;
    }

    const titleEl = document.getElementById('modal-confirm-title');
    const msgEl = document.getElementById('modal-confirm-message');
    const okBtn = document.getElementById('modal-confirm-ok-btn');
    const cancelBtn = document.getElementById('modal-confirm-cancel-btn');

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;
    if (okBtn) okBtn.textContent = confirmText;
    if (cancelBtn) cancelBtn.textContent = cancelText;

    modalCtrl.open();

    if (okBtn) {
      okBtn.focus();
    }
  });
}

export function closeConfirmModal(result = false) {
  if (activeConfirmResolve) {
    const resolve = activeConfirmResolve;
    activeConfirmResolve = null;
    resolve(result);
  }
  getConfirmModal().close();
}

export function showError(message, title = 'Внимание') {
  return new Promise((resolve) => {
    activeErrorResolve = resolve;
    const modalCtrl = getErrorModal();
    const modal = modalCtrl.getElement();
    if (!modal) {
      console.error(title, message);
      activeErrorResolve = null;
      resolve();
      return;
    }

    const titleEl = document.getElementById('modal-error-title');
    const msgEl = document.getElementById('modal-error-message');
    const okBtn = document.getElementById('modal-error-ok-btn');

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;

    modalCtrl.open();

    if (okBtn) {
      okBtn.focus();
    }
  });
}

export function closeErrorModal() {
  if (activeErrorResolve) {
    const resolve = activeErrorResolve;
    activeErrorResolve = null;
    resolve();
  }
  getErrorModal().close();
}

export function showToast(message, type = 'info', durationMs = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;

  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('visible'));

  setTimeout(() => {
    toast.classList.remove('visible');
    setTimeout(() => toast.remove(), 300);
  }, durationMs);
}

// Инициализация событий модальных окон
export function initDialogs() {
  const errModal = getErrorModal();
  const cnfModal = getConfirmModal();
  errModal.bind();
  cnfModal.bind();

  const cnfOkBtn = document.getElementById('modal-confirm-ok-btn');
  if (cnfOkBtn) {
    cnfOkBtn.addEventListener('click', () => closeConfirmModal(true));
  }

  // Подтверждение по Enter для верхней диалоговой модалки (Esc обрабатывает класс Modal)
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const topModal = openModalStack[openModalStack.length - 1];
    if (topModal === errModal && errModal.isOpen()) {
      e.preventDefault();
      closeErrorModal();
    } else if (topModal === cnfModal && cnfModal.isOpen()) {
      e.preventDefault();
      closeConfirmModal(true);
    }
  });
}

// Алиас для обратной совместимости и экспорты базовых утилит модальных окон
export { Modal, hasOpenModals, onModalStateChange, showError as showErrorDialog };
