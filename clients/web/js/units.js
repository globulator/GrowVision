// clients/web/js/units.js
// Управление гидропонными установками (просмотр, создание, редактирование, архивация, ручные снимки).

import { api } from './api.js';

export class UnitsManager {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.userRole = options.userRole || 'guest';
    this.onSelectInstallation = options.onSelectInstallation || (() => {});
    this.showArchived = false;
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
          <h2 class="section-title">🌿 Гидропонные установки</h2>
          <label class="checkbox-label archive-toggle hidden" id="units-archive-toggle-wrap">
            <input type="checkbox" id="units-show-archived" />
            <span>Показать архивные</span>
          </label>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-primary hidden" id="units-btn-create">➕ Создать установку</button>
        </div>
      </div>

      <div id="units-alert-box" class="alert alert-error hidden"></div>
      <div id="units-grid" class="units-grid"></div>
    `;

    this.container.querySelector('#units-show-archived').addEventListener('change', (e) => {
      this.showArchived = e.target.checked;
      this.loadUnits();
    });

    this.container.querySelector('#units-btn-create').addEventListener('click', () => {
      if (window.showUnitEditModal) {
        window.showUnitEditModal(null, () => this.loadUnits());
      }
    });
  }

  async loadUnits() {
    const alertBox = this.container.querySelector('#units-alert-box');
    alertBox.classList.add('hidden');
    const grid = this.container.querySelector('#units-grid');
    grid.innerHTML = '<div class="loading-spinner">Загрузка установок...</div>';

    try {
      this.units = await api.getInstallations(this.showArchived);
      this.render();
    } catch (err) {
      grid.innerHTML = '';
      alertBox.textContent = 'Не удалось загрузить список установок: ' + err.message;
      alertBox.classList.remove('hidden');
    }
  }

  render() {
    const isDev = this.userRole === 'developer';
    const isBio = this.userRole === 'biologist';
    const isGuest = this.userRole === 'guest';

    const createBtn = this.container.querySelector('#units-btn-create');
    if (createBtn) {
      if (isDev) {
        createBtn.classList.remove('hidden');
      } else {
        createBtn.classList.add('hidden');
      }
    }

    const archiveToggle = this.container.querySelector('#units-archive-toggle-wrap');
    if (archiveToggle) {
      if (isDev) {
        archiveToggle.classList.remove('hidden');
      } else {
        archiveToggle.classList.add('hidden');
      }
    }

    const grid = this.container.querySelector('#units-grid');
    grid.innerHTML = '';

    if (!this.units || this.units.length === 0) {
      grid.innerHTML = '<div class="empty-state">Установок пока нет.</div>';
      return;
    }

    this.units.forEach((u) => {
      const card = document.createElement('div');
      card.className = `unit-card ${u.is_archived ? 'unit-card-archived' : ''}`;

      const lightState = u.light_state ? 'ВКЛ' : 'ВЫКЛ';
      const flashState = u.flash_state ? 'ВКЛ' : 'ВЫКЛ';

      card.innerHTML = `
        <div class="unit-card-header">
          <div class="unit-card-title-wrap">
            <h3 class="unit-name">${escapeHtml(u.name)}</h3>
            <span class="unit-culture">${escapeHtml(u.culture_name)}</span>
          </div>
          ${u.is_archived ? '<span class="badge badge-warning">Архив</span>' : '<span class="badge badge-success">Активна</span>'}
        </div>

        <!-- Превью последнего кадра -->
        <div class="unit-preview-wrap">
          ${
            u.last_frame_raw_url
              ? `<img src="${escapeHtml(u.last_frame_raw_url)}" alt="${escapeHtml(u.name)}" class="unit-preview-img" loading="lazy" />`
              : `<div class="unit-preview-placeholder">
                  <svg class="icon-leaf" viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 20A7 7 0 0 1 4 13C4 8.5 8 3 20 4c1 12-4.5 16-9 16Z"/><path d="M4 20l7-7"/></svg>
                  <span>Нет снимков</span>
                </div>`
          }
        </div>

        <div class="unit-card-body">
          <div class="unit-param-row">
            <span class="unit-param-label">Последняя площадь:</span>
            <span class="unit-param-value">${escapeHtml(u.last_area_display || '—')}</span>
          </div>
          <div class="unit-param-row">
            <span class="unit-param-label">Камер привязано:</span>
            <span class="unit-param-value">${u.cameras_count !== undefined ? u.cameras_count : '—'}</span>
          </div>
          <div class="unit-param-row">
            <span class="unit-param-label">Плановых кадров:</span>
            <span class="unit-param-value">${u.captures_count !== undefined ? u.captures_count : '—'}</span>
          </div>
          ${
            isDev
              ? `
            <div class="unit-param-row unit-pins-row">
              <span class="unit-param-label">GPIO Свет:</span>
              <span class="unit-param-value">Пин ${u.light_gpio_pin}</span>
            </div>
            <div class="unit-param-row unit-pins-row">
              <span class="unit-param-label">GPIO Вспышка:</span>
              <span class="unit-param-value">Пин ${u.flash_gpio_pin}</span>
            </div>
          `
              : ''
          }
        </div>

