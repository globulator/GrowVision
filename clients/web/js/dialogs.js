// clients/web/js/dialogs.js
// Модальные уведомления, ошибки и тосты без системных alert/confirm/prompt

let activeErrorResolve = null;
let activeConfirmResolve = null;

export function showConfirm(
  message,
  title = 'Подтверждение',
  confirmText = 'Подтвердить',
  cancelText = 'Отмена'
) {
  return new Promise((resolve) => {
    activeConfirmResolve = resolve;
    const modal = document.getElementById('modal-confirm');
    if (!modal) {
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

    modal.classList.remove('hidden');
    document.body.classList.add('modal-open');

    if (okBtn) {
      okBtn.focus();
    }
  });
}

export function closeConfirmModal(result = false) {
  const modal = document.getElementById('modal-confirm');
  if (modal) {
    modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }
  if (activeConfirmResolve) {
    const resolve = activeConfirmResolve;
    activeConfirmResolve = null;
    resolve(result);
  }
}

export function showError(message, title = 'Внимание') {
  return new Promise((resolve) => {
    activeErrorResolve = resolve;
    const modal = document.getElementById('modal-error');
    if (!modal) {
      console.error(title, message);
      resolve();
      return;
    }

    const titleEl = document.getElementById('modal-error-title');
    const msgEl = document.getElementById('modal-error-message');
    const okBtn = document.getElementById('modal-error-ok-btn');

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;

    modal.classList.remove('hidden');
    document.body.classList.add('modal-open');

    if (okBtn) {
      okBtn.focus();
    }
  });
}

export function closeErrorModal() {
  const modal = document.getElementById('modal-error');
  if (modal) {
    modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }
  if (activeErrorResolve) {
    const resolve = activeErrorResolve;
    activeErrorResolve = null;
    resolve();
  }
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
  const modalErr = document.getElementById('modal-error');
  if (modalErr) {
    const okBtn = document.getElementById('modal-error-ok-btn');
    if (okBtn) {
      okBtn.addEventListener('click', closeErrorModal);
    }
    modalErr.addEventListener('click', (e) => {
      if (e.target === modalErr) {
        closeErrorModal();
      }
    });
  }

  const modalCnf = document.getElementById('modal-confirm');
  if (modalCnf) {
    const okBtn = document.getElementById('modal-confirm-ok-btn');
    const cancelBtn = document.getElementById('modal-confirm-cancel-btn');
    const closeBtn = document.getElementById('modal-confirm-close');

    if (okBtn) okBtn.addEventListener('click', () => closeConfirmModal(true));
    if (cancelBtn) cancelBtn.addEventListener('click', () => closeConfirmModal(false));
    if (closeBtn) closeBtn.addEventListener('click', () => closeConfirmModal(false));

    modalCnf.addEventListener('click', (e) => {
      if (e.target === modalCnf) {
        closeConfirmModal(false);
      }
    });
  }

  // Клавиши Esc и Enter
  window.addEventListener('keydown', (e) => {
    if (modalErr && !modalErr.classList.contains('hidden')) {
      if (e.key === 'Escape' || e.key === 'Enter') {
        e.preventDefault();
        closeErrorModal();
      }
    } else if (modalCnf && !modalCnf.classList.contains('hidden')) {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeConfirmModal(false);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        closeConfirmModal(true);
      }
    }
  });
}

// Алиас для обратной совместимости
export { showError as showErrorDialog };
