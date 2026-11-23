// clients/web/js/system.js
// Управление системными настройками (debug_mode, auto_capture, capture_delay) и мониторинг Edge-агента.

import { api } from './api.js';
import { showToast } from './dialogs.js';

export class SystemManager {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.userRole = options.userRole || 'guest';
    this.settings = null;
    this.edgeTelemetry = null;
    this.versionInfo = null;

    this.initDOM();
  }

  setRole(role) {
    this.userRole = role;
    this.render();
  }

  initDOM() {
    this.container.innerHTML = `
      <div class="section-header">
        <div class="section-title-wrap">
          <h2 class="section-title">
            <svg class="icon-inline" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            <span>Системные настройки</span>
          </h2>
          <span class="section-desc">Параметры сервера, статус контроллера Raspberry Pi и телеметрия</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-secondary btn-sm" id="sys-btn-refresh">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
            <span>Обновить</span>
          </button>
        </div>
      </div>

      <div id="sys-alert-box" class="alert alert-error hidden"></div>

      <div class="system-grid">
        <!-- Карточка статуса Edge-агента -->
        <div class="sys-card">
          <div class="sys-card-header">
            <h3 class="sys-card-title">
              <svg class="icon-inline" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>
              <span>Raspberry Pi (Edge-агент)</span>
            </h3>
            <span id="sys-edge-status-badge" class="badge badge-secondary">—</span>
          </div>
          <div class="sys-card-body">
            <div class="sys-param-row">
              <span class="sys-label">Последний сигнал (heartbeat):</span>
              <span class="sys-val" id="sys-edge-last-seen">—</span>
            </div>
            <div class="sys-param-row">
              <span class="sys-label">Uptime системы:</span>
              <span class="sys-val" id="sys-edge-uptime">—</span>
            </div>
            <div class="sys-param-row">
              <span class="sys-label">Загрузка CPU:</span>
              <span class="sys-val" id="sys-edge-cpu">—</span>
            </div>
            <div class="sys-param-row">
              <span class="sys-label">Память RAM:</span>
              <span class="sys-val" id="sys-edge-ram">—</span>
            </div>
            <div class="sys-param-row">
              <span class="sys-label">Температура ядра:</span>
              <span class="sys-val" id="sys-edge-temp">—</span>
            </div>
            <div class="sys-param-row">
              <span class="sys-label">Свободно на диске:</span>
              <span class="sys-val" id="sys-edge-disk">—</span>
            </div>
          </div>
        </div>

        <!-- Карточка настроек сервера -->
        <div class="sys-card">
          <div class="sys-card-header">
            <h3 class="sys-card-title">
              <svg class="icon-inline" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>
              <span>Сервер GrowVision</span>
            </h3>
            <span id="sys-version-badge" class="badge badge-primary">—</span>
          </div>
          <div class="sys-card-body">
            <div class="sys-toggle-item">
              <div class="sys-toggle-info">
                <span class="sys-toggle-title">
                  <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4"></path><path d="M12 8h.01"></path></svg>
                  <span>Режим отладки (debug_mode)</span>
                </span>
                <span class="sys-toggle-desc">Вывод расширенной отладочной информации и логов</span>
              </div>
              <label class="switch">
                <input type="checkbox" id="sys-debug-toggle" />
                <span class="slider"></span>
              </label>
            </div>

            <div class="sys-toggle-item">
              <div class="sys-toggle-info">
                <span class="sys-toggle-title">
                  <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                  <span>Автосъёмка (auto_capture)</span>
                </span>
                <span class="sys-toggle-desc">Плановая съёмка каждые 4 часа по расписанию</span>
              </div>
              <label class="switch">
                <input type="checkbox" id="sys-auto-capture-toggle" />
                <span class="slider"></span>
              </label>
            </div>

            <div class="sys-delay-setting">
              <label for="sys-capture-delay-input" class="sys-label">
                <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 14 10"></polyline></svg>
                <span>Задержка перед съёмкой (свет/вспышка), сек:</span>
              </label>
              <div class="delay-input-row">
                <input type="number" id="sys-capture-delay-input" class="form-input form-input-sm" min="0" max="30" step="0.5" value="1.5" />
                <button type="button" class="btn btn-secondary btn-sm" id="sys-btn-save-delay">Сохранить</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    this.container.querySelector('#sys-btn-refresh').addEventListener('click', () => {
      this.loadData();
    });

    // Переключатель debug_mode
    this.container.querySelector('#sys-debug-toggle').addEventListener('change', async (e) => {
      const enabled = e.target.checked;
      try {
        await api.setDebugMode(enabled);
        showToast(`Режим отладки ${enabled ? 'включен' : 'выключен'}`, 'success');
      } catch (err) {
        e.target.checked = !enabled;
        showToast('Ошибка изменения debug_mode: ' + err.message, 'error');
      }
    });

    // Переключатель auto_capture
    this.container.querySelector('#sys-auto-capture-toggle').addEventListener('change', async (e) => {
      const enabled = e.target.checked;
      try {
        await api.setAutoCapture(enabled);
        showToast(`Автосъёмка ${enabled ? 'включена' : 'выключена'}`, 'success');
      } catch (err) {
        e.target.checked = !enabled;
        showToast('Ошибка изменения auto_capture: ' + err.message, 'error');
      }
    });

    // Задержка съёмки
    this.container.querySelector('#sys-btn-save-delay').addEventListener('click', async () => {
      const input = this.container.querySelector('#sys-capture-delay-input');
      const val = parseFloat(input.value);
      if (isNaN(val) || val < 0 || val > 30) {
        showToast('Укажите корректную задержку в диапазоне от 0 до 30 секунд.', 'warning');
        return;
      }
      try {
        await api.setCaptureDelay(val);
        showToast(`Задержка съёмки обновлена: ${val} с`, 'success');
      } catch (err) {
        showToast('Ошибка сохранения задержки: ' + err.message, 'error');
      }
    });
  }

  async loadData() {
    const alertBox = this.container.querySelector('#sys-alert-box');
    alertBox.classList.add('hidden');

    try {
      const [settings, telemetry, version] = await Promise.all([
        api.getSystemSettings().catch(() => null),
        api.getEdgeTelemetry().catch(() => null),
        api.getVersion().catch(() => null),
      ]);
      this.settings = settings;
      this.edgeTelemetry = telemetry;
      this.versionInfo = version;
      this.render();
    } catch (err) {
      alertBox.textContent = 'Ошибка загрузки системной информации: ' + err.message;
      alertBox.classList.remove('hidden');
    }
  }

  render() {
    // Версия
    const vBadge = this.container.querySelector('#sys-version-badge');
    if (this.versionInfo && this.versionInfo.server_version) {
      vBadge.textContent = `v${this.versionInfo.server_version}`;
    }

    // Настройки
    if (this.settings) {
      const debugToggle = this.container.querySelector('#sys-debug-toggle');
      debugToggle.checked = Boolean(this.settings.debug_mode);

      const autoCapToggle = this.container.querySelector('#sys-auto-capture-toggle');
      autoCapToggle.checked = Boolean(this.settings.auto_capture);

      const delayInput = this.container.querySelector('#sys-capture-delay-input');
      if (this.settings.capture_delay_sec !== undefined) {
        delayInput.value = String(this.settings.capture_delay_sec);
      }
    }

    // Edge телеметрия
    const statusBadge = this.container.querySelector('#sys-edge-status-badge');
    const lastSeenEl = this.container.querySelector('#sys-edge-last-seen');
    const uptimeEl = this.container.querySelector('#sys-edge-uptime');
    const cpuEl = this.container.querySelector('#sys-edge-cpu');
    const ramEl = this.container.querySelector('#sys-edge-ram');
    const tempEl = this.container.querySelector('#sys-edge-temp');
    const diskEl = this.container.querySelector('#sys-edge-disk');

    if (this.edgeTelemetry) {
      const isOnline = Boolean(this.edgeTelemetry.is_online);
      statusBadge.textContent = isOnline ? 'Онлайн' : 'Оффлайн';
      statusBadge.className = `badge ${isOnline ? 'badge-success' : 'badge-danger'}`;

      lastSeenEl.textContent = this.edgeTelemetry.last_seen_at
        ? new Date(this.edgeTelemetry.last_seen_at).toLocaleString('ru-RU')
        : 'нет данных';

      uptimeEl.textContent = this.edgeTelemetry.uptime_seconds
        ? formatUptime(this.edgeTelemetry.uptime_seconds)
        : '—';

      cpuEl.textContent = this.edgeTelemetry.cpu_percent !== undefined ? `${this.edgeTelemetry.cpu_percent}%` : '—';
      ramEl.textContent = this.edgeTelemetry.ram_percent !== undefined ? `${this.edgeTelemetry.ram_percent}%` : '—';
      tempEl.textContent = this.edgeTelemetry.cpu_temp_c !== undefined ? `${this.edgeTelemetry.cpu_temp_c} °C` : '—';
      diskEl.textContent = this.edgeTelemetry.disk_free_mb !== undefined ? `${Math.round(this.edgeTelemetry.disk_free_mb / 1024 * 10) / 10} ГБ` : '—';
    } else {
      statusBadge.textContent = 'Нет связи';
      statusBadge.className = 'badge badge-secondary';
    }
  }
}

function formatUptime(sec) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d} д ${h} ч ${m} мин`;
  if (h > 0) return `${h} ч ${m} мин`;
  return `${sec} сек`;
}
