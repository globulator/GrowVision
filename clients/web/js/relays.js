// clients/web/js/relays.js
// Управление светом, вспышкой и тестом реле с синхронизацией состояний по GPIO-пинам.

import { api } from './api.js';
import { showToast, showConfirm } from './dialogs.js';

export class RelaysManager {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.userRole = options.userRole || 'guest';
    this.relaysData = null; // { pin_states: {...}, installations: [...] }

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
            <svg class="icon-inline" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="9" y1="18" x2="15" y2="18"></line><line x1="10" y1="22" x2="14" y2="22"></line><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path></svg>
            <span>Свет и реле</span>
          </h2>
          <span class="section-desc">Общие групповые операции управления реле установок</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-secondary btn-sm" id="relays-btn-refresh">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
            <span>Обновить</span>
          </button>
        </div>
      </div>

      <div id="relays-alert-box" class="alert alert-error hidden"></div>

      <!-- Панель общих групповых действий -->
      <div class="global-relays-card">
        <h3 class="global-relays-title">Групповые действия для всех активных установок</h3>
        <p class="global-relays-desc">
          Переключение всех реле одновременно или запуск тестовой последовательности на плате Raspberry Pi.
          Индивидуальное управление реле конкретной установки доступно на карточках во вкладке «Установки».
        </p>

        <div class="global-relays-actions">
          <button type="button" class="btn btn-primary" id="relays-btn-all-light-on">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="9" y1="18" x2="15" y2="18"></line><line x1="10" y1="22" x2="14" y2="22"></line><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path></svg>
            <span>Включить весь свет</span>
          </button>
          <button type="button" class="btn btn-secondary" id="relays-btn-all-light-off">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="9" y1="18" x2="15" y2="18"></line><line x1="10" y1="22" x2="14" y2="22"></line><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path></svg>
            <span>Выключить весь свет</span>
          </button>
          <button type="button" class="btn btn-primary" id="relays-btn-all-flash-on">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
            <span>Включить все вспышки</span>
          </button>
          <button type="button" class="btn btn-secondary" id="relays-btn-all-flash-off">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
            <span>Выключить все вспышки</span>
          </button>
          <button type="button" class="btn btn-warning" id="relays-btn-test">
            <svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 2v7.31"></path><path d="M14 9.3V2"></path><path d="M8.5 2h7"></path><path d="M14 9.3a6.5 6.5 0 1 1-4 0"></path><path d="M5.52 16h12.96"></path></svg>
            <span>Тест всех реле (3 сек)</span>
          </button>
        </div>
      </div>

      <div class="relays-overview-section">
        <h3 class="relays-overview-title">Сводная карта активных GPIO-пинов</h3>
        <div id="relays-grid" class="relays-overview-grid"></div>
      </div>
    `;

    this.container.querySelector('#relays-btn-refresh').addEventListener('click', () => {
      this.loadRelays();
    });

    this.container.querySelector('#relays-btn-all-light-on').addEventListener('click', async () => {
      try {
        await api.setLight('all', true);
        showToast('Основной свет включен на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        showToast('Ошибка переключения света: ' + err.message, 'error');
      }
    });

    this.container.querySelector('#relays-btn-all-light-off').addEventListener('click', async () => {
      try {
        await api.setLight('all', false);
        showToast('Основной свет выключен на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        showToast('Ошибка переключения света: ' + err.message, 'error');
      }
    });

    this.container.querySelector('#relays-btn-all-flash-on').addEventListener('click', async () => {
      try {
        await api.setFlash('all', true);
        showToast('Вспышка включена на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        showToast('Ошибка переключения вспышки: ' + err.message, 'error');
      }
    });

    this.container.querySelector('#relays-btn-all-flash-off').addEventListener('click', async () => {
      try {
        await api.setFlash('all', false);
        showToast('Вспышка выключена на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        showToast('Ошибка переключения вспышки: ' + err.message, 'error');
      }
    });

    this.container.querySelector('#relays-btn-test').addEventListener('click', () => {
      this.handleRelayTest();
    });
  }

  async loadRelays() {
    const alertBox = this.container.querySelector('#relays-alert-box');
    alertBox.classList.add('hidden');
    const grid = this.container.querySelector('#relays-grid');
    grid.innerHTML = '<div class="loading-spinner">Опрос состояний реле...</div>';

    try {
      this.relaysData = await api.getRelays();
      this.render();
    } catch (err) {
      grid.innerHTML = '';
      alertBox.textContent = 'Не удалось получить состояния реле: ' + err.message;
      alertBox.classList.remove('hidden');
    }
  }

  render() {
    const grid = this.container.querySelector('#relays-grid');
    grid.innerHTML = '';

    if (!this.relaysData || !this.relaysData.installations || this.relaysData.installations.length === 0) {
      grid.innerHTML = '<div class="empty-state">Нет активных установок для управления реле.</div>';
      return;
    }

    const pinStates = this.relaysData.pin_states || {};

    // Группировка информации по используемым пинам
    const pinMap = new Map();
    this.relaysData.installations.forEach((inst) => {
      const lPin = inst.light_gpio_pin;
      const fPin = inst.flash_gpio_pin;

      if (!pinMap.has(lPin)) pinMap.set(lPin, { pin: lPin, roles: new Set(), insts: [] });
      pinMap.get(lPin).roles.add('Основной свет');
      pinMap.get(lPin).insts.push(inst.name);

      if (!pinMap.has(fPin)) pinMap.set(fPin, { pin: fPin, roles: new Set(), insts: [] });
      pinMap.get(fPin).roles.add('Вспышка');
      if (!pinMap.get(fPin).insts.includes(inst.name)) {
        pinMap.get(fPin).insts.push(inst.name);
      }
    });

    Array.from(pinMap.values())
      .sort((a, b) => a.pin - b.pin)
      .forEach((item) => {
        const card = document.createElement('div');
        card.className = 'relay-pin-card';
        const isOn = Boolean(pinStates[String(item.pin)]);

        card.innerHTML = `
          <div class="relay-pin-header">
            <span class="relay-pin-badge">GPIO ${item.pin}</span>
            <span class="badge ${isOn ? 'badge-success' : 'badge-secondary'}">${isOn ? 'ВКЛ' : 'ВЫКЛ'}</span>
          </div>
          <div class="relay-pin-body">
            <div class="relay-pin-row">
              <span class="relay-pin-label">Назначение:</span>
              <span class="relay-pin-val">${Array.from(item.roles).join(', ')}</span>
            </div>
            <div class="relay-pin-row">
              <span class="relay-pin-label">Установки:</span>
              <span class="relay-pin-val">${escapeHtml(item.insts.join(', '))}</span>
            </div>
          </div>
        `;
        grid.appendChild(card);
      });
  }

  async handleRelayTest() {
    const ok = await showConfirm({
      title: 'Тестирование реле',
      message: 'Запустить цикл тестирования всех реле (последовательное переключение пинов на Raspberry Pi на 3 секунды)?',
      confirmText: 'Запустить тест',
    });
    if (!ok) return;

    try {
      showToast('Запуск теста реле...', 'info');
      await api.testRelays('all');
      showToast('Тест реле успешно завершён', 'success');
      await this.loadRelays();
    } catch (err) {
      showToast('Ошибка теста реле: ' + err.message, 'error');
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
