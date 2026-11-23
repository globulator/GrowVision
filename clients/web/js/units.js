// clients/web/js/units.js
// Управление гидропонными установками (просмотр, создание, редактирование, архивация, ручные снимки).

import { api } from './api.js';
import { showToast, showConfirm } from './dialogs.js';

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
          <h2 class="section-title">
            <svg class="icon-inline" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22v-8"></path><path d="M12 14c-3.5 0-6-2.5-6-6 4 0 6 2 6 6z"></path><path d="M12 14c3.5 0 6-2.5 6-6-4 0-6 2-6 6z"></path></svg>
            <span>Гидропонные установки</span>
          </h2>
          <label class="checkbox-label archive-toggle hidden" id="units-archive-toggle-wrap">
            <input type="checkbox" id="units-show-archived" />
            <span>Показать архивные</span>
          </label>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-primary hidden" id="units-btn-create">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
            <span>Создать установку</span>
          </button>
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
              <svg class="icon-inline" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="9" y1="18" x2="15" y2="18"></line><line x1="10" y1="22" x2="14" y2="22"></line><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path></svg>
              <span>Свет: ${lightState}</span>
            </button>
            <button type="button" class="btn btn-xs ${u.flash_state ? 'btn-success' : 'btn-secondary'} btn-unit-flash" title="Тумблер вспышки">
              <svg class="icon-inline" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
              <span>Вспышка: ${flashState}</span>
            </button>
          </div>
        `
            : ''
        }

        <div class="unit-card-actions">
          <button type="button" class="btn btn-primary btn-sm btn-action-gallery">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>
            <span>Галерея</span>
          </button>
          ${
            !isGuest
              ? `<button type="button" class="btn btn-secondary btn-sm btn-action-snap">
                   <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>
                   <span>Снимок</span>
                 </button>`
              : ''
          }
          ${
            !isGuest
              ? `<button type="button" class="btn btn-secondary btn-sm btn-action-edit">
                   <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path></svg>
                   <span>Редактировать</span>
                 </button>`
              : ''
          }
          ${
            isDev && !u.is_archived
              ? `<button type="button" class="btn btn-secondary btn-sm btn-action-archive">
                   <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="21 8 21 21 3 21 3 8"></polyline><rect x="1" y="3" width="22" height="5"></rect><line x1="10" y1="12" x2="14" y2="12"></line></svg>
                   <span>В архив</span>
                 </button>`
              : ''
          }
          ${
            isDev && u.is_archived
              ? `<button type="button" class="btn btn-secondary btn-sm btn-action-unarchive">
                   <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 2v6h6"></path><path d="M21 12A9 9 0 0 0 6 5.3L3 8"></path><path d="M21 22v-6h-6"></path><path d="M3 12a9 9 0 0 0 15 6.7l3-2.7"></path></svg>
                   <span>Восстановить</span>
                 </button>`
              : ''
          }
          ${
            isDev && u.is_archived
              ? `<button type="button" class="btn btn-danger btn-sm btn-action-delete">
                   <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                   <span>Удалить</span>
                 </button>`
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
            showToast(`Свет для «${u.name}» ${nextState ? 'включен' : 'выключен'}`, 'success');
            await this.loadUnits();
          } catch (err) {
            showToast('Ошибка переключения света: ' + err.message, 'error');
          }
        });
      }

      const flashBtn = card.querySelector('.btn-unit-flash');
      if (flashBtn) {
        flashBtn.addEventListener('click', async () => {
          const nextState = !u.flash_state;
          try {
            await api.setFlash(u.id, nextState);
            showToast(`Вспышка для «${u.name}» ${nextState ? 'включена' : 'выключена'}`, 'success');
            await this.loadUnits();
          } catch (err) {
            showToast('Ошибка переключения вспышки: ' + err.message, 'error');
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
          const ok = await showConfirm({
            title: 'Перевод в архив',
            message: `Перевести установку «${u.name}» в архив? Автосъемка будет прекращена.`,
            confirmText: 'В архив',
            danger: true,
          });
          if (!ok) return;

          try {
            await api.archiveInstallation(u.id);
            showToast('Установка переведена в архив', 'success');
            this.loadUnits();
          } catch (err) {
            showToast('Ошибка архивации: ' + err.message, 'error');
          }
        });
      }

      const unarchBtn = card.querySelector('.btn-action-unarchive');
      if (unarchBtn) {
        unarchBtn.addEventListener('click', async () => {
          const ok = await showConfirm({
            title: 'Восстановление установки',
            message: `Восстановить установку «${u.name}» из архива? Свет будет включён по умолчанию.`,
            confirmText: 'Восстановить',
          });
          if (!ok) return;

          try {
            const resp = await api.unarchiveInstallation(u.id);
            let msg = 'Установка успешно восстановлена из архива!';
            if (resp && resp.warnings && resp.warnings.length > 0) {
              msg += ' ' + resp.warnings.join(' ');
            }
            msg += ' Проверьте привязанные камеры и настройки GPIO-пинов.';
            showToast(msg, 'success', 6000);
            this.loadUnits();
          } catch (err) {
            showToast('Ошибка разархивации: ' + err.message, 'error');
          }
        });
      }

      const delBtn = card.querySelector('.btn-action-delete');
      if (delBtn) {
        delBtn.addEventListener('click', async () => {
          const ok = await showConfirm({
            title: 'Удаление установки',
            message: `Удалить архивную установку «${u.name}» и все её кадры безвозвратно?`,
            confirmText: 'Удалить',
            danger: true,
          });
          if (!ok) return;

          try {
            await api.deleteInstallation(u.id);
            showToast('Установка удалена', 'success');
            this.loadUnits();
          } catch (err) {
            showToast('Ошибка удаления: ' + err.message, 'error');
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
