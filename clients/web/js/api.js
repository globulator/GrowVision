// clients/web/js/api.js
// Модуль взаимодействия с сервером GrowVision через fetch

let onUnauthorizedCallback = null;

export function setUnauthorizedHandler(fn) {
  onUnauthorizedCallback = fn;
}

async function request(url, options = {}) {
  const defaultHeaders = {
    'Accept': 'application/json',
  };

  if (options.body && typeof options.body === 'string') {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  const mergedOptions = {
    ...options,
    credentials: 'same-origin',
    headers: {
      ...defaultHeaders,
      ...(options.headers || {}),
    },
  };

  try {
    const response = await fetch(url, mergedOptions);

    if (response.status === 401) {
      // Игнорируем 401 для самого эндпоинта проверки сессии и логина
      if (!url.includes('/api/auth/web/me') && !url.includes('/api/auth/web/login')) {
        if (typeof onUnauthorizedCallback === 'function') {
          onUnauthorizedCallback();
        }
      }
    }

    if (!response.ok) {
      let detail = `Ошибка HTTP ${response.status}`;
      try {
        const errJson = await response.json();
        if (errJson && errJson.detail) {
          detail = errJson.detail;
        }
      } catch (_) {
        // если не JSON
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }

    if (response.status === 204) {
      return null;
    }

    return await response.json();
  } catch (err) {
    if (err.name === 'TypeError' && err.message.includes('fetch')) {
      const netError = new Error('Нет связи с сервером GrowVision. Проверьте сеть.');
      netError.status = 0;
      throw netError;
    }
    throw err;
  }
}

export const api = {
  // Информация о сессии
  getMe: () => request('/api/auth/web/me'),

  // Регистрация в Web SPA через Telegram
  registerStart: (telegram) =>
    request('/api/auth/web/register/start', {
      method: 'POST',
      body: JSON.stringify({ telegram }),
    }),

  registerVerify: (telegram_id, code) =>
    request('/api/auth/web/register/verify', {
      method: 'POST',
      body: JSON.stringify({ telegram_id: Number(telegram_id), code }),
    }),

  registerComplete: ({ telegram_id, verification_code, full_name, access_code, password }) =>
    request('/api/auth/web/register/complete', {
      method: 'POST',
      body: JSON.stringify({
        telegram_id: Number(telegram_id),
        verification_code,
        full_name,
        access_code,
        password,
      }),
    }),

  // Вход и выход
  login: ({ telegram, password }) =>
    request('/api/auth/web/login', {
      method: 'POST',
      body: JSON.stringify({ telegram, password }),
    }),

  logout: () =>
    request('/api/auth/web/logout', {
      method: 'POST',
    }),

  // Установки
  getInstallations: () => request('/api/installations'),

  // Кадры установки
  getInstallationFrames: (instId, limit = 500) =>
    request(`/api/installations/${Number(instId)}/frames?limit=${encodeURIComponent(limit)}`),

  // Тестовый снимок (target строго Number(instId))
  snap: (instId) =>
    request('/api/control/snap', {
      method: 'POST',
      body: JSON.stringify({ target: Number(instId) }),
    }),

  // Версия системы
  getVersion: () => request('/api/version'),

  // Системный статус (для разработчика)
  getSystemStatus: () => request('/api/system/status'),

  // Помощник настройки Edge-агента (Raspberry Pi)
  getEdgeSetupDefaults: () => request('/api/edge/setup/defaults'),
  createEdgeSetupToken: ({ server_url, netbird_setup_key, hardware_mode }) =>
    request('/api/edge/setup/token', {
      method: 'POST',
      body: JSON.stringify({ server_url, netbird_setup_key, hardware_mode }),
    }),
};
