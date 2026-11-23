// clients/web/js/relays.js
// Управление светом, вспышкой и тестом реле с синхронизацией состояний по GPIO-пинам.

import { api } from './api.js';

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
          <h2 class="section-title">💡 Свет и реле</h2>
          <span class="section-desc">Общие групповые операции управления реле установок</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-secondary btn-sm" id="relays-btn-refresh">🔄 Обновить</button>
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
          <button type="button" class="btn btn-primary" id="relays-btn-all-light-on">💡 Включить весь свет</button>
          <button type="button" class="btn btn-secondary" id="relays-btn-all-light-off">💡 Выключить весь свет</button>
          <button type="button" class="btn btn-primary" id="relays-btn-all-flash-on">⚡ Включить все вспышки</button>
          <button type="button" class="btn btn-secondary" id="relays-btn-all-flash-off">⚡ Выключить все вспышки</button>
          <button type="button" class="btn btn-warning" id="relays-btn-test">🧪 Тест всех реле (3 сек)</button>
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
        window.showToast?.('Основной свет включен на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        alert('Ошибка переключения света: ' + err.message);
      }
    });

    this.container.querySelector('#relays-btn-all-light-off').addEventListener('click', async () => {
      try {
        await api.setLight('all', false);
        window.showToast?.('Основной свет выключен на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        alert('Ошибка переключения света: ' + err.message);
      }
    });

    this.container.querySelector('#relays-btn-all-flash-on').addEventListener('click', async () => {
      try {
        await api.setFlash('all', true);
        window.showToast?.('Вспышка включена на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        alert('Ошибка переключения вспышки: ' + err.message);
      }
    });

    this.container.querySelector('#relays-btn-all-flash-off').addEventListener('click', async () => {
      try {
        await api.setFlash('all', false);
        window.showToast?.('Вспышка выключена на всех установках', 'success');
        await this.loadRelays();
      } catch (err) {
        alert('Ошибка переключения вспышки: ' + err.message);
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
    if (!confirm('Запустить цикл тестирования всех реле (последовательное переключение пинов на Raspberry Pi)?')) return;
    try {
      window.showToast?.('Запуск теста реле...', 'info');
      await api.testRelays();
      window.showToast?.('Тест реле успешно завершён', 'success');
      await this.loadRelays();
    } catch (err) {
      alert('Ошибка теста реле: ' + err.message);
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
