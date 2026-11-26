// clients/web/js/components/polling.js
// Единый координатор периодического опроса (PollingCoordinator) с паузой при открытых модалках и скрытой вкладке

import { hasOpenModals, onModalStateChange } from '../dialogs.js';

/**
 * Координатор периодических задач опроса.
 * Управляет таймерами, предотвращает наложение параллельных запросов,
 * ставит опрос на паузу при скрытии вкладки (document.hidden) и/или открытых модалках.
 */
export class PollingCoordinator {
  constructor(options = {}) {
    this._tasks = new Map();
    this._isModalOpen =
      typeof options.isModalOpen === 'function' ? options.isModalOpen : hasOpenModals;
    this._unsubscribeModal = null;
    this._visibilityHandler = null;
    this._bindGlobalEvents();
  }

  _bindGlobalEvents() {
    if (typeof document !== 'undefined') {
      this._visibilityHandler = () => {
        if (!document.hidden) {
          this._handleResume();
        }
      };
      document.addEventListener('visibilitychange', this._visibilityHandler);
    }

    this._unsubscribeModal = onModalStateChange((modalOpen) => {
      if (!modalOpen) {
        this._handleResume();
      }
    });
  }

  _shouldSkip(task) {
    if (!task || task.paused) return true;
    if (task.pauseWhenHidden && typeof document !== 'undefined' && document.hidden) {
      return true;
    }
    if (task.pauseWhenModalOpen && this._isModalOpen()) {
      return true;
    }
    return false;
  }

  async _executeTask(name, { force = false } = {}) {
    const task = this._tasks.get(name);
    if (!task) return;
    if (task.inFlight) return;
    if (!force && this._shouldSkip(task)) return;

    task.inFlight = true;
    task.lastRunAt = Date.now();
    try {
      await task.fn();
    } catch (_) {
      // Ошибки конкретного опроса обрабатываются внутри самой задачи
    } finally {
      task.inFlight = false;
    }
  }

  _handleResume() {
    const now = Date.now();
    this._tasks.forEach((task, name) => {
      if (!task.running || !task.runOnResume) return;
      if (this._shouldSkip(task)) return;
      if (now - task.lastRunAt >= task.intervalMs) {
        this._executeTask(name);
      }
    });
  }

  /**
   * Регистрирует задачу периодического опроса.
   */
  register(name, fn, intervalMs, options = {}) {
    if (!name || typeof fn !== 'function') return;
    this.stop(name);

    this._tasks.set(name, {
      fn,
      intervalMs: Math.max(100, Number(intervalMs) || 5000),
      pauseWhenModalOpen: options.pauseWhenModalOpen !== false,
      pauseWhenHidden: options.pauseWhenHidden !== false,
      runOnResume: Boolean(options.runOnResume),
      paused: false,
      running: false,
      inFlight: false,
      lastRunAt: 0,
      timer: null,
    });
  }

  /**
   * Запускает таймер зарегистрированной задачи.
   */
  start(name, { runImmediately = false } = {}) {
    const task = this._tasks.get(name);
    if (!task) return;

    if (task.timer) {
      clearInterval(task.timer);
      task.timer = null;
    }

    task.running = true;
    task.paused = false;
    task.timer = setInterval(() => {
      this._executeTask(name);
    }, task.intervalMs);

    if (runImmediately) {
      this._executeTask(name);
    }
  }

  /**
   * Останавливает таймер задачи.
   */
  stop(name) {
    const task = this._tasks.get(name);
    if (!task) return;
    if (task.timer) {
      clearInterval(task.timer);
      task.timer = null;
    }
    task.running = false;
  }

  /**
   * Ставит задачу (или все задачи) на ручную паузу.
   */
  pause(name = null) {
    if (name !== null) {
      const task = this._tasks.get(name);
      if (task) task.paused = true;
      return;
    }
    this._tasks.forEach((task) => {
      task.paused = true;
    });
  }

  /**
   * Снимает ручную паузу с задачи (или всех задач).
   */
  resume(name = null, { triggerIfDue = false } = {}) {
    if (name !== null) {
      const task = this._tasks.get(name);
      if (task) {
        task.paused = false;
        if (triggerIfDue && task.running) {
          this._executeTask(name);
        }
      }
      return;
    }
    this._tasks.forEach((task, key) => {
      task.paused = false;
      if (triggerIfDue && task.running) {
        this._executeTask(key);
      }
    });
  }

  /**
   * Однократный немедленный запуск задачи.
   */
  trigger(name, options = {}) {
    return this._executeTask(name, options);
  }

  startAll() {
    this._tasks.forEach((_task, name) => this.start(name));
  }

  stopAll() {
    this._tasks.forEach((_task, name) => this.stop(name));
  }

  destroy() {
    this.stopAll();
    if (this._visibilityHandler && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this._visibilityHandler);
      this._visibilityHandler = null;
    }
    if (this._unsubscribeModal) {
      this._unsubscribeModal();
      this._unsubscribeModal = null;
    }
    this._tasks.clear();
  }
}

export const pollingCoordinator = new PollingCoordinator();
