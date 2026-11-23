// clients/web/js/cameras.js
// Управление камерами установки: список, сканирование, привязка/отвязка, редактирование.

import { api } from './api.js';
import { showToast, showPrompt } from './dialogs.js';

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
          <h2 class="section-title">
            <svg class="icon-inline" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>
            <span>Камеры видеонаблюдения</span>
          </h2>
          <span class="section-desc">Список физических камер Raspberry Pi и привязка к установкам</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-primary btn-sm" id="cams-btn-scan">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <span>Сканировать камеры</span>
          </button>
          <button type="button" class="btn btn-secondary btn-sm" id="cams-btn-refresh">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
            <span>Обновить</span>
          </button>
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
    scanBtn.innerHTML = `
      <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
      <span>Сканирование...</span>
    `;

    try {
      const resp = await api.scanCameras();
      const foundCount = resp.cameras ? resp.cameras.length : 0;
      showToast(`Сканирование завершено: найдено камер: ${foundCount}`, 'success');

      if (resp.debug_info && resp.debug_info.log_line) {
        debugBox.innerHTML = `<strong>Лог сканирования:</strong> <code>${escapeHtml(resp.debug_info.log_line)}</code>`;
        debugBox.classList.remove('hidden');
      }

      await this.loadData();
    } catch (err) {
      showToast('Ошибка сканирования камер: ' + err.message, 'error');
    } finally {
      scanBtn.disabled = false;
      scanBtn.innerHTML = `
        <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        <span>Сканировать камеры</span>
      `;
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
          <button type="button" class="btn btn-secondary btn-sm btn-action-rename">
            <svg class="icon-inline" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path></svg>
            <span>Имя</span>
          </button>
        </div>
      `;

      // Привязка
      card.querySelector('.btn-action-bind').addEventListener('click', async () => {
        const select = card.querySelector(`#cam-select-unit-${cam.id}`);
        const unitId = select.value;
        if (!unitId) {
          showToast('Пожалуйста, выберите установку из списка.', 'warning');
          return;
        }
        try {
          await api.bindCamera(cam.id, Number(unitId));
          showToast(`Камера #${cam.id} успешно привязана`, 'success');
          await this.loadData();
        } catch (err) {
          showToast('Ошибка привязки: ' + err.message, 'error');
        }
      });

      // Отвязка
      const unbindBtn = card.querySelector('.btn-action-unbind');
      if (unbindBtn) {
        unbindBtn.addEventListener('click', async () => {
          try {
            await api.unbindCamera(cam.id);
            showToast(`Камера #${cam.id} отвязана`, 'success');
            await this.loadData();
          } catch (err) {
            showToast('Ошибка отвязки: ' + err.message, 'error');
          }
        });
      }

      // Переименование
      card.querySelector('.btn-action-rename').addEventListener('click', async () => {
        const newName = await showPrompt({
          title: 'Переименование камеры',
          message: `Введите новое название для камеры #${cam.id}:`,
          defaultValue: cam.auto_name,
        });
        if (newName === null) return;
        if (!newName.trim()) {
          showToast('Название камеры не может быть пустым.', 'warning');
          return;
        }
        try {
          await api.renameCamera(cam.id, newName.trim());
          showToast('Название камеры обновлено', 'success');
          await this.loadData();
        } catch (err) {
          showToast('Ошибка переименования: ' + err.message, 'error');
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
