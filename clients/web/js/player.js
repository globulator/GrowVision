// clients/web/js/player.js
// Плеер таймлапса GrowVision в полном соответствии с docs/WEB_SPEC.md.

import { api } from './api.js';

export class TimelapsePlayer {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.userRole = options.userRole || 'guest';
    this.onBack = options.onBack || (() => {});

    this.installation = null;
    this.allFrames = [];
    this.selectedCameraId = null;
    this.hasConflictingCameras = false;
    this.availableCameraIds = [];

    this.activeFrames = [];
    this.currentFrameIndex = 0;
    this.currentLayer = 'raw'; // 'raw' | 'annotated'
    this.isPlaying = false;
    this.playSpeed = 1; // 1x (200ms), 2x (100ms), 4x (50ms)
    this.isLooping = false;
    this.playTimer = null;

    this.imageCache = new Map(); // url -> HTMLImageElement
    this.failedLoadsCount = 0;
    this.isLoading = false;

    this.initDOM();
  }

  setRole(role) {
    this.userRole = role;
    this.updateRoleVisibility();
  }

  initDOM() {
    this.container.innerHTML = `
      <div class="player-wrapper">
        <div class="player-header">
          <div class="player-header-left">
            <button type="button" class="btn btn-secondary btn-sm" id="pl-btn-back">← К списку</button>
            <div class="player-title-block">
              <h2 id="pl-title" class="player-title">—</h2>
              <span id="pl-culture" class="player-subtitle">—</span>
            </div>
          </div>
          <div class="player-header-right">
            <div id="pl-camera-selector-wrap" class="hidden">
              <label for="pl-camera-select">Камера:</label>
              <select id="pl-camera-select" class="form-select form-select-sm"></select>
            </div>
            <button type="button" class="btn btn-secondary btn-sm" id="pl-btn-refresh">🔄 Обновить</button>
            <button type="button" class="btn btn-primary btn-sm hidden" id="pl-btn-video">📥 Скачать MP4</button>
          </div>
        </div>

        <div id="pl-error-box" class="alert alert-error hidden"></div>

        <div id="pl-progress-wrap" class="progress-container hidden">
          <div class="progress-bar-bg">
            <div id="pl-progress-bar" class="progress-bar-fill" style="width: 0%;"></div>
          </div>
          <div id="pl-progress-text" class="progress-text">Загрузка кадров: 0 / 0</div>
        </div>

        <div class="player-screen">
          <div id="pl-empty-state" class="player-empty-message hidden">Плановых кадров пока нет. Они появятся после первой автосъёмки.</div>
          <div id="pl-viewport" class="player-viewport">
            <img id="pl-image" src="" alt="Кадр таймлапса" class="player-img hidden" />
          </div>
          <div id="pl-layer-selector" class="layer-selector">
            <button type="button" class="layer-btn active" data-layer="raw">🖼 Оригинал</button>
            <button type="button" class="layer-btn" data-layer="annotated">🧠 Разметка нейросети</button>
          </div>
        </div>

        <div id="pl-controls-card" class="player-controls">
          <div class="timeline-row">
            <input type="range" id="pl-timeline" class="player-timeline" min="0" max="0" value="0" disabled />
            <div class="timeline-meta">
              <span id="pl-frame-counter">Кадр 0 из 0</span>
              <span id="pl-failed-counter" class="text-danger hidden">Не удалось загрузить кадров: 0</span>
            </div>
          </div>

          <div class="controls-toolbar">
            <div class="controls-left">
              <button type="button" class="btn btn-icon" id="pl-btn-step-back" title="Кадр назад" disabled>⏮</button>
              <button type="button" class="btn btn-primary btn-icon" id="pl-btn-play" title="Воспроизведение" disabled>▶</button>
              <button type="button" class="btn btn-icon" id="pl-btn-step-fwd" title="Кадр вперёд" disabled>⏭</button>
            </div>

            <div class="controls-center">
              <div class="speed-selector">
                <button type="button" class="speed-btn active" data-speed="1">1x</button>
                <button type="button" class="speed-btn" data-speed="2">2x</button>
                <button type="button" class="speed-btn" data-speed="4">4x</button>
              </div>
              <label class="checkbox-label">
                <input type="checkbox" id="pl-loop-toggle" />
                <span>🔁 Зациклить</span>
              </label>
            </div>

            <div class="controls-right">
              <span id="pl-single-frame-note" class="text-muted hidden">Пока один плановый кадр</span>
            </div>
          </div>

          <div class="metadata-card" id="pl-metadata-card">
            <div class="meta-item">
              <span class="meta-label">Время съёмки:</span>
              <span class="meta-val" id="pl-meta-time">—</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Площадь растения:</span>
              <span class="meta-val" id="pl-meta-area">—</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Камера:</span>
              <span class="meta-val" id="pl-meta-cam">—</span>
            </div>
          </div>
        </div>
      </div>
    `;

    this.bindEvents();
    this.updateRoleVisibility();
  }

  updateRoleVisibility() {
    const btnVideo = this.container.querySelector('#pl-btn-video');
    if (btnVideo) {
      if (this.userRole === 'biologist' || this.userRole === 'developer') {
        btnVideo.classList.remove('hidden');
      } else {
        btnVideo.classList.add('hidden');
      }
    }
  }

  bindEvents() {
    this.container.querySelector('#pl-btn-back').addEventListener('click', () => {
      this.pause();
      this.onBack();
    });

    this.container.querySelector('#pl-btn-refresh').addEventListener('click', () => {
      this.refreshFrames();
    });

    this.container.querySelector('#pl-btn-play').addEventListener('click', () => {
      this.togglePlay();
    });

    this.container.querySelector('#pl-btn-step-back').addEventListener('click', () => {
      this.stepFrame(-1);
    });

    this.container.querySelector('#pl-btn-step-fwd').addEventListener('click', () => {
      this.stepFrame(1);
    });

    const timeline = this.container.querySelector('#pl-timeline');
    timeline.addEventListener('input', (e) => {
      const idx = parseInt(e.target.value, 10);
      if (!isNaN(idx) && idx >= 0 && idx < this.activeFrames.length) {
        this.currentFrameIndex = idx;
        this.renderCurrentFrame();
      }
    });

    // Скорость
    this.container.querySelectorAll('.speed-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.container.querySelectorAll('.speed-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        this.playSpeed = parseFloat(btn.dataset.speed) || 1;
        if (this.isPlaying) {
          this.pause();
          this.play();
        }
      });
    });

    // Зацикливание
    this.container.querySelector('#pl-loop-toggle').addEventListener('change', (e) => {
      this.isLooping = e.target.checked;
    });

    // Слои
    this.container.querySelectorAll('.layer-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (this.isLoading) return;
        const layer = btn.dataset.layer;
        if (layer === this.currentLayer) return;

        this.container.querySelectorAll('.layer-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        this.currentLayer = layer;

        // Предзагрузка нового слоя при необходимости
        this.preloadActiveFrames();
      });
    });

    // Выбор камеры
    const camSelect = this.container.querySelector('#pl-camera-select');
    camSelect.addEventListener('change', (e) => {
      const val = e.target.value;
      this.selectedCameraId = val ? parseInt(val, 10) : null;
      this.filterFramesByCamera();
      this.preloadActiveFrames();
    });

    // Экспорт видео
    this.container.querySelector('#pl-btn-video').addEventListener('click', () => {
      if (window.showVideoExportModal) {
        window.showVideoExportModal(this.installation, this.selectedCameraId);
      }
    });
  }

  async loadInstallation(installation) {
    this.pause();
    this.installation = installation;
    this.container.querySelector('#pl-title').textContent = installation.name;
    this.container.querySelector('#pl-culture').textContent = installation.culture_name;

    await this.refreshFrames();
  }

  async refreshFrames() {
    this.hideError();
    const refreshBtn = this.container.querySelector('#pl-btn-refresh');
    refreshBtn.disabled = true;

    try {
      const resp = await api.getTimelapseFrames(this.installation.id);
      this.allFrames = resp.frames || [];

      if (this.allFrames.length === 0) {
        this.showEmptyState();
        return;
      }

      this.hideEmptyState();
      this.analyzeCameras();
      this.filterFramesByCamera();
      await this.preloadActiveFrames();
    } catch (err) {
      this.showError('Не удалось загрузить кадры: ' + err.message);
    } finally {
      refreshBtn.disabled = false;
    }
  }

  analyzeCameras() {
    // 4.1: Поиск соседних кадров с разными camera_id с разницей <= 10 минут (600 сек)
    const camIds = new Set();
    let hasConflict = false;

    for (let i = 0; i < this.allFrames.length; i++) {
      const f1 = this.allFrames[i];
      if (f1.camera_id) camIds.add(f1.camera_id);

      const t1 = new Date(f1.captured_at).getTime();
      for (let j = i + 1; j < this.allFrames.length; j++) {
        const f2 = this.allFrames[j];
        const t2 = new Date(f2.captured_at).getTime();
        const diffSec = Math.abs(t2 - t1) / 1000;
        if (diffSec > 600) break; // Кадры отсортированы по времени
        if (f1.camera_id && f2.camera_id && f1.camera_id !== f2.camera_id) {
          hasConflict = true;
          break;
        }
      }
      if (hasConflict) break;
    }

    this.hasConflictingCameras = hasConflict;
    this.availableCameraIds = Array.from(camIds).sort((a, b) => a - b);

    const camSelectorWrap = this.container.querySelector('#pl-camera-selector-wrap');
    const camSelect = this.container.querySelector('#pl-camera-select');

    if (hasConflict && this.availableCameraIds.length > 1) {
      camSelectorWrap.classList.remove('hidden');
      camSelect.innerHTML = '';
      this.availableCameraIds.forEach((cid) => {
        const opt = document.createElement('option');
        opt.value = cid;
        opt.textContent = `Камера ${cid}`;
        camSelect.appendChild(opt);
      });

      // По умолчанию камера самого свежего кадра
      const latestFrame = this.allFrames[this.allFrames.length - 1];
      const defaultCam = latestFrame?.camera_id || this.availableCameraIds[0];
      if (this.selectedCameraId === null || !this.availableCameraIds.includes(this.selectedCameraId)) {
        this.selectedCameraId = defaultCam;
      }
      camSelect.value = String(this.selectedCameraId);
    } else {
      camSelectorWrap.classList.add('hidden');
      this.selectedCameraId = null;
    }
  }

  filterFramesByCamera() {
    if (this.hasConflictingCameras && this.selectedCameraId !== null) {
      this.activeFrames = this.allFrames.filter((f) => f.camera_id === this.selectedCameraId);
    } else {
      this.activeFrames = [...this.allFrames];
    }
  }

  async preloadActiveFrames() {
    this.pause();
    this.setControlsDisabled(true);
    this.isLoading = true;

    const progressWrap = this.container.querySelector('#pl-progress-wrap');
    const progressBar = this.container.querySelector('#pl-progress-bar');
    const progressText = this.container.querySelector('#pl-progress-text');
    progressWrap.classList.remove('hidden');

    const total = this.activeFrames.length;
    let loadedCount = 0;
    this.failedLoadsCount = 0;

    const updateProgress = () => {
      const pct = total > 0 ? Math.round((loadedCount / total) * 100) : 0;
      progressBar.style.width = `${pct}%`;
      progressText.textContent = `Загружено ${loadedCount} из ${total}`;
    };
    updateProgress();

    // 4.2: Предзагрузка с конкаренси до 6
    const urls = this.activeFrames.map((f) => (this.currentLayer === 'annotated' ? f.annotated_url : f.raw_url));
    const concurrency = 6;
    let index = 0;

    const loadSingle = async (url) => {
      if (this.imageCache.has(url)) {
        loadedCount++;
        updateProgress();
        return;
      }

      const loadImage = (src) =>
        new Promise((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = () => reject(new Error('Image load failed'));
          img.src = src;
        });

      try {
        const img = await loadImage(url);
        this.imageCache.set(url, img);
      } catch (_) {
        // Однократный повтор (4.2)
        try {
          const img = await loadImage(url);
          this.imageCache.set(url, img);
        } catch (_) {
          this.failedLoadsCount++;
        }
      } finally {
        loadedCount++;
        updateProgress();
      }
    };

    const workers = Array.from({ length: Math.min(concurrency, total) }, async () => {
      while (index < total) {
        const currentIndex = index++;
        await loadSingle(urls[currentIndex]);
      }
    });

    await Promise.all(workers);

    progressWrap.classList.add('hidden');
    this.isLoading = false;

    // После предзагрузки плеер стоит на последнем (самом свежем) кадре на паузе
    this.currentFrameIndex = Math.max(0, this.activeFrames.length - 1);
    this.renderCurrentFrame();
    this.setControlsDisabled(false);

    // Индикатор неудачных кадров
    const failedEl = this.container.querySelector('#pl-failed-counter');
    if (this.failedLoadsCount > 0) {
      failedEl.textContent = `Не удалось загрузить кадров: ${this.failedLoadsCount}`;
      failedEl.classList.remove('hidden');
    } else {
      failedEl.classList.add('hidden');
    }
  }

  setControlsDisabled(disabled) {
    const isSingle = this.activeFrames.length <= 1;
    const playBtn = this.container.querySelector('#pl-btn-play');
    const stepBack = this.container.querySelector('#pl-btn-step-back');
    const stepFwd = this.container.querySelector('#pl-btn-step-fwd');
    const timeline = this.container.querySelector('#pl-timeline');
    const loopToggle = this.container.querySelector('#pl-loop-toggle');
    const camSelect = this.container.querySelector('#pl-camera-select');
    const singleNote = this.container.querySelector('#pl-single-frame-note');

    this.container.querySelectorAll('.speed-btn').forEach((b) => (b.disabled = disabled || isSingle));
    this.container.querySelectorAll('.layer-btn').forEach((b) => (b.disabled = disabled));

    playBtn.disabled = disabled || isSingle;
    stepBack.disabled = disabled || isSingle;
    stepFwd.disabled = disabled || isSingle;
    timeline.disabled = disabled || isSingle;
    loopToggle.disabled = disabled || isSingle;
    if (camSelect) camSelect.disabled = disabled;

    if (isSingle && !disabled) {
      singleNote.classList.remove('hidden');
    } else {
      singleNote.classList.add('hidden');
    }
  }

  renderCurrentFrame() {
    const total = this.activeFrames.length;
    if (total === 0) return;

    if (this.currentFrameIndex < 0) this.currentFrameIndex = 0;
    if (this.currentFrameIndex >= total) this.currentFrameIndex = total - 1;

    const frame = this.activeFrames[this.currentFrameIndex];
    const url = this.currentLayer === 'annotated' ? frame.annotated_url : frame.raw_url;

    const imgEl = this.container.querySelector('#pl-image');
    imgEl.src = url;
    imgEl.classList.remove('hidden');

    const timeline = this.container.querySelector('#pl-timeline');
    timeline.max = String(total - 1);
    timeline.value = String(this.currentFrameIndex);

    this.container.querySelector('#pl-frame-counter').textContent = `Кадр ${this.currentFrameIndex + 1} из ${total}`;

    // Метаданные (4.5): локальное время браузера, площадь, камера
    const localDate = new Date(frame.captured_at);
    const dateStr = !isNaN(localDate.getTime())
      ? `${localDate.toLocaleString('ru-RU')} (время вашего устройства)`
      : frame.captured_at;

    this.container.querySelector('#pl-meta-time').textContent = dateStr;
    this.container.querySelector('#pl-meta-area').textContent = frame.area_display || 'площадь не определена';
    this.container.querySelector('#pl-meta-cam').textContent = frame.camera_id ? `Камера ${frame.camera_id}` : 'Основная';
  }

  togglePlay() {
    if (this.isPlaying) {
      this.pause();
    } else {
      this.play();
    }
  }

  play() {
    if (this.activeFrames.length <= 1) return;
    this.isPlaying = true;
    const playBtn = this.container.querySelector('#pl-btn-play');
    playBtn.textContent = '⏸';
    playBtn.title = 'Пауза';

    // 4.3: Если стоит последний кадр и зацикливание выключено — начинаем с первого
    if (this.currentFrameIndex >= this.activeFrames.length - 1 && !this.isLooping) {
      this.currentFrameIndex = 0;
      this.renderCurrentFrame();
    }

    const intervalMs = Math.round(200 / this.playSpeed); // 1x=200ms (5fps), 2x=100ms (10fps), 4x=50ms (20fps)
    this.playTimer = setInterval(() => {
      if (this.currentFrameIndex >= this.activeFrames.length - 1) {
        if (this.isLooping) {
          this.currentFrameIndex = 0;
          this.renderCurrentFrame();
        } else {
          this.pause();
        }
      } else {
        this.currentFrameIndex++;
        this.renderCurrentFrame();
      }
    }, intervalMs);
  }

  pause() {
    this.isPlaying = false;
    if (this.playTimer) {
      clearInterval(this.playTimer);
      this.playTimer = null;
    }
    const playBtn = this.container.querySelector('#pl-btn-play');
    if (playBtn) {
      playBtn.textContent = '▶';
      playBtn.title = 'Воспроизведение';
    }
  }

  stepFrame(delta) {
    this.pause();
    const nextIdx = this.currentFrameIndex + delta;
    if (nextIdx >= 0 && nextIdx < this.activeFrames.length) {
      this.currentFrameIndex = nextIdx;
      this.renderCurrentFrame();
    }
  }

  showEmptyState() {
    this.container.querySelector('#pl-empty-state').classList.remove('hidden');
    this.container.querySelector('#pl-image').classList.add('hidden');
    this.setControlsDisabled(true);
  }

  hideEmptyState() {
    this.container.querySelector('#pl-empty-state').classList.add('hidden');
  }

  showError(msg) {
    const errBox = this.container.querySelector('#pl-error-box');
    errBox.textContent = msg;
    errBox.classList.remove('hidden');
  }

  hideError() {
    const errBox = this.container.querySelector('#pl-error-box');
    errBox.textContent = '';
    errBox.classList.add('hidden');
  }
}
