// clients/web/js/player.js
// Объединенная модалка «Галерея и таймлапс» GrowVision Web SPA.

import { api } from './api.js';
import { showToast } from './dialogs.js';

export class TimelapsePlayer {
  constructor(modalEl, options = {}) {
    this.modal = modalEl || document.getElementById('modal-gallery');
    this.userRole = options.userRole || 'guest';
    this.onBack = options.onBack || (() => {});

    this.installation = null;
    this.allFrames = [];
    this.selectedCameraId = null;

    this.currentFrameIndex = 0;
    this.currentLayer = 'raw'; // 'raw' | 'annotated'
    this.isPlaying = false;
    this.playSpeed = 1; // 1x (250ms), 2x (125ms), 4x (60ms)
    this.isLooping = false;
    this.playTimer = null;

    this.isDownloadingVideo = false;

    this.initDOM();
  }

  setRole(role) {
    this.userRole = role;
    this.updateRoleVisibility();
  }

  updateRoleVisibility() {
    const exportPanel = this.modal.querySelector('#gallery-export-panel');
    if (exportPanel) {
      if (this.userRole === 'biologist' || this.userRole === 'developer') {
        exportPanel.classList.remove('hidden');
      } else {
        exportPanel.classList.add('hidden');
      }
    }
  }

