// clients/web/js/dialogs.js
// Модальные уведомления, ошибки и тосты без системных alert/confirm/prompt

let activeErrorResolve = null;

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

// Инициализация событий закрытия ошибки
export function initDialogs() {
  const modal = document.getElementById('modal-error');
  if (!modal) return;

  const okBtn = document.getElementById('modal-error-ok-btn');
  if (okBtn) {
    okBtn.addEventListener('click', closeErrorModal);
  }

  // Клик по затемнённому фону
  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      closeErrorModal();
    }
  });

  // Клавиши Esc и Enter
  window.addEventListener('keydown', (e) => {
    if (modal && !modal.classList.contains('hidden')) {
      if (e.key === 'Escape' || e.key === 'Enter') {
        e.preventDefault();
        closeErrorModal();
      }
    }
  });
}
