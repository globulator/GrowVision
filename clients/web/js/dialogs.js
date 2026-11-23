/**
 * Единый модуль стилизованных уведомлений и модальных окон диалогов:
 * - showToast(message, type, duration)
 * - showConfirm({ title, message, confirmText, cancelText, danger })
 * - showPrompt({ title, message, defaultValue, placeholder })
 */

export function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('toast-fade');
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 400);
  }, duration);
}

export function showConfirm({
  title = 'Подтверждение',
  message = '',
  confirmText = 'Подтвердить',
  cancelText = 'Отмена',
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    const modal = document.getElementById('modal-confirm');
    const titleEl = document.getElementById('confirm-modal-title');
    const msgEl = document.getElementById('confirm-modal-message');
    const btnOk = document.getElementById('confirm-modal-btn-ok');
    const btnCancel = document.getElementById('confirm-modal-btn-cancel');
    const btnClose = document.getElementById('confirm-modal-close');

    if (!modal) {
      resolve(false);
      return;
    }

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;
    if (btnOk) {
      btnOk.textContent = confirmText;
      btnOk.className = danger ? 'btn btn-danger' : 'btn btn-primary';
    }
    if (btnCancel) btnCancel.textContent = cancelText;

    const cleanup = () => {
      modal.classList.add('hidden');
      if (btnOk) btnOk.onclick = null;
      if (btnCancel) btnCancel.onclick = null;
      if (btnClose) btnClose.onclick = null;
      modal.onclick = null;
    };

    if (btnOk) {
      btnOk.onclick = () => {
        cleanup();
        resolve(true);
      };
    }
    if (btnCancel) {
      btnCancel.onclick = () => {
        cleanup();
        resolve(false);
      };
    }
    if (btnClose) {
      btnClose.onclick = () => {
        cleanup();
        resolve(false);
      };
    }
    modal.onclick = (e) => {
      if (e.target === modal) {
        cleanup();
        resolve(false);
      }
    };

    modal.classList.remove('hidden');
  });
}

export function showPrompt({
  title = 'Ввод значения',
  message = '',
  defaultValue = '',
  placeholder = '',
} = {}) {
  return new Promise((resolve) => {
    const modal = document.getElementById('modal-prompt');
    const titleEl = document.getElementById('prompt-modal-title');
    const msgEl = document.getElementById('prompt-modal-message');
    const inputEl = document.getElementById('prompt-modal-input');
    const btnOk = document.getElementById('prompt-modal-btn-ok');
    const btnCancel = document.getElementById('prompt-modal-btn-cancel');
    const btnClose = document.getElementById('prompt-modal-close');

    if (!modal || !inputEl) {
      resolve(null);
      return;
    }

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;
    inputEl.value = defaultValue || '';
    inputEl.placeholder = placeholder || '';

    const cleanup = () => {
      modal.classList.add('hidden');
      if (btnOk) btnOk.onclick = null;
      if (btnCancel) btnCancel.onclick = null;
      if (btnClose) btnClose.onclick = null;
      inputEl.onkeydown = null;
      modal.onclick = null;
    };

    const submit = () => {
      const val = inputEl.value;
      cleanup();
      resolve(val);
    };

    if (btnOk) btnOk.onclick = submit;
    inputEl.onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submit();
      } else if (e.key === 'Escape') {
        cleanup();
        resolve(null);
      }
    };
    if (btnCancel) {
      btnCancel.onclick = () => {
        cleanup();
        resolve(null);
      };
    }
    if (btnClose) {
      btnClose.onclick = () => {
        cleanup();
        resolve(null);
      };
    }
    modal.onclick = (e) => {
      if (e.target === modal) {
        cleanup();
        resolve(null);
      }
    };

    modal.classList.remove('hidden');
    setTimeout(() => inputEl.focus(), 50);
  });
}

// Экспортируем также в глобальный scope window для удобства доступа
if (typeof window !== 'undefined') {
  window.showToast = showToast;
  window.showConfirm = showConfirm;
  window.showPrompt = showPrompt;
}
