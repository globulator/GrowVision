// clients/web/js/sessions.js
// Управление сессиями пользователей и одноразовыми инвайт-кодами (только Developer).

import { api } from './api.js';
import { showToast, showConfirm, showPrompt } from './dialogs.js';

export class SessionsManager {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.userRole = options.userRole || 'guest';
    this.invites = [];
    this.sessions = [];

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
            <svg class="icon-inline" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
            <span>Пользователи и сессии</span>
          </h2>
          <span class="section-desc">Управление доступом биологов, генерация кодов и завершение сессий</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-primary btn-sm" id="sess-btn-create-invite">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
            <span>Создать код для биолога</span>
          </button>
          <button type="button" class="btn btn-secondary btn-sm" id="sess-btn-refresh">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
            <span>Обновить</span>
          </button>
        </div>
      </div>

      <div id="sess-alert-box" class="alert alert-error hidden"></div>

      <!-- Блок сгенерированного кода -->
      <div id="sess-new-code-box" class="new-code-banner hidden">
        <div class="new-code-content">
          <span class="new-code-label">Сгенерирован одноразовый код (действует 10 минут):</span>
          <div class="new-code-row">
            <code id="sess-new-code-val" class="code-badge">—</code>
            <button type="button" class="btn btn-secondary btn-sm" id="sess-btn-copy-code">
              <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
              <span>Копировать</span>
            </button>
          </div>
        </div>
      </div>

      <!-- Активные инвайт-коды -->
      <div class="sub-section">
        <h3 class="sub-section-title">
          <svg class="icon-inline" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z"></path></svg>
          <span>Активные инвайт-коды</span>
        </h3>
        <div id="sess-invites-list" class="invites-list"></div>
      </div>