        ${
          isDev && !u.is_archived
            ? `
          <div class="unit-relays-panel">
            <button type="button" class="btn btn-xs ${u.light_state ? 'btn-success' : 'btn-secondary'} btn-unit-light" title="Тумблер света">
              💡 Свет: ${lightState}
            </button>
            <button type="button" class="btn btn-xs ${u.flash_state ? 'btn-success' : 'btn-secondary'} btn-unit-flash" title="Тумблер вспышки">
              ⚡ Вспышка: ${flashState}
            </button>
          </div>
        `
            : ''
        }

        <div class="unit-card-actions">
          <button type="button" class="btn btn-primary btn-sm btn-action-gallery">🖼 Галерея</button>
          ${!isGuest ? '<button type="button" class="btn btn-secondary btn-sm btn-action-snap">📸 Снимок</button>' : ''}
          ${!isGuest ? '<button type="button" class="btn btn-secondary btn-sm btn-action-edit">✏️ Редактировать</button>' : ''}
          ${
            isDev && !u.is_archived
              ? '<button type="button" class="btn btn-secondary btn-sm btn-action-archive">📦 В архив</button>'
              : ''
          }
          ${
            isDev && u.is_archived
              ? '<button type="button" class="btn btn-secondary btn-sm btn-action-unarchive">Разархивировать</button>'
              : ''
          }
          ${
            isDev && u.is_archived
              ? '<button type="button" class="btn btn-danger btn-sm btn-action-delete">🗑 Удалить</button>'
              : ''
          }
        </div>
      `;

      // Привязка событий
      card.querySelector('.btn-action-gallery').addEventListener('click', () => {
        this.onSelectInstallation(u);
      });

      // Тумблеры света и вспышки (Developer)
      const lightBtn = card.querySelector('.btn-unit-light');
      if (lightBtn) {
        lightBtn.addEventListener('click', async () => {
          const nextState = !u.light_state;
          try {
            await api.setLight(u.id, nextState);
            window.showToast?.(`Свет для «${u.name}» ${nextState ? 'включен' : 'выключен'}`, 'success');
            await this.loadUnits();
          } catch (err) {
            alert('Ошибка переключения света: ' + err.message);
          }
        });
      }

      const flashBtn = card.querySelector('.btn-unit-flash');
      if (flashBtn) {
        flashBtn.addEventListener('click', async () => {
          const nextState = !u.flash_state;
          try {
            await api.setFlash(u.id, nextState);
            window.showToast?.(`Вспышка для «${u.name}» ${nextState ? 'включена' : 'выключена'}`, 'success');
            await this.loadUnits();
          } catch (err) {
            alert('Ошибка переключения вспышки: ' + err.message);
          }
        });
      }

      const snapBtn = card.querySelector('.btn-action-snap');
      if (snapBtn) {
        snapBtn.addEventListener('click', () => this.handleSnap(u));
      }

      const editBtn = card.querySelector('.btn-action-edit');
      if (editBtn) {
        editBtn.addEventListener('click', () => {
          if (window.showUnitEditModal) {
            window.showUnitEditModal(u, () => this.loadUnits());
          }
        });
      }

      const archBtn = card.querySelector('.btn-action-archive');
      if (archBtn) {
        archBtn.addEventListener('click', async () => {
          if (!confirm(`Перевести установку «${u.name}» в архив?`)) return;
          try {
            await api.archiveInstallation(u.id);
            window.showToast?.('Установка переведена в архив', 'success');
            this.loadUnits();
          } catch (err) {
            alert('Ошибка архивации: ' + err.message);
          }
        });
      }

      const unarchBtn = card.querySelector('.btn-action-unarchive');
      if (unarchBtn) {
        unarchBtn.addEventListener('click', async () => {
          try {
            await api.unarchiveInstallation(u.id);
            window.showToast?.('Установка разархивирована', 'success');
            this.loadUnits();
          } catch (err) {
            alert('Ошибка разархивации: ' + err.message);
          }
        });
      }

      const delBtn = card.querySelector('.btn-action-delete');
      if (delBtn) {
        delBtn.addEventListener('click', async () => {
          if (!confirm(`Удалить архивную установку «${u.name}» и все её кадры безвозвратно?`)) return;
          try {
            await api.deleteInstallation(u.id);
            window.showToast?.('Установка удалена', 'success');
            this.loadUnits();
          } catch (err) {
            alert('Ошибка удаления: ' + err.message);
          }
        });
      }

      grid.appendChild(card);
    });
  }

  async handleSnap(unit) {
    if (window.showSnapModal) {
      window.showSnapModal(unit, this.userRole);
    }
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
