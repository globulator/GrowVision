// clients/web/js/api.js
// Модуль работы с API GrowVision без внешних зависимостей и без секретов (роль Гость по умолчанию).

function getClientId() {
  let clientId = localStorage.getItem('gv_client_id');
  if (!clientId) {
    clientId = 'web_' + Math.random().toString(36).substring(2, 10) + Date.now().toString(36);
    localStorage.setItem('gv_client_id', clientId);
  }
  return clientId;
}

async function request(endpoint, options = {}) {
  const defaultHeaders = {
    'Accept': 'application/json',
  };

  if (options.body && !(options.body instanceof FormData)) {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  const config = {
    ...options,
    credentials: 'same-origin',
    headers: {
      ...defaultHeaders,
      ...(options.headers || {}),
    },
  };

  let response;
  try {
    response = await fetch(endpoint, config);
  } catch (err) {
    throw new Error('Сервер недоступен. Проверьте сетевое подключение.');
  }

  // Обработка 204 No Content
  if (response.status === 204) {
    return null;
  }

  // Для бинарных файлов (например MP4 видео)
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('video/') || contentType.includes('application/octet-stream')) {
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `Ошибка загрузки видео (${response.status})`);
    }
    return response.blob();
  }

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    // Ответ не в формате JSON
  }

  if (!response.ok) {
    let message = 'Произошла ошибка при выполнении запроса';
    if (data && data.detail) {
      if (Array.isArray(data.detail)) {
        message = data.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
      } else {
        message = data.detail;
      }
    } else if (response.status === 401) {
      message = 'Требуется авторизация или сессия завершена';
    } else if (response.status === 403) {
      message = 'Недостаточно прав для выполнения операции';
    } else if (response.status === 404) {
      message = 'Запрашиваемый ресурс не найден';
    } else if (response.status === 409) {
      message = 'Конфликт данных';
    } else if (response.status === 429) {
      message = 'Превышен лимит запросов. Попробуйте позже.';
    }
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

export const api = {
  getClientId,

  // Авторизация
  async getMe() {
    return request('/api/auth/web/me');
  },

  async login(password, login = null) {
    const clientId = getClientId();
    return request('/api/auth/web/login', {
      method: 'POST',
      body: JSON.stringify({
        password,
        login: login || null,
        client_id: clientId,
      }),
    });
  },

  async logout() {
    return request('/api/auth/web/logout', { method: 'POST' });
  },

  async updateMe(newLogin) {
    return request('/api/auth/me', {
      method: 'PATCH',
      body: JSON.stringify({ login: newLogin }),
    });
  },

  // Установки
  async getInstallations(includeArchived = false) {
    return request(`/api/installations?include_archived=${includeArchived}`);
  },

  async getInstallation(id) {
    return request(`/api/installations/${id}`);
  },

  async createInstallation(data) {
    return request('/api/installations', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async updateInstallation(id, data) {
    return request(`/api/installations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  },

  async archiveInstallation(id) {
    return request(`/api/installations/${id}/archive`, { method: 'POST' });
  },

  async unarchiveInstallation(id) {
    return request(`/api/installations/${id}/unarchive`, { method: 'POST' });
  },

  async deleteInstallation(id) {
    return request(`/api/installations/${id}`, { method: 'DELETE' });
  },

  // Таймлапс и кадры
  async getTimelapseFrames(installationId, cameraId = null) {
    let url = `/api/timelapse/${installationId}/frames`;
    if (cameraId !== null && cameraId !== undefined) {
      url += `?camera_id=${cameraId}`;
    }
    return request(url);
  },

  async buildVideo(installationId, { cameraId = null, step = 1, durationSec = null } = {}) {
    const body = { step };
    if (cameraId !== null && cameraId !== undefined) body.camera_id = cameraId;
    if (durationSec !== null && durationSec !== undefined) body.duration_sec = durationSec;

    return request(`/api/timelapse/${installationId}/video`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  // Управление (свет, реле, снимки)
  async getRelays() {
    return request('/api/control/relays');
  },

  async getInstallationRelays(installationId) {
    return request(`/api/control/relays/${installationId}`);
  },

  async setLight(target, state) {
    return request('/api/control/light/main', {
      method: 'POST',
      body: JSON.stringify({ target: String(target), state }),
    });
  },

  async setFlash(target, state) {
    return request('/api/control/light/flash', {
      method: 'POST',
      body: JSON.stringify({ target: String(target), state }),
    });
  },

  async testRelays() {
    return request('/api/control/relay-test', { method: 'POST' });
  },

  async snap(target) {
    return request('/api/control/snap', {
      method: 'POST',
      body: JSON.stringify({ target: String(target) }),
    });
  },

  // Камеры
  async getCameras() {
    return request('/api/cameras');
  },

  async scanCameras() {
    return request('/api/cameras/scan', { method: 'POST' });
  },

  async bindCamera(cameraId, installationId) {
    return request(`/api/cameras/${cameraId}/bind/${installationId}`, { method: 'POST' });
  },

  async unbindCamera(cameraId) {
    return request(`/api/cameras/${cameraId}/unbind`, { method: 'POST' });
  },

  async renameCamera(cameraId, autoName) {
    return request(`/api/cameras/${cameraId}`, {
      method: 'PATCH',
      body: JSON.stringify({ auto_name: autoName }),
    });
  },

  // Сессии и инвайты (Developer)
  async getInvites() {
    return request('/api/auth/invites');
  },

  async createInvite() {
    return request('/api/auth/invites', { method: 'POST' });
  },

  async revokeInvite(inviteId) {
    return request(`/api/auth/invites/${inviteId}`, { method: 'DELETE' });
  },

  async getSessions() {
    return request('/api/auth/sessions');
  },

  async renameSession(sessionId, login) {
    return request(`/api/auth/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify({ login }),
    });
  },

  async terminateSession(sessionId) {
    return request(`/api/auth/sessions/${sessionId}/terminate`, { method: 'POST' });
  },

  // Системные настройки и Edge
  async getSystemSettings() {
    return request('/api/system/settings');
  },

  async setDebugMode(enabled) {
    return request('/api/system/debug-mode', {
      method: 'POST',
      body: JSON.stringify({ debug_mode: enabled }),
    });
  },

  async setAutoCapture(enabled) {
    return request('/api/system/auto-capture', {
      method: 'POST',
      body: JSON.stringify({ auto_capture: enabled }),
    });
  },

  async setCaptureDelay(delaySec) {
    return request('/api/system/capture-delay', {
      method: 'POST',
      body: JSON.stringify({ capture_delay_sec: delaySec }),
    });
  },

  async getEdgeTelemetry() {
    return request('/api/system/edge-telemetry');
  },

  async getVersion() {
    return request('/api/version');
  },
};
