// clients/web/js/auth.js
// Модальные окна «Вход» и «Регистрация» (автономно, без Telegram-кодов)

import { api } from './api.js';
import { Modal, showToast } from './dialogs.js';

let authSuccessCallback = null;
let loginModalCtrl = null;
let registerModalCtrl = null;
let lookupDebounceTimer = null;
let lastLookedUpValue = '';
let nameEditedManually = false;

export function setAuthSuccessHandler(fn) {
  authSuccessCallback = fn;
}

export function updateBotLinks(_botUsername) {
  // Оставлено для обратной совместимости с app.js; ссылки на бота в модалках входа/регистрации больше не требуются.
}

function setInlineError(elementId, msg) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
  } else {
    el.textContent = '';
    el.classList.add('hidden');
  }
}

function setLookupHint(msg) {
  const el = document.getElementById('reg-tg-lookup-hint');
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
  } else {
    el.textContent = '';
    el.classList.add('hidden');
  }
}

function getLoginModalCtrl() {
  if (!loginModalCtrl) {
    loginModalCtrl = new Modal('modal-login', {
      closeBtn: 'modal-login-close',
      onClose: () => {
        setInlineError('login-form-error', '');
      },
    });
  }
  return loginModalCtrl;
}

function getRegisterModalCtrl() {
  if (!registerModalCtrl) {
    registerModalCtrl = new Modal('modal-register', {
      closeBtn: 'modal-register-close',
      onClose: () => {
        clearTimeout(lookupDebounceTimer);
        lookupDebounceTimer = null;
        lastLookedUpValue = '';
        nameEditedManually = false;
        setInlineError('reg-form-error', '');
        setLookupHint('');
      },
    });
  }
  return registerModalCtrl;
}

export function openLoginModal() {
  closeRegisterModal();
  const modalCtrl = getLoginModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;

  const form = document.getElementById('login-form');
  if (form) form.reset();
  setInlineError('login-form-error', '');

  modalCtrl.open();

  const firstInput = document.getElementById('login-telegram');
  if (firstInput) {
    setTimeout(() => firstInput.focus(), 50);
  }
}

export function closeLoginModal() {
  getLoginModalCtrl().close();
}

export function openRegisterModal(initialTelegram = '') {
  closeLoginModal();
  const modalCtrl = getRegisterModalCtrl();
  const modal = modalCtrl.getElement();
  if (!modal) return;

  const form = document.getElementById('register-form');
  if (form) form.reset();
  lastLookedUpValue = '';
  nameEditedManually = false;
  setInlineError('reg-form-error', '');
  setLookupHint('');

  modalCtrl.open();

  const tgInput = document.getElementById('reg-telegram');
  if (tgInput) {
    if (initialTelegram) {
      tgInput.value = initialTelegram;
      lookupTelegramAndSuggest(initialTelegram);
    }
    setTimeout(() => tgInput.focus(), 50);
  }
}

export function closeRegisterModal() {
  getRegisterModalCtrl().close();
}

function splitRecommendedName(recommended) {
  const cleaned = (recommended || '').trim();
  if (!cleaned) return { first: '', last: '' };
  const idx = cleaned.indexOf(' ');
  if (idx === -1) return { first: cleaned, last: '' };
  return {
    first: cleaned.slice(0, idx).trim(),
    last: cleaned.slice(idx + 1).trim(),
  };
}

async function lookupTelegramAndSuggest(rawValue) {
  const val = (rawValue || '').trim();
  if (!val) {
    lastLookedUpValue = '';
    setLookupHint('');
    return;
  }
  if (val === lastLookedUpValue) return;
  lastLookedUpValue = val;

  try {
    const res = await api.telegramLookup(val);
    const currentTg = (document.getElementById('reg-telegram')?.value || '').trim();
    if (currentTg !== val) return;

    if (res && res.found) {
      const fnInput = document.getElementById('reg-first-name');
      const lnInput = document.getElementById('reg-last-name');
      const fallback = splitRecommendedName(res.recommended_name);
      const suggestedFirst = (res.first_name || fallback.first || '').trim();
      const suggestedLast = (res.last_name || fallback.last || '').trim();

      const canOverwrite =
        !nameEditedManually ||
        (!(fnInput?.value || '').trim() && !(lnInput?.value || '').trim());

      if (canOverwrite) {
        if (fnInput && suggestedFirst) fnInput.value = suggestedFirst;
        if (lnInput && suggestedLast) lnInput.value = suggestedLast;
      }
      setLookupHint('Найдено в базе: имя и фамилия подставлены (можно изменить)');
    } else {
      setLookupHint('');
    }
  } catch (_) {
    // При сетевой ошибке не мешаем ручному вводу
  }
}

