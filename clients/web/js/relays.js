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
          <span class="section-desc">Управление освещением и импульсными реле установок</span>
        </div>
        <div class="section-actions">
          <button type="button" class="btn btn-secondary btn-sm" id="relays-btn-refresh">🔄 Обновить</button>
          <button type="button" class="btn btn-primary btn-sm hidden" id="relays-btn-test">⚡ Тест реле</button>
        </div>
      </div>

      <div id="relays-alert-box" class="alert alert-error hidden"></div>
      <div id="relays-grid" class="relays-grid"></div>
    `;

    this.container.querySelector('#relays-btn-refresh').addEventListener('click', () => {
      this.loadRelays();
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
    const testBtn = this.container.querySelector('#relays-btn-test');
    if (testBtn) {
      if (this.userRole === 'developer') {
        testBtn.classList.remove('hidden');
      } else {
        testBtn.classList.add('hidden');
      }
    }

    const grid = this.container.querySelector('#relays-grid');
    grid.innerHTML = '';

    if (!this.relaysData || !this.relaysData.installations || this.relaysData.installations.length === 0) {
      grid.innerHTML = '<div class="empty-state">Нет активных установок для управления реле.</div>';
      return;
    }

    const pinStates = this.relaysData.pin_states || {};

    this.relaysData.installations.forEach((inst) => {
      const card = document.createElement('div');
      card.className = 'relay-card';

      const lightPin = inst.light_gpio_pin;
      const flashPin = inst.flash_gpio_pin;

      const isLightOn = Boolean(pinStates[String(lightPin)]);
      const isFlashOn = Boolean(pinStates[String(flashPin)]);

      card.innerHTML = `
        <div class="relay-card-header">
          <h3 class="relay-unit-name">${escapeHtml(inst.name)}</h3>
          <span class="relay-unit-culture">${escapeHtml(inst.culture_name)}</span>
        </div>

        <div class="relay-controls-list">
          <div class="relay-control-row">
            <div class="relay-info">
              <span class="relay-name">💡 Основной свет</span>
              <span class="relay-pin">GPIO ${lightPin}</span>
            </div>
            <div class="relay-toggle-wrap">
              <button type="button" class="btn ${isLightOn ? 'btn-success' : 'btn-secondary'} btn-sm btn-toggle-light">
                ${isLightOn ? '● ВКЛ' : '○ ВЫКЛ'}
              </button>
            </div>
          </div>

          <div class="relay-control-row">
            <div class="relay-info">
              <span class="relay-name">⚡ Вспышка (досветка)</span>
              <span class="relay-pin">GPIO ${flashPin}</span>
            </div>
            <div class="relay-toggle-wrap">
              <button type="button" class="btn ${isFlashOn ? 'btn-success' : 'btn-secondary'} btn-sm btn-toggle-flash">
                ${isFlashOn ? '● ВКЛ' : '○ ВЫКЛ'}
              </button>
            </div>
          </div>
        </div>
      `;

      // Переключение света
      card.querySelector('.btn-toggle-light').addEventListener('click', async () => {
        const nextState = !isLightOn;
        try {
          await api.setLight(inst.id, nextState);
          window.showToast?.(`Свет для «${inst.name}» (пин ${lightPin}) переключен`, 'success');
          await this.loadRelays();
        } catch (err) {
          alert('Ошибка переключения света: ' + err.message);
        }
      });

      // Переключение вспышки
      card.querySelector('.btn-toggle-flash').addEventListener('click', async () => {
        const nextState = !isFlashOn;
        try {
          await api.setFlash(inst.id, nextState);
          window.showToast?.(`Вспышка для «${inst.name}» (пин ${flashPin}) переключена`, 'success');
          await this.loadRelays();
        } catch (err) {
          alert('Ошибка переключения вспышки: ' + err.message);
        }
      });

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
