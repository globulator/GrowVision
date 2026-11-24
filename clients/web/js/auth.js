// clients/web/js/auth.js
// Модальные окна «Вход» и пошаговой «Регистрации» через Telegram

import { api } from './api.js';
import { showToast } from './dialogs.js';

let authSuccessCallback = null;

// Состояние пошаговой регистрации
const registerState = {
  step: 1,
  telegram: '',
  telegram_id: null,
  recommended_name: '',
  code: '',
  access_code: '',
  timerInterval: null,
  timerSeconds: 0,
  pollInterval: null,
};

export function setAuthSuccessHandler(fn) {
  authSuccessCallback = fn;
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

function stopPollingCodeDelivery() {
  if (registerState.pollInterval) {
    clearInterval(registerState.pollInterval);
    registerState.pollInterval = null;
  }
}

function startPollingCodeDelivery(telegramId) {
  stopPollingCodeDelivery();
  if (!telegramId) return;

  let attempts = 0;
  const maxAttempts = 10;

  registerState.pollInterval = setInterval(async () => {
    attempts++;
    if (attempts > maxAttempts || registerState.step !== 2) {
      stopPollingCodeDelivery();
      return;
    }

    try {
      const res = await api.getCodeStatus(telegramId);
      if (registerState.step !== 2) {
        stopPollingCodeDelivery();
        return;
      }

      if (res && res.status === 'delivery_error') {
        stopPollingCodeDelivery();
        const errDetail = res.error || 'ошибка доставки';
        setInlineError(
          'reg-error-step2',
          `Не удалось доставить код в Telegram (${errDetail}). Убедитесь, что бот запущен (@GrowVisionBot) и не заблокирован.`
        );
      } else if (res && res.status === 'delivered') {
        stopPollingCodeDelivery();
      }
    } catch (_) {
      // Игнорируем сетевые ошибки фонового опроса
    }
  }, 2000);
}

export function updateBotLinks(botUsername) {
  const cleanName = (botUsername || 'GrowVisionBot').replace(/^@/, '');
  const loginLink = document.getElementById('login-tg-bot-link');
  if (loginLink) {
    loginLink.href = `https://t.me/${cleanName}`;
    loginLink.textContent = `@${cleanName}`;
  }
  const regLink = document.getElementById('reg-tg-bot-link');
  if (regLink) {
    regLink.href = `https://t.me/${cleanName}`;
    regLink.textContent = `@${cleanName}`;
  }
}

// Открытие модалки входа
export function openLoginModal() {
  closeRegisterModal();
  const modal = document.getElementById('modal-login');
  if (!modal) return;

  const form = document.getElementById('login-form');
  if (form) form.reset();
  setInlineError('login-form-error', '');

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  const firstInput = document.getElementById('login-telegram');
  if (firstInput) {
    setTimeout(() => firstInput.focus(), 50);
  }
}

export function closeLoginModal() {
  const modal = document.getElementById('modal-login');
  if (modal) {
    modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }
  setInlineError('login-form-error', '');
}

// Открытие модалки регистрации
export function openRegisterModal(initialTelegram = '', initialStep = 1) {
  closeLoginModal();
  const modal = document.getElementById('modal-register');
  if (!modal) return;

  setInlineError('reg-error-step1', '');
  setInlineError('reg-error-step2', '');
  setInlineError('reg-error-step3', '');

  setRegisterStep(initialStep);
  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  const firstInput = document.getElementById('reg-telegram');
  if (firstInput && initialTelegram) {
    firstInput.value = initialTelegram;
  }
}

export function closeRegisterModal() {
  const modal = document.getElementById('modal-register');
  if (modal) {
    modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }
  clearInterval(registerState.timerInterval);
  registerState.timerInterval = null;
  stopPollingCodeDelivery();
  registerState.step = 1;
  registerState.telegram = '';
  registerState.telegram_id = null;
  registerState.recommended_name = '';
  registerState.code = '';
  registerState.access_code = '';
  setInlineError('reg-error-step1', '');
  setInlineError('reg-error-step2', '');
  setInlineError('reg-error-step3', '');
}

// Переключение шага регистрации
function setRegisterStep(step) {
  registerState.step = step;
  const s1 = document.getElementById('reg-step-1');
  const s2 = document.getElementById('reg-step-2');
  const s3 = document.getElementById('reg-step-3');

  if (s1) s1.classList.toggle('hidden', step !== 1);
  if (s2) s2.classList.toggle('hidden', step !== 2);
  if (s3) s3.classList.toggle('hidden', step !== 3);

  if (step === 1) {
    const el = document.getElementById('reg-telegram');
    if (el) setTimeout(() => el.focus(), 50);
  } else if (step === 2) {
    const el = document.getElementById('reg-code');
    if (el) setTimeout(() => el.focus(), 50);
  } else if (step === 3) {
    const el = document.getElementById('reg-fullname');
    if (el) {
      if (registerState.recommended_name) {
        el.value = registerState.recommended_name;
      }
      setTimeout(() => el.focus(), 50);
    }
    const accessEl = document.getElementById('reg-access-code');
    if (accessEl && registerState.access_code) {
      accessEl.value = registerState.access_code;
    }
  }
}

// Запуск таймера на повторную отправку кода (60 секунд)
function startResendTimer() {
  clearInterval(registerState.timerInterval);
  registerState.timerSeconds = 60;
  const btn = document.getElementById('reg-resend-btn');
  const timerSpan = document.getElementById('reg-resend-timer');

  if (btn) btn.disabled = true;
  if (timerSpan) timerSpan.textContent = `(${registerState.timerSeconds} с)`;

  registerState.timerInterval = setInterval(() => {
    registerState.timerSeconds--;
    if (registerState.timerSeconds <= 0) {
      clearInterval(registerState.timerInterval);
      registerState.timerInterval = null;
      if (btn) btn.disabled = false;
      if (timerSpan) timerSpan.textContent = '';
    } else {
      if (timerSpan) timerSpan.textContent = `(${registerState.timerSeconds} с)`;
    }
  }, 1000);
}

// Обработка отправки кода (Шаг 1)
async function handleRegisterStep1() {
  setInlineError('reg-error-step1', '');
  const input = document.getElementById('reg-telegram');
  const btn = document.getElementById('reg-btn-step1');
  const val = input ? input.value.trim() : '';

  if (!val) {
    setInlineError('reg-error-step1', 'Введите ваш Telegram (@username или числовой ID)');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await api.registerStart(val);
    registerState.telegram = val;
    registerState.telegram_id = res.telegram_id;
    registerState.recommended_name = res.recommended_name || '';
    showToast('Код подтверждения отправлен в Telegram');
    setRegisterStep(2);
    startResendTimer();
    startPollingCodeDelivery(res.telegram_id);
  } catch (err) {
    setInlineError('reg-error-step1', err.message || 'Ошибка отправки кода');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Обработка проверки кода (Шаг 2)
async function handleRegisterStep2() {
  setInlineError('reg-error-step2', '');
  const input = document.getElementById('reg-code');
  const btn = document.getElementById('reg-btn-step2');
  const codeVal = input ? input.value.trim() : '';

  if (!codeVal || codeVal.length < 6) {
    setInlineError('reg-error-step2', 'Введите 6-значный код подтверждения');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    await api.registerVerify(registerState.telegram_id, codeVal);
    registerState.code = codeVal;
    stopPollingCodeDelivery();
    showToast('Код успешно подтверждён');
    setRegisterStep(3);
  } catch (err) {
    setInlineError('reg-error-step2', err.message || 'Неверный код подтверждения');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Завершение регистрации (Шаг 3)
async function handleRegisterStep3() {
  setInlineError('reg-error-step3', '');
  const fnInput = document.getElementById('reg-fullname');
  const codeInput = document.getElementById('reg-access-code');
  const btn = document.getElementById('reg-btn-step3');

  const fullName = fnInput ? fnInput.value.trim() : '';
  const accessCode = codeInput ? codeInput.value.trim() : '';

  if (!fullName) {
    setInlineError('reg-error-step3', 'Введите ваши Имя и Фамилию');
    return;
  }
  if (!accessCode) {
    setInlineError('reg-error-step3', 'Введите код приглашения биолога или пароль разработчика');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await api.registerComplete({
      telegram_id: registerState.telegram_id,
      verification_code: registerState.code,
      full_name: fullName,
      access_code: accessCode,
    });

    closeRegisterModal();
    showToast(`Добро пожаловать, ${res.login || fullName}!`);
    if (typeof authSuccessCallback === 'function') {
      authSuccessCallback(res);
    }
  } catch (err) {
    setInlineError('reg-error-step3', err.message || 'Ошибка завершения регистрации');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Обработка формы входа
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
    // Если пользователь не зарегистрирован в системе — бесшовный переход на верификацию и шаг 2
    if (
      err.status === 403 ||
      (typeof err.message === 'string' &&
        (err.message.includes('telegram_verification_required') ||
          err.message.includes('not_registered')))
    ) {
      try {
        const startRes = await api.registerStart(telegram);
        closeLoginModal();
        registerState.telegram = telegram;
        registerState.telegram_id = startRes.telegram_id;
        registerState.recommended_name = startRes.recommended_name || '';
        registerState.access_code = accessCode;

        openRegisterModal(telegram, 2);
        showToast('Код подтверждения отправлен в Telegram');
        startResendTimer();
        startPollingCodeDelivery(startRes.telegram_id);
      } catch (startErr) {
        setInlineError(
          'login-form-error',
          startErr.message || 'Ошибка отправки кода подтверждения в Telegram'
        );
      }
      return;
    }
    setInlineError('login-form-error', err.message || 'Ошибка входа');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Переключение видимости паролей
function togglePasswordVisibility(inputEl, btnEl) {
  if (!inputEl) return;
  const isPass = inputEl.type === 'password';
  inputEl.type = isPass ? 'text' : 'password';
  if (btnEl) {
    btnEl.classList.toggle('active', isPass);
  }
}

// Инициализация событий модалок авторизации
export function initAuth() {
  // Кнопка входа в шапке
  const headerLoginBtn = document.getElementById('header-btn-login');
  if (headerLoginBtn) {
    headerLoginBtn.addEventListener('click', openLoginModal);
  }

  // Переход на регистрацию из модалки входа
  const switchRegBtn = document.getElementById('login-switch-to-reg');
  if (switchRegBtn) {
    switchRegBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const loginTg = document.getElementById('login-telegram');
      const val = loginTg ? loginTg.value.trim() : '';
      openRegisterModal(val);
    });
  }

  // Переход на вход из модалки регистрации
  const linkToLogin = document.getElementById('link-to-login');
  if (linkToLogin) {
    linkToLogin.addEventListener('click', (e) => {
      e.preventDefault();
      closeRegisterModal();
      openLoginModal();
    });
  }

  // Кнопки закрытия
  const closeLoginBtn = document.getElementById('modal-login-close');
  if (closeLoginBtn) closeLoginBtn.addEventListener('click', closeLoginModal);

  const closeRegBtn = document.getElementById('modal-register-close');
  if (closeRegBtn) closeRegBtn.addEventListener('click', closeRegisterModal);

  // Клик по фону (backdrop)
  const modalLogin = document.getElementById('modal-login');
  if (modalLogin) {
    modalLogin.addEventListener('click', (e) => {
      if (e.target === modalLogin) closeLoginModal();
    });
  }
  const modalReg = document.getElementById('modal-register');
  if (modalReg) {
    modalReg.addEventListener('click', (e) => {
      if (e.target === modalReg) closeRegisterModal();
    });
  }

  // Форма входа
  const loginForm = document.getElementById('login-form');
  if (loginForm) {
    loginForm.addEventListener('submit', handleLoginFormSubmit);
  }

  // Кнопки шагов регистрации
  const btnStep1 = document.getElementById('reg-btn-step1');
  if (btnStep1) btnStep1.addEventListener('click', handleRegisterStep1);

  const btnStep2 = document.getElementById('reg-btn-step2');
  if (btnStep2) btnStep2.addEventListener('click', handleRegisterStep2);

  const btnStep3 = document.getElementById('reg-btn-step3');
  if (btnStep3) btnStep3.addEventListener('click', handleRegisterStep3);

  const btnResend = document.getElementById('reg-resend-btn');
  if (btnResend) btnResend.addEventListener('click', handleRegisterStep1);

  // Поддержка Enter на полях регистрации
  const regTg = document.getElementById('reg-telegram');
  if (regTg) {
    regTg.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleRegisterStep1();
      }
    });
  }
  const regCode = document.getElementById('reg-code');
  if (regCode) {
    regCode.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleRegisterStep2();
      }
    });
  }

  // SVG-кнопки переключения видимости пароля
  const toggleLoginPwd = document.getElementById('toggle-login-password');
  const loginPwdInput = document.getElementById('login-password');
  if (toggleLoginPwd && loginPwdInput) {
    toggleLoginPwd.addEventListener('click', () =>
      togglePasswordVisibility(loginPwdInput, toggleLoginPwd)
    );
  }

  // Закрытие по Escape
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (modalLogin && !modalLogin.classList.contains('hidden')) {
        closeLoginModal();
      } else if (modalReg && !modalReg.classList.contains('hidden')) {
        closeRegisterModal();
      }
    }
  });
}
