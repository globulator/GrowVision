// clients/web/js/sessions.js
// Управление сессиями пользователей и одноразовыми инвайт-кодами (только Developer).

import { api } from './api.js';

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
          <h2 class="section-title">👥 Пользователи и сессии</h2>
          <span class="section-desc">Управление доступом биологов, генерация кодов и завершение сессий</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-primary btn-sm" id="sess-btn-create-invite">➕ Создать код для биолога</button>
          <button type="button" class="btn btn-secondary btn-sm" id="sess-btn-refresh">🔄 Обновить</button>
        </div>
      </div>

      <div id="sess-alert-box" class="alert alert-error hidden"></div>

      <!-- Блок сгенерированного кода -->
      <div id="sess-new-code-box" class="new-code-banner hidden">
        <div class="new-code-content">
          <span class="new-code-label">Сгенерирован одноразовый код (действует 10 минут):</span>
          <div class="new-code-row">
            <code id="sess-new-code-val" class="code-badge">—</code>
            <button type="button" class="btn btn-secondary btn-sm" id="sess-btn-copy-code">📋 Копировать</button>
          </div>
        </div>
      </div>

      <!-- Активные инвайт-коды -->
      <div class="sub-section">
        <h3 class="sub-section-title">🎟 Активные инвайт-коды</h3>
        <div id="sess-invites-list" class="invites-list"></div>
      </div>

      <!-- Сессии пользователей -->
      <div class="sub-section">
        <h3 class="sub-section-title">💻 Сессии пользователей (все клиенты)</h3>
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
          window.showToast?.(`Код ${code} скопирован в буфер обмена`, 'success');
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

      window.showToast?.(`Создан код приглашения: ${resp.code}`, 'success');
      await this.loadData();
    } catch (err) {
      alert('Ошибка генерации кода: ' + err.message);
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
          if (!confirm(`Отозвать код ${inv.code}?`)) return;
          try {
            await api.revokeInvite(inv.id);
            window.showToast?.(`Код ${inv.code} отозван`, 'success');
            await this.loadData();
          } catch (err) {
            alert('Ошибка отзыва кода: ' + err.message);
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
            <button type="button" class="btn btn-secondary btn-xs btn-rename-user" title="Переименовать">✏️</button>
            ${
              s.is_active
                ? '<button type="button" class="btn btn-danger btn-xs btn-terminate-sess" title="Завершить сессию (кик)">🚫</button>'
                : ''
            }
          </div>
        </td>
      `;

      // Переименование
      tr.querySelector('.btn-rename-user').addEventListener('click', async () => {
        const newName = prompt(`Введите новое имя для пользователя «${s.login}» (сессия #${s.id}):`, s.login);
        if (newName === null) return;
        if (!newName.trim()) {
          alert('Имя не может быть пустым.');
          return;
        }
        try {
          await api.renameSession(s.id, newName.trim());
          window.showToast?.('Пользователь переименован', 'success');
          await this.loadData();
        } catch (err) {
          alert('Ошибка переименования: ' + err.message);
        }
      });

      // Кик
      const termBtn = tr.querySelector('.btn-terminate-sess');
      if (termBtn) {
        termBtn.addEventListener('click', async () => {
          if (!confirm(`Принудительно завершить сессию пользователя «${s.login}» (#${s.id})?`)) return;
          try {
            await api.terminateSession(s.id);
            window.showToast?.(`Сессия #${s.id} завершена`, 'success');
            await this.loadData();
          } catch (err) {
            alert('Ошибка завершения сессии: ' + err.message);
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