      <!-- Сессии пользователей -->
      <div class="sub-section">
        <h3 class="sub-section-title">
          <svg class="icon-inline" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
          <span>Сессии пользователей (все клиенты)</span>
        </h3>
        <div class="table-responsive">
          <table class="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Клиент</th>
                <th>Имя пользователя</th>
                <th>Роль</th>
                <th>Статус</th>
                <th>Авторизован</th>
                <th>Посл. активность</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody id="sess-table-body"></tbody>
          </table>
        </div>
      </div>
    `;

    this.container.querySelector('#sess-btn-refresh').addEventListener('click', () => {
      this.loadData();
    });

    this.container.querySelector('#sess-btn-create-invite').addEventListener('click', () => {
      this.handleCreateInvite();
    });

    this.container.querySelector('#sess-btn-copy-code').addEventListener('click', () => {
      const code = this.container.querySelector('#sess-new-code-val').textContent;
      if (code && code !== '—') {
        navigator.clipboard.writeText(code).then(() => {
          showToast(`Код ${code} скопирован в буфер обмена`, 'success');
        });
      }
    });
  }

  async loadData() {
    const alertBox = this.container.querySelector('#sess-alert-box');
    alertBox.classList.add('hidden');

    try {
      const [invites, sessions] = await Promise.all([
        api.getInvites(),
        api.getSessions(),
      ]);
      this.invites = invites || [];
      this.sessions = sessions || [];
      this.render();
    } catch (err) {
      alertBox.textContent = 'Не удалось загрузить данные сессий: ' + err.message;
      alertBox.classList.remove('hidden');
    }
  }

  async handleCreateInvite() {
    try {
      const resp = await api.createInvite();
      const codeBox = this.container.querySelector('#sess-new-code-box');
      const codeVal = this.container.querySelector('#sess-new-code-val');
      codeVal.textContent = resp.code;
      codeBox.classList.remove('hidden');

      showToast(`Создан код приглашения: ${resp.code}`, 'success');
      await this.loadData();
    } catch (err) {
      showToast('Ошибка генерации кода: ' + err.message, 'error');
    }
  }

  render() {
    // 1. Отрисовка активных кодов
    const invitesWrap = this.container.querySelector('#sess-invites-list');
    invitesWrap.innerHTML = '';

    if (!this.invites || this.invites.length === 0) {
      invitesWrap.innerHTML = '<div class="text-muted">Нет активных неиспользованных инвайт-кодов.</div>';
    } else {
      this.invites.forEach((inv) => {
        const item = document.createElement('div');
        item.className = 'invite-item';
        const expTime = new Date(inv.expires_at).toLocaleTimeString('ru-RU');

        item.innerHTML = `
          <div class="invite-item-info">
            <code class="code-badge">${escapeHtml(inv.code)}</code>
            <span class="invite-role">Роль: ${escapeHtml(inv.role)}</span>
            <span class="invite-exp">Истекает в: ${expTime}</span>
          </div>
          <button type="button" class="btn btn-danger btn-sm btn-action-revoke">Отозвать</button>
        `;

        item.querySelector('.btn-action-revoke').addEventListener('click', async () => {
          const ok = await showConfirm({
            title: 'Отзыв кода',
            message: `Отозвать код приглашения «${inv.code}»?`,
            confirmText: 'Отозвать',
            danger: true,
          });
          if (!ok) return;

          try {
            await api.revokeInvite(inv.id);
            showToast(`Код ${inv.code} отозван`, 'success');
            await this.loadData();
          } catch (err) {
            showToast('Ошибка отзыва кода: ' + err.message, 'error');
          }
        });

        invitesWrap.appendChild(item);
      });
    }

    // 2. Отрисовка сессий
    const tbody = this.container.querySelector('#sess-table-body');
    tbody.innerHTML = '';

    if (!this.sessions || this.sessions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center">Сессий не найдено.</td></tr>';
      return;
    }

    this.sessions.forEach((s) => {
      const tr = document.createElement('tr');
      const authTime = s.authorized_at ? new Date(s.authorized_at).toLocaleString('ru-RU') : '—';
      const lastAct = s.last_active_at ? new Date(s.last_active_at).toLocaleString('ru-RU') : '—';

      const clientBadgeClass =
        s.client_type === 'telegram'
          ? 'badge-telegram'
          : s.client_type === 'cli'
          ? 'badge-cli'
          : 'badge-web';

      const roleBadgeClass = s.role === 'developer' ? 'badge-primary' : 'badge-secondary';

      tr.innerHTML = `
        <td>${s.id}</td>
        <td><span class="badge ${clientBadgeClass}">${escapeHtml(s.client_type.toUpperCase())}</span></td>
        <td><strong>${escapeHtml(s.login)}</strong></td>
        <td><span class="badge ${roleBadgeClass}">${escapeHtml(s.role)}</span></td>
        <td>
          ${
            s.is_active
              ? '<span class="badge badge-success">Активна</span>'
              : '<span class="badge badge-danger">Завершена</span>'
          }
        </td>
        <td>${authTime}</td>
        <td>${lastAct}</td>
        <td>
          <div class="table-actions">
            <button type="button" class="btn btn-secondary btn-xs btn-rename-user" title="Переименовать">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path></svg>
            </button>
            ${
              s.is_active
                ? `<button type="button" class="btn btn-danger btn-xs btn-terminate-sess" title="Завершить сессию (кик)">
                     <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line></svg>
                   </button>`
                : ''
            }
          </div>
        </td>
      `;

      // Переименование
      tr.querySelector('.btn-rename-user').addEventListener('click', async () => {
        const newName = await showPrompt({
          title: 'Переименование пользователя',
          message: `Введите новое имя для пользователя «${s.login}» (сессия #${s.id}):`,
          defaultValue: s.login,
        });
        if (newName === null) return;
        if (!newName.trim()) {
          showToast('Имя не может быть пустым.', 'warning');
          return;
        }
        try {
          await api.renameSession(s.id, newName.trim());
          showToast('Пользователь переименован', 'success');
          await this.loadData();
        } catch (err) {
          showToast('Ошибка переименования: ' + err.message, 'error');
        }
      });

      // Кик
      const termBtn = tr.querySelector('.btn-terminate-sess');
      if (termBtn) {
        termBtn.addEventListener('click', async () => {
          const ok = await showConfirm({
            title: 'Завершение сессии',
            message: `Принудительно завершить сессию пользователя «${s.login}» (#${s.id})?`,
            confirmText: 'Завершить',
            danger: true,
          });
          if (!ok) return;

          try {
            await api.terminateSession(s.id);
            showToast(`Сессия #${s.id} завершена`, 'success');
            await this.loadData();
          } catch (err) {
            showToast('Ошибка завершения сессии: ' + err.message, 'error');
          }
        });
      }

      tbody.appendChild(tr);
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
