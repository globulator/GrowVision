// clients/web/js/auth.js
// Модальные окна «Вход» и пошаговой «Регистрации» через Telegram

import { api } from './api.js';
import { showError, showToast } from './dialogs.js';

let authSuccessCallback = null;

// Состояние пошаговой регистрации
const registerState = {
  step: 1,
  telegram: '',
  telegram_id: null,
  code: '',
  timerInterval: null,
  timerSeconds: 0,
};

export function setAuthSuccessHandler(fn) {
  authSuccessCallback = fn;
}

// Открытие модалки входа
export function openLoginModal() {
  closeRegisterModal();
  const modal = document.getElementById('modal-login');
  if (!modal) return;

  const form = document.getElementById('login-form');
  if (form) form.reset();

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
}

// Открытие модалки регистрации
export function openRegisterModal() {
  closeLoginModal();
  const modal = document.getElementById('modal-register');
  if (!modal) return;

  setRegisterStep(1);
  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  const firstInput = document.getElementById('reg-telegram');
  if (firstInput) {
    setTimeout(() => firstInput.focus(), 50);
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
  registerState.step = 1;
  registerState.telegram = '';
  registerState.telegram_id = null;
  registerState.code = '';
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
    if (el) setTimeout(() => el.focus(), 50);
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
  const input = document.getElementById('reg-telegram');
  const btn = document.getElementById('reg-btn-step1');
  const val = input ? input.value.trim() : '';

  if (!val) {
    showError('Введите ваш Telegram (@username или числовой ID)', 'Регистрация');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await api.registerStart(val);
    registerState.telegram = val;
    registerState.telegram_id = res.telegram_id;
    showToast('Код подтверждения отправлен в Telegram-бот GrowVision');
    setRegisterStep(2);
    startResendTimer();
  } catch (err) {
    showError(err.message, 'Ошибка регистрации');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Обработка проверки кода (Шаг 2)
async function handleRegisterStep2() {
  const input = document.getElementById('reg-code');
  const btn = document.getElementById('reg-btn-step2');
  const codeVal = input ? input.value.trim() : '';

  if (!codeVal || codeVal.length < 6) {
    showError('Введите 6-значный код подтверждения', 'Проверка кода');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    await api.registerVerify(registerState.telegram_id, codeVal);
    registerState.code = codeVal;
    showToast('Код успешно подтверждён');
    setRegisterStep(3);
  } catch (err) {
    showError(err.message, 'Неверный код');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Завершение регистрации (Шаг 3)
async function handleRegisterStep3() {
  const fnInput = document.getElementById('reg-fullname');
  const codeInput = document.getElementById('reg-access-code');
  const pwdInput = document.getElementById('reg-password');
  const btn = document.getElementById('reg-btn-step3');

  const fullName = fnInput ? fnInput.value.trim() : '';
  const accessCode = codeInput ? codeInput.value.trim() : '';
  const password = pwdInput ? pwdInput.value.trim() : '';

  if (!fullName) {
    showError('Введите ваши Имя и Фамилию', 'Регистрация');
    return;
  }
  if (!accessCode) {
    showError('Введите код приглашения биолога или пароль разработчика', 'Регистрация');
    return;
  }
  if (!password || password.length < 4) {
    showError('Пароль должен содержать не менее 4 символов', 'Регистрация');
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await api.registerComplete({
      telegram_id: registerState.telegram_id,
      verification_code: registerState.code,
      full_name: fullName,
      access_code: accessCode,
      password: password,
    });

    closeRegisterModal();
    showToast(`Добро пожаловать, ${res.login || fullName}!`);
    if (typeof authSuccessCallback === 'function') {
      authSuccessCallback(res);
    }
  } catch (err) {
    showError(err.message, 'Ошибка завершения регистрации');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Обработка формы входа
async function handleLoginFormSubmit(e) {
  if (e) e.preventDefault();

  const tgInput = document.getElementById('login-telegram');
  const pwdInput = document.getElementById('login-password') || document.getElementById('login-code');
  const btn = document.getElementById('login-submit-btn');

  const telegram = tgInput ? tgInput.value.trim() : '';
  const accessCode = pwdInput ? pwdInput.value.trim() : '';

  if (!telegram || !accessCode) {
    showError('Заполните Telegram и код доступа', 'Вход в систему');
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
    showError(err.message, 'Ошибка входа');
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

  // Переключение Вход <-> Регистрация
  const toRegLink = document.getElementById('link-to-register');
  if (toRegLink) {
    toRegLink.addEventListener('click', (e) => {
      e.preventDefault();
      openRegisterModal();
    });
  }
  const toLoginLink = document.getElementById('link-to-login');
  if (toLoginLink) {
    toLoginLink.addEventListener('click', (e) => {
      e.preventDefault();
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

  const toggleRegPwd = document.getElementById('toggle-reg-password');
  const regPwdInput = document.getElementById('reg-password');
  if (toggleRegPwd && regPwdInput) {
    toggleRegPwd.addEventListener('click', () =>
      togglePasswordVisibility(regPwdInput, toggleRegPwd)
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