async function handleRegisterFormSubmit(e) {
  if (e) e.preventDefault();
  setInlineError('reg-form-error', '');

  const tgInput = document.getElementById('reg-telegram');
  const fnInput = document.getElementById('reg-first-name');
  const lnInput = document.getElementById('reg-last-name');
  const codeInput = document.getElementById('reg-access-code');
  const btn = document.getElementById('reg-submit-btn');

  const telegram = tgInput ? tgInput.value.trim() : '';
  const firstName = fnInput ? fnInput.value.trim() : '';
  const lastName = lnInput ? lnInput.value.trim() : '';
  const accessCode = codeInput ? codeInput.value.trim() : '';

  if (!firstName) {
    setInlineError('reg-form-error', 'Введите ваше Имя');
    return;
  }
  if (!accessCode) {
    setInlineError('reg-form-error', 'Введите код приглашения биолога или пароль доступа');
    return;
  }

  const fullName = [firstName, lastName].filter(Boolean).join(' ');

  if (btn) btn.disabled = true;
  try {
    const res = await api.registerComplete({
      telegram: telegram || null,
      first_name: firstName,
      last_name: lastName || null,
      full_name: fullName,
      access_code: accessCode,
    });

    closeRegisterModal();
    showToast(`Добро пожаловать, ${res.login || fullName}!`);
    if (typeof authSuccessCallback === 'function') {
      authSuccessCallback(res);
    }
  } catch (err) {
    setInlineError('reg-form-error', err.message || 'Ошибка завершения регистрации');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleLoginFormSubmit(e) {
  if (e) e.preventDefault();
  setInlineError('login-form-error', '');

  const tgInput = document.getElementById('login-telegram');
  const pwdInput = document.getElementById('login-password') || document.getElementById('login-code');
  const btn = document.getElementById('login-submit-btn');

  const telegram = tgInput ? tgInput.value.trim() : '';
  const accessCode = pwdInput ? pwdInput.value.trim() : '';

  if (!telegram || !accessCode) {
    setInlineError('login-form-error', 'Заполните Telegram и код доступа');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await api.login({ telegram, code: accessCode, password: accessCode });
    closeLoginModal();
    showToast(`Успешный вход: ${res.login || telegram}`);
    if (typeof authSuccessCallback === 'function') {
      authSuccessCallback(res);
    }
  } catch (err) {
    setInlineError('login-form-error', err.message || 'Ошибка входа');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function togglePasswordVisibility(inputEl, btnEl) {
  if (!inputEl) return;
  const isPass = inputEl.type === 'password';
  inputEl.type = isPass ? 'text' : 'password';
  if (btnEl) {
    btnEl.classList.toggle('active', isPass);
  }
}

export function initAuth() {
  const headerLoginBtn = document.getElementById('header-btn-login');
  if (headerLoginBtn) {
    headerLoginBtn.addEventListener('click', openLoginModal);
  }

  const switchRegBtn = document.getElementById('login-switch-to-reg');
  if (switchRegBtn) {
    switchRegBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const loginTg = document.getElementById('login-telegram');
      const val = loginTg ? loginTg.value.trim() : '';
      openRegisterModal(val);
    });
  }

  const linkToLogin = document.getElementById('link-to-login');
  if (linkToLogin) {
    linkToLogin.addEventListener('click', (e) => {
      e.preventDefault();
      closeRegisterModal();
      openLoginModal();
    });
  }

  getLoginModalCtrl().bind();
  getRegisterModalCtrl().bind();

  const loginForm = document.getElementById('login-form');
  if (loginForm) {
    loginForm.addEventListener('submit', handleLoginFormSubmit);
  }

  const registerForm = document.getElementById('register-form');
  if (registerForm) {
    registerForm.addEventListener('submit', handleRegisterFormSubmit);
  }

  const regTgInput = document.getElementById('reg-telegram');
  if (regTgInput) {
    regTgInput.addEventListener('blur', () => {
      clearTimeout(lookupDebounceTimer);
      lookupTelegramAndSuggest(regTgInput.value);
    });
    regTgInput.addEventListener('input', () => {
      clearTimeout(lookupDebounceTimer);
      const val = regTgInput.value.trim();
      if (!val) {
        lastLookedUpValue = '';
        setLookupHint('');
        return;
      }
      lookupDebounceTimer = setTimeout(() => {
        lookupTelegramAndSuggest(val);
      }, 350);
    });
  }

  const regFirstInput = document.getElementById('reg-first-name');
  const regLastInput = document.getElementById('reg-last-name');
  for (const el of [regFirstInput, regLastInput]) {
    if (el) {
      el.addEventListener('input', () => {
        nameEditedManually = true;
      });
    }
  }

  const toggleLoginPwd = document.getElementById('toggle-login-password');
  const loginPwdInput = document.getElementById('login-password');
  if (toggleLoginPwd && loginPwdInput) {
    toggleLoginPwd.addEventListener('click', () =>
      togglePasswordVisibility(loginPwdInput, toggleLoginPwd)
    );
  }
}