  initDOM() {
    // Кнопка закрытия модалки
    const closeBtn = this.modal.querySelector('#gallery-modal-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => this.close());
    }

    // Закрытие при клике по фону
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) {
        this.close();
      }
    });

    // Селектор камеры
    const camSelect = this.modal.querySelector('#gallery-camera-select');
    if (camSelect) {
      camSelect.addEventListener('change', (e) => {
        const val = e.target.value;
        this.selectedCameraId = val ? parseInt(val, 10) : null;
        this.loadFrames();
      });
    }

    // Переключатель слоев
    const btnRaw = this.modal.querySelector('#gallery-layer-raw');
    const btnAnn = this.modal.querySelector('#gallery-layer-ann');

    if (btnRaw && btnAnn) {
      btnRaw.addEventListener('click', () => {
        this.currentLayer = 'raw';
        btnRaw.classList.add('active');
        btnAnn.classList.remove('active');
        this.renderCurrentFrame();
      });

      btnAnn.addEventListener('click', () => {
        this.currentLayer = 'annotated';
        btnAnn.classList.add('active');
        btnRaw.classList.remove('active');
        this.renderCurrentFrame();
      });
    }

    // Ползунок таймлайна
    const timeline = this.modal.querySelector('#gallery-timeline');
    if (timeline) {
      timeline.addEventListener('input', (e) => {
        this.pause();
        this.currentFrameIndex = parseInt(e.target.value, 10);
        this.renderCurrentFrame();
      });
    }

    // Кнопки навигации плеера (4 кнопки + play/pause)
    const firstBtn = this.modal.querySelector('#gallery-btn-first');
    const prevBtn = this.modal.querySelector('#gallery-btn-prev');
    const nextBtn = this.modal.querySelector('#gallery-btn-next');
    const lastBtn = this.modal.querySelector('#gallery-btn-last');
    const playBtn = this.modal.querySelector('#gallery-btn-play');

    if (firstBtn) {
      firstBtn.addEventListener('click', () => {
        this.pause();
        this.currentFrameIndex = 0;
        this.renderCurrentFrame();
      });
    }

    if (prevBtn) {
      prevBtn.addEventListener('click', () => {
        this.pause();
        this.step(-1);
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener('click', () => {
        this.pause();
        this.step(1);
      });
    }

    if (lastBtn) {
      lastBtn.addEventListener('click', () => {
        this.pause();
        this.currentFrameIndex = Math.max(0, this.allFrames.length - 1);
        this.renderCurrentFrame();
      });
    }

    if (playBtn) {
      playBtn.addEventListener('click', () => {
        this.togglePlay();
      });
    }

    // Селектор скорости
    this.modal.querySelectorAll('.speed-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.modal.querySelectorAll('.speed-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        this.playSpeed = parseInt(btn.dataset.speed, 10) || 1;
        if (this.isPlaying) {
          this.startPlaybackTimer();
        }
      });
    });

    // Чекбокс зацикливания
    const loopToggle = this.modal.querySelector('#gallery-loop-toggle');
    if (loopToggle) {
      loopToggle.addEventListener('change', (e) => {
        this.isLooping = e.target.checked;
      });
    }

    // Связанный расчет длительности и FPS
    const durInput = this.modal.querySelector('#gallery-video-duration');
    const fpsInput = this.modal.querySelector('#gallery-video-fps');

    if (durInput && fpsInput) {
      durInput.addEventListener('input', () => {
        const total = this.allFrames.length || 1;
        const dur = parseFloat(durInput.value);
        if (!isNaN(dur) && dur > 0) {
          const calculatedFps = Math.max(1, Math.round(total / dur));
          fpsInput.value = calculatedFps;
        }
      });

      fpsInput.addEventListener('input', () => {
        const total = this.allFrames.length || 1;
        const fps = parseInt(fpsInput.value, 10);
        if (!isNaN(fps) && fps >= 1) {
          const calculatedDur = Math.max(0.5, total / fps).toFixed(1);
          durInput.value = calculatedDur;
        }
      });
    }

    // Кнопка экспорта MP4
    const btnDownload = this.modal.querySelector('#gallery-btn-download-mp4');
    if (btnDownload) {
      btnDownload.addEventListener('click', () => {
        this.handleDownloadMp4();
      });
    }
  }

  // Открытие модалки для установки
  open(installation) {
    this.installation = installation;
    this.selectedCameraId = null;
    this.currentFrameIndex = 0;
    this.currentLayer = 'raw';
    this.pause();

    // Заполнение заголовка
    const titleEl = this.modal.querySelector('#gallery-modal-title');
    const cultEl = this.modal.querySelector('#gallery-modal-culture');
    if (titleEl) titleEl.textContent = `Галерея: ${installation.name}`;
    if (cultEl) cultEl.textContent = installation.culture_name;

    // Инициализация выпадающего списка камер (если камер несколько)
    this.setupCamerasSelector();

    // Видимость панели экспорта по роли
    this.updateRoleVisibility();

    // Открытие модального окна
    this.modal.classList.remove('hidden');

    // Загрузка кадров
    this.loadFrames();
  }

  // Для обратной совместимости
  loadInstallation(installation) {
    this.open(installation);
  }

  close() {
    this.pause();
    this.modal.classList.add('hidden');
    this.onBack();
  }

  setupCamerasSelector() {
    const wrap = this.modal.querySelector('#gallery-camera-select-wrap');
    const select = this.modal.querySelector('#gallery-camera-select');
    if (!wrap || !select) return;

    select.innerHTML = '';
    const cameras = this.installation.cameras || [];

    if (cameras.length > 1) {
      wrap.classList.remove('hidden');
      const optAll = document.createElement('option');
      optAll.value = '';
      optAll.textContent = 'Все камеры';
      select.appendChild(optAll);

      cameras.forEach((c) => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = c.auto_name ? `${c.auto_name} (#${c.id})` : `Камера #${c.id}`;
        select.appendChild(opt);
      });
    } else {
      wrap.classList.add('hidden');
    }
  }

  async loadFrames() {
    const alertBox = this.modal.querySelector('#gallery-alert-box');
    const loaderWrap = this.modal.querySelector('#gallery-loader-wrap');
    const loaderText = this.modal.querySelector('#gallery-loader-text');
    const progressBar = this.modal.querySelector('#gallery-progress-bar');
    const emptyState = this.modal.querySelector('#gallery-empty-state');
    const viewportContainer = this.modal.querySelector('#gallery-viewport-container');
    const playerControls = this.modal.querySelector('#gallery-player-controls');
    const exportPanel = this.modal.querySelector('#gallery-export-panel');

    alertBox.classList.add('hidden');
    emptyState.classList.add('hidden');
    loaderWrap.classList.remove('hidden');
    progressBar.style.width = '30%';
    loaderText.textContent = 'Запрос плановых кадров с сервера...';

    try {
      const resp = await api.getTimelapseFrames(this.installation.id, this.selectedCameraId);
      const rawFrames = Array.isArray(resp) ? resp : (resp.frames || []);

      progressBar.style.width = '70%';
      loaderText.textContent = `Получено кадров: ${rawFrames.length}. Инициализация...`;

      // Фильтрация кадров по камере, если выбрана конкретная
      this.allFrames = rawFrames;

      if (this.allFrames.length === 0) {
        loaderWrap.classList.add('hidden');
        emptyState.classList.remove('hidden');
        viewportContainer.classList.add('hidden');
        playerControls.classList.add('hidden');
        exportPanel.classList.add('hidden');
        return;
      }

      // Кадры есть -> открываем плеер
      viewportContainer.classList.remove('hidden');
      playerControls.classList.remove('hidden');
      if (this.userRole === 'biologist' || this.userRole === 'developer') {
        exportPanel.classList.remove('hidden');
      }

      // Открываем самый свежий кадр
      this.currentFrameIndex = this.allFrames.length - 1;

      // Обновление таймлайна
      const timeline = this.modal.querySelector('#gallery-timeline');
      if (timeline) {
        timeline.max = this.allFrames.length - 1;
        timeline.value = this.currentFrameIndex;
      }

      // Начальный расчет полей экспорта
      this.updateExportCalculations();

      // Отрисовка кадра
      progressBar.style.width = '100%';
      setTimeout(() => {
        loaderWrap.classList.add('hidden');
        this.renderCurrentFrame();
      }, 150);

    } catch (err) {
      loaderWrap.classList.add('hidden');
      alertBox.textContent = 'Ошибка загрузки кадров: ' + err.message;
      alertBox.classList.remove('hidden');
    }
  }

  updateExportCalculations() {
    const total = this.allFrames.length || 1;
    const durInput = this.modal.querySelector('#gallery-video-duration');
    const fpsInput = this.modal.querySelector('#gallery-video-fps');

    if (durInput && fpsInput) {
      // По умолчанию длительность 5.0 секунд
      const defDur = 5.0;
      const defFps = Math.max(1, Math.round(total / defDur));
      durInput.value = defDur;
      fpsInput.value = defFps;
    }
  }

  renderCurrentFrame() {
    if (!this.allFrames || this.allFrames.length === 0) return;

    const frame = this.allFrames[this.currentFrameIndex];
    if (!frame) return;

    const imgEl = this.modal.querySelector('#gallery-image');
    const skeleton = this.modal.querySelector('#gallery-img-skeleton');
    const counterEl = this.modal.querySelector('#gallery-frame-counter');
    const timeEl = this.modal.querySelector('#gallery-meta-time');
    const areaEl = this.modal.querySelector('#gallery-meta-area');
    const timeline = this.modal.querySelector('#gallery-timeline');

    if (timeline) {
      timeline.value = this.currentFrameIndex;
    }

    if (counterEl) {
      counterEl.textContent = `Кадр ${this.currentFrameIndex + 1} из ${this.allFrames.length}`;
    }

    if (timeEl) {
      let localTime = '—';
      if (frame.captured_at) {
        const d = new Date(frame.captured_at);
        if (!isNaN(d.getTime())) {
          localTime = `${d.toLocaleString('ru-RU')} (местное время)`;
        } else {
          localTime = frame.captured_at;
        }
      }
      timeEl.innerHTML = `<svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg> <span>${escapeHtml(localTime)}</span>`;
    }

    if (areaEl) {
      const area = frame.area_display || (frame.total_area_cm2 !== null && frame.total_area_cm2 !== undefined ? `${frame.total_area_cm2.toFixed(1)} см²` : '—');
      areaEl.innerHTML = `<svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path></svg> <span>Площадь: ${escapeHtml(area)}</span>`;
    }

    const url = this.currentLayer === 'annotated'
      ? (frame.annotated_url || frame.raw_url)
      : (frame.raw_url || frame.annotated_url);

    if (imgEl && url) {
      skeleton?.classList.remove('hidden');
      imgEl.classList.add('hidden');

      const tempImg = new Image();
      tempImg.onload = () => {
        imgEl.src = url;
        skeleton?.classList.add('hidden');
        imgEl.classList.remove('hidden');
      };
      tempImg.onerror = () => {
        skeleton?.classList.add('hidden');
        imgEl.classList.remove('hidden');
      };
      tempImg.src = url;
    }
  }

  step(direction) {
    if (this.allFrames.length === 0) return;
    let nextIndex = this.currentFrameIndex + direction;

    if (nextIndex >= this.allFrames.length) {
      if (this.isLooping) {
        nextIndex = 0;
      } else {
        this.pause();
        return;
      }
    } else if (nextIndex < 0) {
      if (this.isLooping) {
        nextIndex = this.allFrames.length - 1;
      } else {
        this.pause();
        return;
      }
    }

    this.currentFrameIndex = nextIndex;
    this.renderCurrentFrame();
  }

  togglePlay() {
    if (this.isPlaying) {
      this.pause();
    } else {
      this.play();
    }
  }

  play() {
    if (this.allFrames.length <= 1) return;
    if (!this.isLooping && this.currentFrameIndex >= this.allFrames.length - 1) {
      this.currentFrameIndex = 0;
      this.renderCurrentFrame();
    }
    this.isPlaying = true;
    const playBtn = this.modal.querySelector('#gallery-btn-play');
    if (playBtn) {
      playBtn.innerHTML = `<svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg> <span>Пауза</span>`;
    }

    this.startPlaybackTimer();
  }

  pause() {
    this.isPlaying = false;
    const playBtn = this.modal.querySelector('#gallery-btn-play');
    if (playBtn) {
      playBtn.innerHTML = `<svg class="icon-inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg> <span>Воспроизвести</span>`;
    }

    if (this.playTimer) {
      clearInterval(this.playTimer);
      this.playTimer = null;
    }
  }

  startPlaybackTimer() {
    if (this.playTimer) clearInterval(this.playTimer);
    // Скорость 1x = 250ms, 2x = 125ms, 4x = 60ms
    const intervalMs = Math.round(250 / this.playSpeed);

    this.playTimer = setInterval(() => {
      this.step(1);
    }, intervalMs);
  }

  async handleDownloadMp4() {
    if (this.isDownloadingVideo) return;
    const statusEl = this.modal.querySelector('#gallery-export-status');
    const downloadBtn = this.modal.querySelector('#gallery-btn-download-mp4');
    const durInput = this.modal.querySelector('#gallery-video-duration');
    const fpsInput = this.modal.querySelector('#gallery-video-fps');

    const duration = parseFloat(durInput?.value || '5.0');
    const fps = parseInt(fpsInput?.value || '4', 10);

    if (isNaN(fps) || fps < 1) {
      showToast('Частота кадров (FPS) должна быть не менее 1', 'warning');
      return;
    }

    this.isDownloadingVideo = true;
    downloadBtn.disabled = true;
    statusEl.classList.remove('hidden');
    statusEl.textContent = `Идёт формирование MP4 (${duration} с, ${fps} FPS)...`;

    try {
      const blob = await api.buildVideo(this.installation.id, {
        cameraId: this.selectedCameraId,
        step: 1,
        durationSec: duration,
        fps: fps,
      });

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.style.display = 'none';
      a.href = url;
      a.download = `${this.installation.name}_timelapse.mp4`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);

      statusEl.textContent = 'Видео успешно сформировано и скачано!';
      showToast('MP4 видео успешно скачано', 'success');
      setTimeout(() => {
        statusEl.classList.add('hidden');
      }, 4000);
    } catch (err) {
      statusEl.textContent = `Ошибка сборки видео: ${err.message}`;
      showToast(`Ошибка экспорта видео: ${err.message}`, 'error');
    } finally {
      this.isDownloadingVideo = false;
      downloadBtn.disabled = false;
    }
  }
}
