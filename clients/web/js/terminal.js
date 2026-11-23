// clients/web/js/terminal.js
// Интерактивная веб-консоль GrowVision для разработчика.

import { api } from './api.js';

export class TerminalManager {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.userRole = options.userRole || 'guest';
    this.history = [];
    this.historyIndex = -1;

    this.initDOM();
  }

  setRole(role) {
    this.userRole = role;
  }

  initDOM() {
    this.container.innerHTML = `
      <div class="terminal-wrapper">
        <div class="terminal-header">
          <div class="terminal-dots">
            <span class="dot dot-red"></span>
            <span class="dot dot-yellow"></span>
            <span class="dot dot-green"></span>
          </div>
          <div class="terminal-title">GrowVision Terminal v0.3.3 (CLI Console)</div>
          <button type="button" class="btn btn-secondary btn-xs" id="term-btn-clear">Очистить</button>
        </div>

        <div class="terminal-screen" id="term-screen">
          <div class="terminal-output" id="term-output">
            <div class="term-line term-welcome">
Добро пожаловать в веб-консоль GrowVision!
Введите <span class="term-highlight">help</span> для просмотра списка доступных команд.
            </div>
          </div>

          <div class="terminal-input-row">
            <span class="term-prompt">growvision&gt;</span>
            <input type="text" id="term-input" class="term-input" autocomplete="off" spellcheck="false" autofocus />
          </div>
        </div>
      </div>
    `;

    const input = this.container.querySelector('#term-input');
    const clearBtn = this.container.querySelector('#term-btn-clear');

    clearBtn.addEventListener('click', () => {
      this.clearScreen();
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const val = input.value.trim();
        input.value = '';
        if (val) {
          this.history.push(val);
          this.historyIndex = this.history.length;
          this.executeCommand(val);
        }
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (this.history.length > 0 && this.historyIndex > 0) {
          this.historyIndex--;
          input.value = this.history[this.historyIndex];
        }
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (this.historyIndex < this.history.length - 1) {
          this.historyIndex++;
          input.value = this.history[this.historyIndex];
        } else {
          this.historyIndex = this.history.length;
          input.value = '';
        }
      }
    });

    this.container.addEventListener('click', () => {
      input.focus();
    });
  }

  clearScreen() {
    const out = this.container.querySelector('#term-output');
    out.innerHTML = '';
  }

  printLine(html, type = 'normal') {
    const out = this.container.querySelector('#term-output');
    const line = document.createElement('div');
    line.className = `term-line term-${type}`;
    line.innerHTML = html;
    out.appendChild(line);

    const screen = this.container.querySelector('#term-screen');
    screen.scrollTop = screen.scrollHeight;
  }

  async executeCommand(rawCmd) {
    this.printLine(`<span class="term-prompt">growvision&gt;</span> ${escapeHtml(rawCmd)}`, 'cmd');

    const parts = rawCmd.split(/\s+/).filter(Boolean);
    const cmd = parts[0]?.toLowerCase();
    const args = parts.slice(1);

    try {
      switch (cmd) {
        case 'help':
          this.cmdHelp();
          break;
        case 'clear':
          this.clearScreen();
          break;
        case 'status':
          await this.cmdStatus();
          break;
        case 'unit':
          await this.cmdUnit(args);
          break;
        case 'cam':
          await this.cmdCam(args);
          break;
        case 'light':
          await this.cmdLight(args);
          break;
        case 'flash':
          await this.cmdFlash(args);
          break;
        case 'snap':
          await this.cmdSnap(args);
          break;
        case 'relay-test':
          await this.cmdRelayTest();
          break;
        case 'auth':
          await this.cmdAuth(args);
          break;
        case 'sys':
          await this.cmdSys(args);
          break;
        default:
          this.printLine(`Неизвестная команда: "${escapeHtml(cmd)}". Введите <span class="term-highlight">help</span> для справки.`, 'error');
          break;
      }
    } catch (err) {
      this.printLine(`Ошибка: ${escapeHtml(err.message)}`, 'error');
    }
  }

  cmdHelp() {
    this.printLine(`
<strong>Доступные команды веб-консоли:</strong>
  <span class="term-highlight">help</span>                               Справка по командам
  <span class="term-highlight">status</span>                             Общий статус системы и устройств
  <span class="term-highlight">unit list [archived]</span>               Список установок
  <span class="term-highlight">unit get &lt;id&gt;</span>                        Информация об установке
  <span class="term-highlight">unit create &lt;name&gt; &lt;culture&gt; &lt;light_pin&gt; &lt;flash_pin&gt;</span>  Создание установки
  <span class="term-highlight">cam list</span>                             Список камер
  <span class="term-highlight">cam scan</span>                             Сканирование подключенных камер
  <span class="term-highlight">cam bind &lt;cam_id&gt; &lt;unit_id&gt;</span>          Привязка камеры к установке
  <span class="term-highlight">cam unbind &lt;cam_id&gt;</span>                  Отвязка камеры от установки
  <span class="term-highlight">light on|off &lt;unit_id|all&gt;</span>           Управление основным светом
  <span class="term-highlight">flash on|off &lt;unit_id|all&gt;</span>           Управление вспышкой (досветкой)
  <span class="term-highlight">snap &lt;unit_id|all&gt;</span>                   Ручной снимок с установок
  <span class="term-highlight">relay-test</span>                           Запуск теста реле на Raspberry Pi
  <span class="term-highlight">auth invites</span>                         Список активных инвайт-кодов
  <span class="term-highlight">auth invite create</span>                   Создать одноразовый код (TTL 10 мин)
  <span class="term-highlight">auth sessions</span>                        Список всех сессий пользователей
  <span class="term-highlight">auth rename &lt;sess_id&gt; &lt;name&gt;</span>        Переименовать пользователя
  <span class="term-highlight">auth kick &lt;sess_id&gt;</span>                  Завершить сессию пользователя
  <span class="term-highlight">sys status</span>                           Статус настроек и Edge-агента
  <span class="term-highlight">sys debug on|off</span>                     Включить/выключить режим отладки
  <span class="term-highlight">sys auto-capture on|off</span>              Включить/выключить автосъёмку
  <span class="term-highlight">clear</span>                                Очистить экран консоли
    `, 'info');
  }

  async cmdStatus() {
    const [units, relays, telemetry, version] = await Promise.all([
      api.getInstallations(false),
      api.getRelays().catch(() => null),
      api.getEdgeTelemetry().catch(() => null),
      api.getVersion().catch(() => null),
    ]);

    const vStr = version ? `v${version.server_version}` : 'неизвестно';
    const edgeOnline = telemetry && telemetry.is_online ? '[Онлайн]' : '[Оффлайн]';

    let html = `<strong>Статус системы:</strong>\n`;
    html += `  Версия сервера: ${vStr}\n`;
    html += `  Raspberry Pi (Edge): ${edgeOnline}\n`;
    html += `  Активных установок: ${units ? units.length : 0}\n`;

    if (relays && relays.pin_states) {
      const pinList = Object.entries(relays.pin_states)
        .map(([pin, st]) => `Пин ${pin}: ${st ? 'ON' : 'OFF'}`)
        .join(', ');
      html += `  Реле пинов: ${pinList || 'нет активных'}\n`;
    }

    this.printLine(`<pre>${html}</pre>`, 'normal');
  }

  async cmdUnit(args) {
    const sub = args[0]?.toLowerCase();
    if (sub === 'list') {
      const includeArchived = args[1]?.toLowerCase() === 'archived';
      const units = await api.getInstallations(includeArchived);
      if (!units || units.length === 0) {
        this.printLine('Установок пока нет.', 'normal');
        return;
      }
      let out = `Установки (${units.length}):\n`;
      units.forEach((u) => {
        out += `  [#${u.id}] ${u.name} (${u.culture_name}) | Свет: GPIO ${u.light_gpio_pin}, Вспышка: GPIO ${u.flash_gpio_pin} | ${u.is_archived ? 'АРХИВ' : 'АКТИВНА'}\n`;
      });
      this.printLine(`<pre>${escapeHtml(out)}</pre>`, 'normal');
    } else if (sub === 'get') {
      const id = parseInt(args[1], 10);
      if (isNaN(id)) {
        this.printLine('Использование: unit get <id>', 'error');
        return;
      }
      const u = await api.getInstallation(id);
      let out = `Установка #${u.id}:\n`;
      out += `  Название: ${u.name}\n  Культура: ${u.culture_name}\n  GPIO Свет: ${u.light_gpio_pin}\n  GPIO Вспышка: ${u.flash_gpio_pin}\n  Размер маркера ArUco: ${u.aruco_size_mm || '—'} мм\n  Задержка съёмки: ${u.capture_delay_sec || '—'} с\n  Статус: ${u.is_archived ? 'В архиве' : 'Активна'}\n`;
      this.printLine(`<pre>${escapeHtml(out)}</pre>`, 'normal');
    } else if (sub === 'create') {
      if (args.length < 5) {
        this.printLine('Использование: unit create <name> <culture> <light_pin> <flash_pin>', 'error');
        return;
      }
      const [name, culture, lightPin, flashPin] = args.slice(1);
      const resp = await api.createInstallation({
        name,
        culture_name: culture,
        light_gpio_pin: parseInt(lightPin, 10),
        flash_gpio_pin: parseInt(flashPin, 10),
      });
      this.printLine(`Установка «${resp.name}» (#${resp.id}) успешно создана!`, 'success');
      if (resp.warnings && resp.warnings.length > 0) {
        resp.warnings.forEach((w) => this.printLine(`[Предупреждение] ${escapeHtml(w)}`, 'warning'));
      }
    } else {
      this.printLine('Использование: unit list [archived] | unit get <id> | unit create ...', 'error');
    }
  }

  async cmdCam(args) {
    const sub = args[0]?.toLowerCase();
    if (sub === 'list') {
      const cams = await api.getCameras();
      if (!cams || cams.length === 0) {
        this.printLine('Камер в системе нет.', 'normal');
        return;
      }
      let out = `Камеры (${cams.length}):\n`;
      cams.forEach((c) => {
        const inst = c.installation_id ? `Установка #${c.installation_id}` : 'Не привязана';
        out += `  [#${c.id}] ${c.auto_name} (HW: ${c.hardware_id}) | ${c.is_online ? 'ОНЛАЙН' : 'ОФФЛАЙН'} | ${inst}\n`;
      });
      this.printLine(`<pre>${escapeHtml(out)}</pre>`, 'normal');
    } else if (sub === 'scan') {
      this.printLine('Запуск сканирования камер...', 'info');
      const resp = await api.scanCameras();
      const count = resp.cameras ? resp.cameras.length : 0;
      this.printLine(`Сканирование завершено: найдено камер: ${count}`, 'success');
      if (resp.debug_info?.log_line) {
        this.printLine(`[Лог] <code>${escapeHtml(resp.debug_info.log_line)}</code>`, 'info');
      }
    } else if (sub === 'bind') {
      const camId = parseInt(args[1], 10);
      const unitId = parseInt(args[2], 10);
      if (isNaN(camId) || isNaN(unitId)) {
        this.printLine('Использование: cam bind <cam_id> <unit_id>', 'error');
        return;
      }
      await api.bindCamera(camId, unitId);
      this.printLine(`Камера #${camId} привязана к установке #${unitId}`, 'success');
    } else if (sub === 'unbind') {
      const camId = parseInt(args[1], 10);
      if (isNaN(camId)) {
        this.printLine('Использование: cam unbind <cam_id>', 'error');
        return;
      }
      await api.unbindCamera(camId);
      this.printLine(`Камера #${camId} отвязана`, 'success');
    } else {
      this.printLine('Использование: cam list | cam scan | cam bind <cam_id> <unit_id> | cam unbind <cam_id>', 'error');
    }
  }

  async cmdLight(args) {
    if (args.length < 2) {
      this.printLine('Использование: light on|off <unit_id|all>', 'error');
      return;
    }
    const state = args[0].toLowerCase() === 'on';
    const target = args[1];
    const resp = await api.setLight(target, state);
    this.printLine(`Основной свет переключен в ${state ? 'ON' : 'OFF'} для target=${target}`, 'success');
    if (resp.debug_info?.log_line) {
      this.printLine(`[Лог] <code>${escapeHtml(resp.debug_info.log_line)}</code>`, 'info');
    }
  }

  async cmdFlash(args) {
    if (args.length < 2) {
      this.printLine('Использование: flash on|off <unit_id|all>', 'error');
      return;
    }
    const state = args[0].toLowerCase() === 'on';
    const target = args[1];
    const resp = await api.setFlash(target, state);
    this.printLine(`Вспышка переключена в ${state ? 'ON' : 'OFF'} для target=${target}`, 'success');
    if (resp.debug_info?.log_line) {
      this.printLine(`[Лог] <code>${escapeHtml(resp.debug_info.log_line)}</code>`, 'info');
    }
  }

  async cmdSnap(args) {
    const target = args[0] || 'all';
    this.printLine(`Выполнение ручного снимка (target=${target})...`, 'info');
    const resp = await api.snap(target);
    const count = resp.captures ? resp.captures.length : 0;
    this.printLine(`Снимок выполнен! Получено кадров: ${count}`, 'success');
    if (resp.captures) {
      resp.captures.forEach((c) => {
        this.printLine(`  Камера #${c.camera_id}: raw=${c.raw_url}, площадь=${c.area_display || '—'}`, 'normal');
      });
    }
    if (resp.debug_info?.log_line) {
      this.printLine(`[Лог] <code>${escapeHtml(resp.debug_info.log_line)}</code>`, 'info');
    }
  }

  async cmdRelayTest() {
    this.printLine('Запуск тестирования всех реле на Raspberry Pi...', 'info');
    const resp = await api.testRelays();
    this.printLine('Тест реле успешно выполнен!', 'success');
    if (resp.debug_info?.log_line) {
      this.printLine(`[Лог] <code>${escapeHtml(resp.debug_info.log_line)}</code>`, 'info');
    }
  }

  async cmdAuth(args) {
    const sub = args[0]?.toLowerCase();
    if (sub === 'invites') {
      const invites = await api.getInvites();
      if (!invites || invites.length === 0) {
        this.printLine('Нет активных инвайт-кодов.', 'normal');
        return;
      }
      let out = `Активные инвайт-коды (${invites.length}):\n`;
      invites.forEach((i) => {
        out += `  [#${i.id}] Код: ${i.code} | Роль: ${i.role} | Истекает: ${new Date(i.expires_at).toLocaleTimeString('ru-RU')}\n`;
      });
      this.printLine(`<pre>${escapeHtml(out)}</pre>`, 'normal');
    } else if (sub === 'invite' && args[1]?.toLowerCase() === 'create') {
      const resp = await api.createInvite();
      this.printLine(`Создан код приглашения: <code>${resp.code}</code> (TTL 10 мин)`, 'success');
    } else if (sub === 'sessions') {
      const sessions = await api.getSessions();
      if (!sessions || sessions.length === 0) {
        this.printLine('Сессий не найдено.', 'normal');
        return;
      }
      let out = `Сессии пользователей (${sessions.length}):\n`;
      sessions.forEach((s) => {
        out += `  [#${s.id}] ${s.client_type.toUpperCase()} | ${s.login} (${s.role}) | ${s.is_active ? 'АКТИВНА' : 'ЗАВЕРШЕНА'}\n`;
      });
      this.printLine(`<pre>${escapeHtml(out)}</pre>`, 'normal');
    } else if (sub === 'rename') {
      const sessId = parseInt(args[1], 10);
      const newName = args.slice(2).join(' ');
      if (isNaN(sessId) || !newName) {
        this.printLine('Использование: auth rename <sess_id> <name>', 'error');
        return;
      }
      await api.renameSession(sessId, newName);
      this.printLine(`Пользователь сессии #${sessId} переименован в «${newName}»`, 'success');
    } else if (sub === 'kick') {
      const sessId = parseInt(args[1], 10);
      if (isNaN(sessId)) {
        this.printLine('Использование: auth kick <sess_id>', 'error');
        return;
      }
      await api.terminateSession(sessId);
      this.printLine(`Сессия #${sessId} принудительно завершена`, 'success');
    } else {
      this.printLine('Использование: auth invites | auth invite create | auth sessions | auth rename <id> <name> | auth kick <id>', 'error');
    }
  }

  async cmdSys(args) {
    const sub = args[0]?.toLowerCase();
    if (sub === 'status') {
      const settings = await api.getSystemSettings();
      let out = `Системные настройки:\n`;
      out += `  Режим отладки (debug_mode): ${settings.debug_mode ? 'ВКЛ' : 'ВЫКЛ'}\n`;
      out += `  Автосъёмка (auto_capture): ${settings.auto_capture ? 'ВКЛ' : 'ВЫКЛ'}\n`;
      out += `  Задержка съёмки: ${settings.capture_delay_sec || '1.5'} с\n`;
      this.printLine(`<pre>${escapeHtml(out)}</pre>`, 'normal');
    } else if (sub === 'debug') {
      const enabled = args[1]?.toLowerCase() === 'on';
      await api.setDebugMode(enabled);
      this.printLine(`Режим отладки: ${enabled ? 'ВКЛ' : 'ВЫКЛ'}`, 'success');
    } else if (sub === 'auto-capture') {
      const enabled = args[1]?.toLowerCase() === 'on';
      await api.setAutoCapture(enabled);
      this.printLine(`Автосъёмка: ${enabled ? 'ВКЛ' : 'ВЫКЛ'}`, 'success');
    } else {
      this.printLine('Использование: sys status | sys debug on|off | sys auto-capture on|off', 'error');
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
