// clients/web/js/system.js
// Управление системными настройками (debug_mode, auto_capture, capture_delay) и мониторинг Edge-агента.

import { api } from './api.js';

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
          <h2 class="section-title">⚙️ Системные настройки</h2>
          <span class="section-desc">Параметры сервера, статус контроллера Raspberry Pi и телеметрия</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-secondary btn-sm" id="sys-btn-refresh">🔄 Обновить</button>
        </div>
      </div>

      <div id="sys-alert-box" class="alert alert-error hidden"></div>

      <div class="system-grid">
        <!-- Карточка статуса Edge-агента -->
        <div class="sys-card">
          <div class="sys-card-header">
            <h3 class="sys-card-title">🍓 Raspberry Pi (Edge-агент)</h3>
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
            <h3 class="sys-card-title">🖥 Сервер GrowVision</h3>
            <span id="sys-version-badge" class="badge badge-primary">—</span>
          </div>
          <div class="sys-card-body">
            <div class="sys-toggle-item">
              <div class="sys-toggle-info">
                <span class="sys-toggle-title">🐞 Режим отладки (debug_mode)</span>
                <span class="sys-toggle-desc">Вывод расширенной отладочной информации и логов</span>
              </div>
              <label class="switch">
                <input type="checkbox" id="sys-debug-toggle" />
                <span class="slider"></span>
              </label>
            </div>

            <div class="sys-toggle-item">
              <div class="sys-toggle-info">
                <span class="sys-toggle-title">⏰ Автосъёмка (auto_capture)</span>
                <span class="sys-toggle-desc">Плановая съёмка каждые 4 часа по расписанию</span>
              </div>
              <label class="switch">
                <input type="checkbox" id="sys-auto-capture-toggle" />
                <span class="slider"></span>
              </label>
            </div>

            <div class="sys-delay-setting">
              <label for="sys-capture-delay-input" class="sys-label">⏱ Задержка перед съёмкой (свет/вспышка), сек:</label>
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
        window.showToast?.(`Режим отладки ${enabled ? 'включен' : 'выключен'}`, 'success');
      } catch (err) {
        e.target.checked = !enabled;
        alert('Ошибка изменения debug_mode: ' + err.message);
      }
    });

    // Переключатель auto_capture
    this.container.querySelector('#sys-auto-capture-toggle').addEventListener('change', async (e) => {
      const enabled = e.target.checked;
      try {
        await api.setAutoCapture(enabled);
        window.showToast?.(`Автосъёмка ${enabled ? 'включена' : 'выключена'}`, 'success');
      } catch (err) {
        e.target.checked = !enabled;
        alert('Ошибка изменения auto_capture: ' + err.message);
      }
    });

    // Задержка съёмки
    this.container.querySelector('#sys-btn-save-delay').addEventListener('click', async () => {
      const input = this.container.querySelector('#sys-capture-delay-input');
      const val = parseFloat(input.value);
      if (isNaN(val) || val < 0 || val > 30) {
        alert('Укажите корректную задержку в диапазоне от 0 до 30 секунд.');
        return;
      }
      try {
        await api.setCaptureDelay(val);
        window.showToast?.(`Задержка съёмки обновлена: ${val} с`, 'success');
      } catch (err) {
        alert('Ошибка сохранения задержки: ' + err.message);
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
