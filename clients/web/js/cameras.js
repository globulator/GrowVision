// clients/web/js/cameras.js
// Управление камерами установки: список, сканирование, привязка/отвязка, редактирование.

import { api } from './api.js';

export class CamerasManager {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.userRole = options.userRole || 'guest';
    this.cameras = [];
    this.units = [];

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
          <h2 class="section-title">📷 Камеры видеонаблюдения</h2>
          <span class="section-desc">Список физических камер Raspberry Pi и привязка к установкам</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-primary btn-sm" id="cams-btn-scan">🔍 Сканировать камеры</button>
          <button type="button" class="btn btn-secondary btn-sm" id="cams-btn-refresh">🔄 Обновить</button>
        </div>
      </div>

      <div id="cams-alert-box" class="alert alert-error hidden"></div>
      <div id="cams-debug-box" class="alert alert-info hidden"></div>
      <div id="cams-grid" class="cams-grid"></div>
    `;

    this.container.querySelector('#cams-btn-refresh').addEventListener('click', () => {
      this.loadData();
    });

    this.container.querySelector('#cams-btn-scan').addEventListener('click', () => {
      this.handleScan();
    });
  }

  async loadData() {
    const alertBox = this.container.querySelector('#cams-alert-box');
    alertBox.classList.add('hidden');
    const grid = this.container.querySelector('#cams-grid');
    grid.innerHTML = '<div class="loading-spinner">Загрузка списка камер...</div>';

    try {
      const [camsResp, unitsResp] = await Promise.all([
        api.getCameras(),
        api.getInstallations(false),
      ]);
      this.cameras = camsResp || [];
      this.units = unitsResp || [];
      this.render();
    } catch (err) {
      grid.innerHTML = '';
      alertBox.textContent = 'Не удалось загрузить данные камер: ' + err.message;
      alertBox.classList.remove('hidden');
    }
  }

  async handleScan() {
    const scanBtn = this.container.querySelector('#cams-btn-scan');
    const debugBox = this.container.querySelector('#cams-debug-box');
    debugBox.classList.add('hidden');
    scanBtn.disabled = true;
    scanBtn.textContent = 'Сканирование...';

    try {
      const resp = await api.scanCameras();
      const foundCount = resp.cameras ? resp.cameras.length : 0;
      window.showToast?.(`Сканирование завершено: найдено камер: ${foundCount}`, 'success');

      if (resp.debug_info && resp.debug_info.log_line) {
        debugBox.innerHTML = `<strong>🐞 Лог сканирования:</strong> <code>${escapeHtml(resp.debug_info.log_line)}</code>`;
        debugBox.classList.remove('hidden');
      }

      await this.loadData();
    } catch (err) {
      alert('Ошибка сканирования камер: ' + err.message);
    } finally {
      scanBtn.disabled = false;
      scanBtn.textContent = '🔍 Сканировать камеры';
    }
  }

  render() {
    const grid = this.container.querySelector('#cams-grid');
    grid.innerHTML = '';

    if (!this.cameras || this.cameras.length === 0) {
      grid.innerHTML = '<div class="empty-state">Камер в системе пока нет. Нажмите «Сканировать камеры», чтобы обнаружить устройства.</div>';
      return;
    }

    this.cameras.forEach((cam) => {
      const card = document.createElement('div');
      card.className = `cam-card ${cam.is_online ? 'cam-online' : 'cam-offline'}`;

      const assignedUnit = this.units.find((u) => u.id === cam.installation_id);
      const assignedName = assignedUnit ? assignedUnit.name : (cam.installation_id ? `Установка #${cam.installation_id}` : 'Не привязана');

      const lastSeen = cam.last_seen_at
        ? new Date(cam.last_seen_at).toLocaleString('ru-RU')
        : 'никогда';

      card.innerHTML = `
        <div class="cam-card-header">
          <div class="cam-title-wrap">
            <h3 class="cam-name">Камера #${cam.id}: ${escapeHtml(cam.auto_name)}</h3>
            <span class="cam-hwid">ID: <code>${escapeHtml(cam.hardware_id)}</code></span>
          </div>
          <span class="badge ${cam.is_online ? 'badge-success' : 'badge-danger'}">
            ${cam.is_online ? 'Онлайн' : 'Оффлайн'}
          </span>
        </div>

        <div class="cam-card-body">
          <div class="cam-param-row">
            <span class="cam-param-label">Привязана к:</span>
            <span class="cam-param-val" id="cam-unit-label-${cam.id}"><strong>${escapeHtml(assignedName)}</strong></span>
          </div>
          <div class="cam-param-row">
            <span class="cam-param-label">Последний сигнал:</span>
            <span class="cam-param-val">${escapeHtml(lastSeen)}</span>
          </div>
        </div>

        <div class="cam-card-actions">
          <div class="bind-select-wrap">
            <select class="form-select form-select-sm" id="cam-select-unit-${cam.id}">
              <option value="">-- Выберите установку --</option>
              ${this.units
                .map(
                  (u) =>
                    `<option value="${u.id}" ${u.id === cam.installation_id ? 'selected' : ''}>${escapeHtml(u.name)}</option>`
                )
                .join('')}
            </select>
            <button type="button" class="btn btn-secondary btn-sm btn-action-bind">Привязать</button>
            ${
              cam.installation_id
                ? '<button type="button" class="btn btn-secondary btn-sm btn-action-unbind">Отвязать</button>'
                : ''
            }
          </div>
          <button type="button" class="btn btn-secondary btn-sm btn-action-rename">✏️ Имя</button>
        </div>
      `;

      // Привязка
      card.querySelector('.btn-action-bind').addEventListener('click', async () => {
        const select = card.querySelector(`#cam-select-unit-${cam.id}`);
        const unitId = select.value;
        if (!unitId) {
          alert('Пожалуйста, выберите установку из списка.');
          return;
        }
        try {
          await api.bindCamera(cam.id, unitId);
          window.showToast?.(`Камера #${cam.id} успешно привязана`, 'success');
          await this.loadData();
        } catch (err) {
          alert('Ошибка привязки: ' + err.message);
        }
      });

      // Отвязка
      const unbindBtn = card.querySelector('.btn-action-unbind');
      if (unbindBtn) {
        unbindBtn.addEventListener('click', async () => {
          try {
            await api.unbindCamera(cam.id);
            window.showToast?.(`Камера #${cam.id} отвязана`, 'success');
            await this.loadData();
          } catch (err) {
            alert('Ошибка отвязки: ' + err.message);
          }
        });
      }

      // Переименование
      card.querySelector('.btn-action-rename').addEventListener('click', async () => {
        const newName = prompt(`Введите новое название для камеры #${cam.id}:`, cam.auto_name);
        if (newName === null) return;
        if (!newName.trim()) {
          alert('Название камеры не может быть пустым.');
          return;
        }
        try {
          await api.renameCamera(cam.id, newName.trim());
          window.showToast?.('Название камеры обновлено', 'success');
          await this.loadData();
        } catch (err) {
          alert('Ошибка переименования: ' + err.message);
        }
      });

      grid.appendChild(card);
    });
  }
}

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
