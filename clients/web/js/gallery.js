// clients/web/js/gallery.js
// Модальное окно «Галерея и таймлапс»: чистый плеер, клиентский GIF-экспорт, жесты и горячие клавиши

import { api } from './api.js';
import { showError, showToast } from './dialogs.js';
import { encodeGifFromImages } from './gif_encoder.js';
import { getPlaceholderHTML } from './placeholders.js';

let currentUnit = null;
let currentRole = 'guest';

// Загруженные метаданные кадров с сервера
let allRawFrames = [];
let currentCameraFrames = [];
let availableCameras = [];
let selectedCameraIndex = 0; // индекс в availableCameras
let currentLayer = 'raw'; // 'raw' или 'ann'
let isChartOpen = false;
let chartMode = 'cm2'; // 'cm2' | 'px'

// Кеш загруженных изображений для текущей выборки
let loadedImages = []; // Array of Image objects
let currentFrameIndex = 0;
let isPlaying = false;
let playTimer = null;
let playbackSessionId = 0;
let currentLoadId = 0;
let isEncodingGif = false;
let galleryCacheBuster = Date.now();
let renderRequestId = 0;
let galleryFrontImg = null;
let galleryBackImg = null;
const decodedFrameSrcs = new Set();
const activePreloadImages = new Set();
const PRELOAD_WINDOW_AHEAD = 6;

// Жесты: дабл-тап и свайпы
let lastTapTime = 0;
let touchStartX = 0;
let touchStartY = 0;

export function setGalleryRole(role) {
  currentRole = role;
}

export async function openGalleryModal(unit) {
  currentUnit = unit;
  currentLayer = 'raw';
  isPlaying = false;
  playbackSessionId++;
  isChartOpen = false;
  chartMode = 'cm2';
  galleryCacheBuster = Date.now();
  clearTimeout(playTimer);
  clearInterval(playTimer);
  playTimer = null;
  cancelActivePreloads();
  updateChartPanelUi();

  const modal = document.getElementById('modal-gallery');
  if (!modal) return;

  const titleEl = document.getElementById('gallery-title');
  if (titleEl) {
    titleEl.textContent = `Галерея: ${unit.name}`;
  }

  // Слой «Разметка» доступен только для разработчика
  const layerContainer = document.getElementById('gallery-layer-container');
  if (layerContainer) {
    layerContainer.classList.toggle('hidden', currentRole !== 'developer');
  }

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  // Блокируем управление на время первоначальной загрузки метаданных
  setControlsDisabled(true);
  showLoadingPlaceholder();

  try {
    const framesResp = await api.getInstallationFrames(unit.id, 500);
    const frames = Array.isArray(framesResp) ? framesResp : (framesResp?.frames || []);
    if (!frames || frames.length === 0) {
      allRawFrames = [];
      loadedImages = [];
      setControlsDisabled(true);
      const box = document.getElementById('gallery-image-box');
      if (box) {
        box.innerHTML = getPlaceholderHTML('A');
      }
      const metaBox = document.getElementById('gallery-meta');
      if (metaBox) metaBox.textContent = 'Кадры отсутствуют';
      updateFpsAndWarnings(0);
      return;
    }

    allRawFrames = frames;
    detectCameras(frames);
    selectedCameraIndex = 0;

    updateCameraSwitcherUi();
    await loadCameraFrames();
  } catch (err) {
    setControlsDisabled(true);
    const box = document.getElementById('gallery-image-box');
    if (box) {
      box.innerHTML = getPlaceholderHTML('C');
    }
    const metaBox = document.getElementById('gallery-meta');
    if (metaBox) metaBox.textContent = 'Связь с сервером временно оборвалась';
    updateFpsAndWarnings(0);
  }
}

export function closeGalleryModal() {
  const modal = document.getElementById('modal-gallery');
  if (modal) {
    modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }

  // Отменяем воспроизведение и инкрементируем loadId для отмены параллельных загрузок
  isPlaying = false;
  playbackSessionId++;
  clearTimeout(playTimer);
  clearInterval(playTimer);
  playTimer = null;
  currentLoadId++;
  renderRequestId++;
  cancelActivePreloads();
  galleryFrontImg = null;
  galleryBackImg = null;
  decodedFrameSrcs.clear();

  // Очищаем кеш изображений для освобождения памяти
  loadedImages.forEach((img) => {
    img.src = '';
  });
  loadedImages = [];
  allRawFrames = [];
  currentCameraFrames = [];
  isChartOpen = false;
  chartMode = 'cm2';
  updateChartPanelUi();
  currentUnit = null;
}

// Определение камер с порогом 10 минут
function detectCameras(frames) {
  const camIds = new Set();
  frames.forEach((f) => {
    if (f.camera_id) camIds.add(f.camera_id);
  });

  const sortedCamIds = Array.from(camIds).sort((a, b) => a - b);
  if (sortedCamIds.length <= 1) {
    availableCameras = sortedCamIds;
    return;
  }

  // Проверяем, есть ли съемка с разницей <= 10 мин
  const thresholdMs = 10 * 60 * 1000;
  let hasPairs = false;

  const sortedByTime = [...frames].sort(
    (a, b) => new Date(a.captured_at) - new Date(b.captured_at)
  );

  for (let i = 0; i < sortedByTime.length - 1; i++) {
    const f1 = sortedByTime[i];
    const f2 = sortedByTime[i + 1];
    if (f1.camera_id !== f2.camera_id) {
      const diff = Math.abs(new Date(f2.captured_at) - new Date(f1.captured_at));
      if (diff <= thresholdMs) {
        hasPairs = true;
        break;
      }
    }
  }

  availableCameras = hasPairs ? sortedCamIds : (sortedCamIds.length > 0 ? [sortedCamIds[0]] : []);
}

function updateCameraSwitcherUi() {
  const camBtn = document.getElementById('gallery-btn-camera-switch');
  const camText = document.getElementById('gallery-camera-label');
  if (!camBtn) return;

  if (availableCameras.length > 1) {
    camBtn.classList.remove('hidden');
    const curCamId = availableCameras[selectedCameraIndex];
    if (camText) camText.textContent = `Камера ${selectedCameraIndex + 1}`;
  } else {
    camBtn.classList.add('hidden');
  }
}

// Загрузка изображений для текущей камеры и слоя
async function loadCameraFrames() {
  const thisLoadId = ++currentLoadId;
  renderRequestId++;
  galleryFrontImg = null;
  galleryBackImg = null;
  decodedFrameSrcs.clear();
  pausePlayback();
  setControlsDisabled(true);
  showLoadingPlaceholder();

  const targetCamId = availableCameras[selectedCameraIndex];
  // Фильтруем кадры для выбранной камеры и сортируем хронологически от старых к новым
  let filtered = allRawFrames.filter((f) => {
    if (!targetCamId) return true;
    return f.camera_id === targetCamId;
  });

  filtered.sort((a, b) => new Date(a.captured_at) - new Date(b.captured_at));

  if (filtered.length === 0) {
    filtered = [...allRawFrames].sort(
      (a, b) => new Date(a.captured_at) - new Date(b.captured_at)
    );
  }
  currentCameraFrames = filtered;
  if (isChartOpen) {
    chartMode = resolveDefaultGrowthChartMode(currentCameraFrames);
    renderGrowthChart();
  }

  // Предзагрузка всех кадров в Image объекты (до 6 параллельно, 1 повтор при ошибке)
  const images = new Array(filtered.length);
  const concurrency = 6;
  let cursor = 0;

  async function loadOne(idx) {
    if (thisLoadId !== currentLoadId) return; // отмена
    const frame = filtered[idx];
    const rawUrl = frame.raw_url || frame.raw_image_url || '';
    const annUrl = frame.annotated_url || frame.annotated_image_url || '';
    const url = currentLayer === 'ann' && annUrl ? annUrl : (rawUrl || annUrl);

    return new Promise((resolve) => {
      let attempts = 0;
      function tryLoad() {
        if (thisLoadId !== currentLoadId) {
          resolve(null);
          return;
        }
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          img.dataset.capturedAt = frame.captured_at || '';
          img.dataset.areaDisplay = frame.area_display || '—';
          const finalize = () => {
            decodedFrameSrcs.add(img.src);
            images[idx] = img;
            resolve(img);
          };
          if (typeof img.decode === 'function') {
            img.decode().then(finalize, finalize);
          } else {
            finalize();
          }
        };
        img.onerror = () => {
          attempts++;
          if (attempts < 2) {
            setTimeout(tryLoad, 300);
          } else {
            // Если повтор не удался, помечаем как битый
            img.dataset.broken = 'true';
            img.dataset.capturedAt = frame.captured_at || '';
            img.dataset.areaDisplay = frame.area_display || '—';
            images[idx] = img;
            resolve(img);
          }
        };
        const sep = url.includes('?') ? '&' : '?';
        img.src = url ? `${url}${sep}t=${galleryCacheBuster}` : '';
      }
      tryLoad();
    });
  }

  async function worker() {
    while (cursor < filtered.length) {
      if (thisLoadId !== currentLoadId) break;
      const idx = cursor++;
      await loadOne(idx);
    }
  }

  const workers = [];
  for (let w = 0; w < Math.min(concurrency, filtered.length); w++) {
    workers.push(worker());
  }
  await Promise.all(workers);

  if (thisLoadId !== currentLoadId) return;

  loadedImages = images.filter((img) => img !== null);

  // Инициализация длительности и FPS (~15 fps)
  const totalCount = loadedImages.length;
  const durInput = document.getElementById('gallery-duration-input');
  if (durInput) {
    durInput.min = '1';
    durInput.step = '1';
    const recommendedSec = Math.max(1, Math.round(totalCount / 15));
    durInput.value = String(recommendedSec);
  }
  updateFpsAndWarnings(totalCount);

  // Открываем последний кадр на паузе и заранее прогреваем начало таймлапса
  currentFrameIndex = Math.max(0, loadedImages.length - 1);
  setControlsDisabled(false);
  preloadFrameWindow(0, PRELOAD_WINDOW_AHEAD);
  await renderCurrentFrame();
  updatePlaybackUi();
}

function showLoadingPlaceholder() {
  const box = document.getElementById('gallery-image-box');
  if (box) {
    box.innerHTML = `
      <div class="gallery-skeleton-placeholder plant-lifecycle-spinner">
        <svg viewBox="0 0 64 64" width="56" height="56" class="plant-cycle-svg" aria-hidden="true">
          <path class="plant-ground" d="M12 52 H52" stroke="#334155" stroke-width="2" stroke-linecap="round"/>
          <ellipse class="plant-seed" cx="32" cy="50" rx="4" ry="3" fill="#10b981"/>
          <path class="plant-stem" d="M32 50 Q32 36 32 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round"/>
          <path class="plant-sprout-left" d="M32 38 Q22 36 20 28 Q28 28 32 36" fill="#10b981"/>
          <path class="plant-leaf-right" d="M32 30 Q44 26 44 16 Q34 18 32 26" fill="#059669"/>
          <path class="plant-top-leaf" d="M32 24 Q32 10 32 8 Q36 14 32 24" fill="#34d399"/>
        </svg>
        <span class="placeholder-text">Растение растёт, кадры загружаются...</span>
      </div>
    `;
  }
}

function updateFpsAndWarnings(framesCount) {
  const durInput = document.getElementById('gallery-duration-input');
  const fpsValEl = document.getElementById('gallery-fps-value');
  const fpsWarnEl = document.getElementById('gallery-fps-warning');
  const durErrEl = document.getElementById('gallery-duration-error');
  const downloadBtn = document.getElementById('gallery-btn-download');

  if (!durInput || !fpsValEl) return false;

  const rawVal = durInput.value.trim();
  const val = Number(rawVal);

  if (!rawVal || isNaN(val) || val < 1 || !Number.isInteger(val)) {
    if (durErrEl) {
      durErrEl.textContent = 'Минимум 1 с';
      durErrEl.classList.remove('hidden');
    }
    if (fpsWarnEl) fpsWarnEl.classList.add('hidden');
    fpsValEl.textContent = '— fps';
    if (downloadBtn) downloadBtn.disabled = true;
    return false;
  }

  if (durErrEl) {
    durErrEl.textContent = '';
    durErrEl.classList.add('hidden');
  }
  if (downloadBtn) downloadBtn.disabled = false;

  if (framesCount <= 0) {
    fpsValEl.textContent = '— fps';
    if (fpsWarnEl) fpsWarnEl.classList.add('hidden');
    return true;
  }

  const fps = framesCount / val;
  fpsValEl.textContent = `${fps.toFixed(1)} fps`;

  if (fps > 30) {
    if (fpsWarnEl) {
      fpsWarnEl.textContent = 'Макс. 30 FPS';
      fpsWarnEl.classList.remove('hidden');
    }
  } else if (val > framesCount) {
    if (fpsWarnEl) {
      fpsWarnEl.textContent = 'Каждый кадр удерживается дольше 1 с';
      fpsWarnEl.classList.remove('hidden');
    }
  } else {
    if (fpsWarnEl) fpsWarnEl.classList.add('hidden');
  }

  return true;
}

function waitForImageReady(imgEl, srcUrl) {
  return new Promise((resolve) => {
    if (!srcUrl) {
      resolve(false);
      return;
    }
    const finish = () => {
      if (typeof imgEl.decode === 'function') {
        imgEl.decode().then(() => resolve(true), () => resolve(true));
      } else {
        resolve(true);
      }
    };
    if (imgEl.src === srcUrl && imgEl.complete) {
      if (imgEl.naturalWidth > 0) {
        finish();
      } else {
        resolve(false);
      }
      return;
    }
    imgEl.onload = () => finish();
    imgEl.onerror = () => resolve(false);
    imgEl.src = srcUrl;
  });
}

function cancelActivePreloads() {
  activePreloadImages.forEach((img) => {
    img.onload = null;
    img.onerror = null;
    img.src = '';
  });
  activePreloadImages.clear();
}

function preloadFrameWindow(centerIndex, lookAhead = PRELOAD_WINDOW_AHEAD) {
  if (!loadedImages.length) return;
  const sessionToken = playbackSessionId;
  const indices = [];
  for (let step = 1; step <= lookAhead; step++) {
    const nextIdx = centerIndex + step;
    if (nextIdx < loadedImages.length) indices.push(nextIdx);
  }
  if (centerIndex - 1 >= 0) {
    indices.push(centerIndex - 1);
  }
  indices.forEach((idx) => {
    if (sessionToken !== playbackSessionId) return;
    const target = loadedImages[idx];
    if (!target || target.dataset.broken === 'true' || !target.src) return;
    if (decodedFrameSrcs.has(target.src)) return;
    const hiddenPreload = new Image();
    hiddenPreload.crossOrigin = 'anonymous';
    activePreloadImages.add(hiddenPreload);
    waitForImageReady(hiddenPreload, target.src).then((ok) => {
      activePreloadImages.delete(hiddenPreload);
      if (sessionToken !== playbackSessionId) return;
      if (ok) decodedFrameSrcs.add(target.src);
    });
  });
}

function ensureDoubleBufferImgs(box) {
  if (
    galleryFrontImg &&
    galleryBackImg &&
    galleryFrontImg.parentElement === box &&
    galleryBackImg.parentElement === box
  ) {
    return { front: galleryFrontImg, back: galleryBackImg };
  }
  box.innerHTML = '';
  const front = document.createElement('img');
  front.className = 'gallery-main-img';
  front.alt = '';

  const back = document.createElement('img');
  back.className = 'gallery-main-img gallery-buffer-hidden';
  back.alt = '';

  box.appendChild(front);
  box.appendChild(back);
  galleryFrontImg = front;
  galleryBackImg = back;
  return { front, back };
}

async function renderCurrentFrame() {
  const box = document.getElementById('gallery-image-box');
  const metaBox = document.getElementById('gallery-meta');
  const slider = document.getElementById('gallery-slider');
  if (!box || loadedImages.length === 0) return;

  const frameIdx = currentFrameIndex;
  const img = loadedImages[frameIdx];
  if (!img) return;

  // Обновление ползунка
  if (slider) {
    slider.min = '0';
    slider.max = String(loadedImages.length - 1);
    slider.value = String(frameIdx);
  }

  // Метаданные: Кадр X из N | Время (локальное браузера) | Площадь
  if (metaBox) {
    const total = loadedImages.length;
    let timeStr = '—';
    if (img.dataset.capturedAt) {
      const dt = new Date(img.dataset.capturedAt);
      timeStr = `${dt.toLocaleString('ru-RU')} (местное время)`;
    }
    const areaStr = img.dataset.areaDisplay || '—';
    metaBox.textContent = `Кадр ${frameIdx + 1} из ${total} | ${timeStr} | Площадь: ${areaStr}`;
  }

  updatePlaybackUi();
  preloadFrameWindow(frameIdx, PRELOAD_WINDOW_AHEAD);

  const reqId = ++renderRequestId;
  if (img.dataset.broken === 'true') {
    galleryFrontImg = null;
    galleryBackImg = null;
    box.innerHTML = getPlaceholderHTML('D');
    return;
  }

  const { front, back } = ensureDoubleBufferImgs(box);
  back.alt = `Кадр ${frameIdx + 1}`;
  const ok = await waitForImageReady(back, img.src);
  if (reqId !== renderRequestId || !currentUnit) return;

  if (!ok) {
    galleryFrontImg = null;
    galleryBackImg = null;
    box.innerHTML = getPlaceholderHTML('D');
    return;
  }

  decodedFrameSrcs.add(img.src);
  back.classList.remove('gallery-buffer-hidden');
  front.classList.add('gallery-buffer-hidden');
  galleryFrontImg = back;
  galleryBackImg = front;
}

// Блокировка/разблокировка кнопок управления
function setControlsDisabled(disabled) {
  const btnFirst = document.getElementById('gallery-btn-first');
  const btnPrev = document.getElementById('gallery-btn-prev');
  const btnPlay = document.getElementById('gallery-btn-play');
  const btnNext = document.getElementById('gallery-btn-next');
  const btnLast = document.getElementById('gallery-btn-last');
  const slider = document.getElementById('gallery-slider');
  const durInput = document.getElementById('gallery-duration-input');
  const downloadBtn = document.getElementById('gallery-btn-download');
  const chartBtn = document.getElementById('gallery-btn-chart');

  const btns = [btnFirst, btnPrev, btnPlay, btnNext, btnLast, slider, durInput, downloadBtn, chartBtn];
  btns.forEach((b) => {
    if (b) b.disabled = disabled;
  });
}

// Обновление доступности 5 кнопок по правилам первого/последнего кадра
function updatePlaybackUi() {
  const total = loadedImages.length;
  const btnFirst = document.getElementById('gallery-btn-first');
  const btnPrev = document.getElementById('gallery-btn-prev');
  const btnPlay = document.getElementById('gallery-btn-play');
  const btnNext = document.getElementById('gallery-btn-next');
  const btnLast = document.getElementById('gallery-btn-last');
  const slider = document.getElementById('gallery-slider');
  const durInput = document.getElementById('gallery-duration-input');

  if (total <= 1) {
    if (btnFirst) btnFirst.disabled = true;
    if (btnPrev) btnPrev.disabled = true;
    if (btnPlay) btnPlay.disabled = true;
    if (btnNext) btnNext.disabled = true;
    if (btnLast) btnLast.disabled = true;
    if (slider) slider.disabled = true;
    if (durInput) durInput.disabled = true;
    return;
  }

  // Если выбран 1-й кадр: «В начало» и «Предыдущий» disabled
  if (btnFirst) btnFirst.disabled = currentFrameIndex === 0;
  if (btnPrev) btnPrev.disabled = currentFrameIndex === 0;

  // Если выбран последний кадр: «Следующий» и «В конец» disabled
  if (btnNext) btnNext.disabled = currentFrameIndex === total - 1;
  if (btnLast) btnLast.disabled = currentFrameIndex === total - 1;

  // Иконка Play / Pause (обновляем DOM только при смене состояния, чтобы не срывать клик по кнопке)
  if (btnPlay) {
    btnPlay.disabled = false;
    const nextState = isPlaying ? 'playing' : 'paused';
    if (btnPlay.dataset.playState !== nextState) {
      btnPlay.dataset.playState = nextState;
      btnPlay.innerHTML = isPlaying
        ? `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`
        : `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
    }
    const label = isPlaying ? 'Пауза' : 'Воспроизвести';
    btnPlay.title = label;
    btnPlay.setAttribute('aria-label', label);
  }
}

function scheduleNextPlaybackTick(sessionId, frameIntervalMs) {
  if (!isPlaying || sessionId !== playbackSessionId) return;
  playTimer = setTimeout(async () => {
    playTimer = null;
    if (!isPlaying || sessionId !== playbackSessionId) return;
    if (currentFrameIndex < loadedImages.length - 1) {
      const tickStarted = Date.now();
      currentFrameIndex++;
      await renderCurrentFrame();
      if (!isPlaying || sessionId !== playbackSessionId) return;
      const elapsed = Date.now() - tickStarted;
      const nextDelay = Math.max(0, frameIntervalMs - elapsed);
      scheduleNextPlaybackTick(sessionId, nextDelay);
    } else {
      // Завершение одного прохода до последнего кадра
      pausePlayback();
    }
  }, frameIntervalMs);
}

function startPlayback() {
  if (loadedImages.length <= 1) return;

  // Если нажали Play на последнем кадре — начинаем с 1-го кадра
  if (currentFrameIndex >= loadedImages.length - 1) {
    currentFrameIndex = 0;
  }

  clearTimeout(playTimer);
  clearInterval(playTimer);
  playTimer = null;
  const sessionId = ++playbackSessionId;
  isPlaying = true;
  updatePlaybackUi();
  preloadFrameWindow(currentFrameIndex, PRELOAD_WINDOW_AHEAD);

  const durInput = document.getElementById('gallery-duration-input');
  let durationSec = parseFloat(durInput ? durInput.value : '3') || 3;
  durationSec = Math.max(1, Math.min(loadedImages.length, durationSec));

  const frameIntervalMs = Math.max(30, Math.round((durationSec * 1000) / loadedImages.length));
  scheduleNextPlaybackTick(sessionId, frameIntervalMs);
}

function pausePlayback() {
  isPlaying = false;
  playbackSessionId++;
  clearTimeout(playTimer);
  clearInterval(playTimer);
  playTimer = null;
  renderRequestId++;
  cancelActivePreloads();
  updatePlaybackUi();
}

function togglePlayback() {
  if (isPlaying) {
    pausePlayback();
  } else {
    startPlayback();
  }
}

// Навигация
function goToFirstFrame() {
  pausePlayback();
  currentFrameIndex = 0;
  renderCurrentFrame();
}

function goToPrevFrame() {
  pausePlayback();
  if (currentFrameIndex > 0) {
    currentFrameIndex--;
    renderCurrentFrame();
  }
}

function goToNextFrame() {
  pausePlayback();
  if (currentFrameIndex < loadedImages.length - 1) {
    currentFrameIndex++;
    renderCurrentFrame();
  }
}

function goToLastFrame() {
  pausePlayback();
  currentFrameIndex = loadedImages.length - 1;
  renderCurrentFrame();
}

// Экспорт GIF (клиентский) или PNG (при 1 кадре)
async function handleDownload() {
  if (loadedImages.length === 0 || !currentUnit) return;
  if (!updateFpsAndWarnings(loadedImages.length)) return;

  const btn = document.getElementById('gallery-btn-download');
  const now = new Date();
  const datePart = now.toISOString().slice(0, 10);
  const timePart = `${String(now.getHours()).padStart(2, '0')}-${String(now.getMinutes()).padStart(2, '0')}`;
  const safeName = (currentUnit.name || 'unit').replace(/[^a-zA-Z0-9а-яА-Я_-]/g, '_');

  // Если 1 кадр -> скачиваем PNG
  if (loadedImages.length === 1) {
    try {
      const img = loadedImages[0];
      const res = await fetch(img.src);
      const blob = await res.blob();
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `growvision_${safeName}_${datePart}_${timePart}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      showToast('Кадр успешно сохранён как PNG');
    } catch (err) {
      showError('Ошибка скачивания PNG: ' + err.message, 'Скачивание');
    }
    return;
  }

  // N >= 2 кадров -> формируем GIF в браузере на клиенте
  if (isEncodingGif) return;
  isEncodingGif = true;

  if (btn) {
    btn.disabled = true;
    btn.classList.add('rotating');
  }
  showToast('Генерация GIF в браузере...');

  try {
    const durInput = document.getElementById('gallery-duration-input');
    let durSec = parseFloat(durInput ? durInput.value : '3') || 3;
    durSec = Math.max(1, Math.min(loadedImages.length, durSec));

    const gifBlob = await encodeGifFromImages(loadedImages, durSec, 720);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(gifBlob);
    link.download = `growvision_${safeName}_${datePart}_${timePart}.gif`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    showToast('GIF-таймлапс успешно создан и скачан');
  } catch (err) {
    showError('Ошибка генерации GIF: ' + err.message, 'Экспорт GIF');
  } finally {
    isEncodingGif = false;
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('rotating');
    }
  }
}

// Мобильные свайпы и дабл-тапы
function setupGestures() {
  const box = document.getElementById('gallery-image-box');
  if (!box) return;

  box.addEventListener(
    'touchstart',
    (e) => {
      if (e.touches.length === 1) {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
      }
    },
    { passive: true }
  );

  box.addEventListener(
    'touchend',
    (e) => {
      if (e.changedTouches.length !== 1) return;
      const endX = e.changedTouches[0].clientX;
      const endY = e.changedTouches[0].clientY;
      const diffX = endX - touchStartX;
      const diffY = endY - touchStartY;

      const now = Date.now();
      const isDoubleTap = now - lastTapTime < 300 && Math.abs(diffX) < 15 && Math.abs(diffY) < 15;
      lastTapTime = now;

      if (isDoubleTap) {
        // Двойной тап по левой или правой половине
        const rect = box.getBoundingClientRect();
        const tapX = endX - rect.left;
        if (tapX < rect.width / 2) {
          goToFirstFrame();
        } else {
          goToLastFrame();
        }
        return;
      }

      // Горизонтальный свайп с порогом > 40 px
      if (Math.abs(diffX) > 40 && Math.abs(diffX) > Math.abs(diffY) * 1.5) {
        if (diffX < 0) {
          goToNextFrame();
        } else {
          goToPrevFrame();
        }
      }
    },
    { passive: true }
  );
}

// Выбор режима графика по умолчанию: 'cm2', если есть хотя бы 1 калиброванный кадр, иначе 'px'
export function resolveDefaultGrowthChartMode(frames) {
  const list = Array.isArray(frames) ? frames : [];
  const hasAnyCalibrated = list.some(
    (f) => f && f.total_area_cm2 !== null && f.total_area_cm2 !== undefined
  );
  return hasAnyCalibrated ? 'cm2' : 'px';
}

// Подготовка единого ряда данных графика без скачков единиц измерения (см² vs px)
export function prepareGrowthChartSeries(frames, mode = null) {
  const list = Array.isArray(frames) ? frames : [];
  if (list.length === 0) {
    return { unit: 'px', unitLabel: 'px', points: [] };
  }

  const resolvedMode =
    mode === 'cm2' || mode === 'px' ? mode : resolveDefaultGrowthChartMode(list);
  let targetUnit = resolvedMode;
  let sourceFrames = list;

  if (resolvedMode === 'cm2') {
    targetUnit = 'cm2';
    sourceFrames = list.filter(
      (f) => f && f.total_area_cm2 !== null && f.total_area_cm2 !== undefined
    );
  } else {
    targetUnit = 'px';
    sourceFrames = list;
  }

  const points = sourceFrames.map((f) => {
    const origIdx = list.indexOf(f);
    const totalVal =
      targetUnit === 'cm2'
        ? Number(f.total_area_cm2 || 0)
        : Number(f.total_area_px || 0);
    const avgVal =
      targetUnit === 'cm2'
        ? Number(f.avg_area_cm2 || 0)
        : Number(f.avg_area_px || 0);
    return {
      frameIndex: origIdx >= 0 ? origIdx : 0,
      capturedAt: f.captured_at || '',
      plantsCount: Number(f.plants_count || 0),
      total: totalVal,
      avg: avgVal,
    };
  });

  return {
    unit: targetUnit,
    unitLabel: targetUnit === 'cm2' ? 'см²' : 'px',
    points,
  };
}

function escapeCsvCell(value) {
  if (value === null || value === undefined) return '';
  const str = String(value);
  if (/[",\r\n]/.test(str)) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

function fmtCsvNum(value) {
  if (value === null || value === undefined || value === '') return '';
  return String(value);
}

// Генерация CSV-отчёта по замерам графика роста (с UTF-8 BOM для Excel/LibreOffice)
export function buildGrowthChartCsv(frames, _unitName = '') {
  const list = Array.isArray(frames) ? frames : [];
  const headers = [
    'Дата и время',
    'Количество кустов',
    'Общая площадь (см²)',
    'Средняя площадь (см²)',
    'Общая площадь (px)',
    'Средняя площадь (px)',
    'Ширина (мм)',
    'Высота (мм)',
    'Ширина (px)',
    'Высота (px)',
    'Калибровка ArUco',
  ];

  const rows = [headers.map(escapeCsvCell).join(',')];
  for (const f of list) {
    if (!f) continue;
    const isCalibrated = Boolean(f.aruco_calibrated ?? f.has_aruco);
    const row = [
      f.captured_at || '',
      f.plants_count ?? 0,
      fmtCsvNum(f.total_area_cm2),
      fmtCsvNum(f.avg_area_cm2),
      fmtCsvNum(f.total_area_px),
      fmtCsvNum(f.avg_area_px),
      fmtCsvNum(f.width_mm),
      fmtCsvNum(f.height_mm),
      fmtCsvNum(f.width_px),
      fmtCsvNum(f.height_px),
      isCalibrated ? 'Да' : 'Нет',
    ];
    rows.push(row.map(escapeCsvCell).join(','));
  }

  return `\uFEFF${rows.join('\r\n')}\r\n`;
}

function handleExportGrowthCsv() {
  if (!currentCameraFrames || currentCameraFrames.length === 0) {
    showToast('Нет данных для экспорта', 'info');
    return;
  }
  const unitName = (currentUnit && currentUnit.name) || 'installation';
  const csvContent = buildGrowthChartCsv(currentCameraFrames, unitName);
  const safeName =
    String(unitName)
      .trim()
      .replace(/[^\p{L}\p{N}_-]+/gu, '_')
      .replace(/^_+|_+$/g, '') || 'installation';
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `growvision_${safeName}_growth.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function updateChartPanelUi() {
  const panel = document.getElementById('gallery-chart-panel');
  const btn = document.getElementById('gallery-btn-chart');
  const exportBtn = document.getElementById('gallery-chart-export-csv');
  if (panel) {
    panel.classList.toggle('hidden', !isChartOpen);
  }
  if (btn) {
    btn.classList.toggle('active', isChartOpen);
  }
  if (exportBtn) {
    exportBtn.disabled = !currentCameraFrames || currentCameraFrames.length === 0;
  }
  const modeBtns = document.querySelectorAll('#gallery-chart-mode-control .seg-btn');
  modeBtns.forEach((b) => {
    b.classList.toggle('active', b.dataset.chartMode === chartMode);
  });
}

function formatChartShortTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function renderGrowthChart() {
  updateChartPanelUi();
  const svg = document.getElementById('gallery-chart-svg');
  const emptyEl = document.getElementById('gallery-chart-empty');
  const unitBadge = document.getElementById('gallery-chart-unit-badge');
  const tooltip = document.getElementById('gallery-chart-tooltip');
  if (!svg) return;

  if (tooltip) tooltip.classList.add('hidden');

  const { unit, unitLabel, points } = prepareGrowthChartSeries(
    currentCameraFrames,
    chartMode
  );
  if (unitBadge) {
    unitBadge.textContent = `Единицы: ${unitLabel}`;
  }

  if (!points || points.length === 0) {
    svg.innerHTML = '';
    svg.classList.add('hidden');
    if (emptyEl) {
      emptyEl.textContent =
        chartMode === 'cm2'
          ? 'Нет кадров с ArUco-калибровкой'
          : 'Нет данных для построения графика';
      emptyEl.classList.remove('hidden');
    }
    return;
  }

  svg.classList.remove('hidden');
  if (emptyEl) emptyEl.classList.add('hidden');

  const W = 760;
  const H = 230;
  const padL = 62;
  const padR = 20;
  const padT = 16;
  const padB = 34;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const maxRaw = Math.max(
    1,
    ...points.map((p) => Math.max(p.total, p.avg))
  );
  const yMax = maxRaw * 1.12;

  const getX = (i) =>
    points.length === 1
      ? padL + plotW / 2
      : padL + (i / (points.length - 1)) * plotW;
  const getY = (val) => padT + plotH - (val / yMax) * plotH;

  const gridSteps = 4;
  const svgParts = [];

  // Сетка и подписи оси Y
  for (let s = 0; s <= gridSteps; s++) {
    const ratio = s / gridSteps;
    const val = yMax * (1 - ratio);
    const y = padT + ratio * plotH;
    const fmtVal = unit === 'cm2' ? val.toFixed(1) : String(Math.round(val));
    svgParts.push(
      `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="rgba(148,163,184,0.22)" stroke-width="1"/>`
    );
    svgParts.push(
      `<text x="${padL - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="#94a3b8" font-size="11" font-family="monospace">${fmtVal}</text>`
    );
  }

  // Подписи оси X (первый, средний, последний)
  const labelIndices =
    points.length === 1
      ? [0]
      : points.length === 2
        ? [0, 1]
        : [0, Math.floor((points.length - 1) / 2), points.length - 1];
  labelIndices.forEach((idx, pos) => {
    const x = getX(idx);
    const anchor =
      points.length === 1
        ? 'middle'
        : pos === 0
          ? 'start'
          : pos === labelIndices.length - 1
            ? 'end'
            : 'middle';
    svgParts.push(
      `<text x="${x.toFixed(1)}" y="${H - 8}" text-anchor="${anchor}" fill="#94a3b8" font-size="11">${formatChartShortTime(points[idx].capturedAt)}</text>`
    );
  });

  // Линии: Общая площадь (#10b981) и Средняя площадь куста (#38bdf8)
  const totalCoords = points.map((p, i) => `${getX(i).toFixed(1)},${getY(p.total).toFixed(1)}`);
  const avgCoords = points.map((p, i) => `${getX(i).toFixed(1)},${getY(p.avg).toFixed(1)}`);

  if (points.length >= 2) {
    svgParts.push(
      `<polyline fill="none" stroke="#10b981" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" points="${totalCoords.join(' ')}"/>`
    );
    svgParts.push(
      `<polyline fill="none" stroke="#38bdf8" stroke-width="2.2" stroke-dasharray="5 3" stroke-linejoin="round" stroke-linecap="round" points="${avgCoords.join(' ')}"/>`
    );
  }

  // Точки для тултипов и перехода к кадру
  points.forEach((p, i) => {
    const cx = getX(i).toFixed(1);
    const cyTot = getY(p.total).toFixed(1);
    const cyAvg = getY(p.avg).toFixed(1);
    svgParts.push(
      `<circle class="chart-point chart-point-total" data-idx="${i}" cx="${cx}" cy="${cyTot}" r="4.5" fill="#10b981" stroke="#0f172a" stroke-width="1.5"/>`
    );
    svgParts.push(
      `<circle class="chart-point chart-point-avg" data-idx="${i}" cx="${cx}" cy="${cyAvg}" r="4" fill="#38bdf8" stroke="#0f172a" stroke-width="1.5"/>`
    );
  });

  svg.innerHTML = svgParts.join('');

  svg.querySelectorAll('.chart-point').forEach((circle) => {
    circle.addEventListener('mouseenter', () => {
      const idx = parseInt(circle.dataset.idx, 10);
      const p = points[idx];
      if (!p || !tooltip) return;
      const fmtTotal = unit === 'cm2' ? p.total.toFixed(2) : String(Math.round(p.total));
      const fmtAvg = unit === 'cm2' ? p.avg.toFixed(2) : String(Math.round(p.avg));
      tooltip.innerHTML = `
        <div class="tooltip-time">${formatChartShortTime(p.capturedAt)}</div>
        <div>Кустов: <strong>${p.plantsCount}</strong></div>
        <div class="tooltip-total">Общая: <strong>${fmtTotal} ${unitLabel}</strong></div>
        <div class="tooltip-avg">Средняя: <strong>${fmtAvg} ${unitLabel}</strong></div>
      `;
      const cxNum = parseFloat(circle.getAttribute('cx') || '0');
      const cyNum = parseFloat(circle.getAttribute('cy') || '0');
      tooltip.style.left = `${Math.min(82, Math.max(8, (cxNum / W) * 100))}%`;
      tooltip.style.top = `${Math.max(6, (cyNum / H) * 100 - 16)}%`;
      tooltip.classList.remove('hidden');
    });
    circle.addEventListener('mouseleave', () => {
      if (tooltip) tooltip.classList.add('hidden');
    });
    circle.addEventListener('click', () => {
      const idx = parseInt(circle.dataset.idx, 10);
      const p = points[idx];
      if (!p) return;
      if (p.frameIndex >= 0 && p.frameIndex < loadedImages.length) {
        pausePlayback();
        currentFrameIndex = p.frameIndex;
        renderCurrentFrame();
      }
    });
  });
}

// Инициализация событий галереи
export function initGallery() {
  const modal = document.getElementById('modal-gallery');
  if (!modal) return;

  const closeBtn = document.getElementById('modal-gallery-close');
  if (closeBtn) closeBtn.addEventListener('click', closeGalleryModal);

  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeGalleryModal();
  });

  // 5 кнопок навигации
  const btnFirst = document.getElementById('gallery-btn-first');
  const btnPrev = document.getElementById('gallery-btn-prev');
  const btnPlay = document.getElementById('gallery-btn-play');
  const btnNext = document.getElementById('gallery-btn-next');
  const btnLast = document.getElementById('gallery-btn-last');

  if (btnFirst) btnFirst.addEventListener('click', goToFirstFrame);
  if (btnPrev) btnPrev.addEventListener('click', goToPrevFrame);
  if (btnPlay) btnPlay.addEventListener('click', togglePlayback);
  if (btnNext) btnNext.addEventListener('click', goToNextFrame);
  if (btnLast) btnLast.addEventListener('click', goToLastFrame);

  // Ползунок
  const slider = document.getElementById('gallery-slider');
  if (slider) {
    slider.addEventListener('input', () => {
      pausePlayback();
      currentFrameIndex = parseInt(slider.value, 10);
      renderCurrentFrame();
    });
  }

  // Кнопка переключения камер (циклическая)
  const camBtn = document.getElementById('gallery-btn-camera-switch');
  if (camBtn) {
    camBtn.addEventListener('click', () => {
      if (availableCameras.length > 1) {
        selectedCameraIndex = (selectedCameraIndex + 1) % availableCameras.length;
        updateCameraSwitcherUi();
        loadCameraFrames();
      }
    });
  }

  // Переключатель слоя (только для разработчика)
  const layerRaw = document.getElementById('gallery-layer-raw');
  const layerAnn = document.getElementById('gallery-layer-ann');
  if (layerRaw && layerAnn) {
    layerRaw.addEventListener('click', () => {
      if (currentLayer !== 'raw') {
        currentLayer = 'raw';
        layerRaw.classList.add('active');
        layerAnn.classList.remove('active');
        loadCameraFrames();
      }
    });
    layerAnn.addEventListener('click', () => {
      if (currentLayer !== 'ann') {
        currentLayer = 'ann';
        layerAnn.classList.add('active');
        layerRaw.classList.remove('active');
        loadCameraFrames();
      }
    });
  }

  // Кнопка скачать
  const downloadBtn = document.getElementById('gallery-btn-download');
  if (downloadBtn) downloadBtn.addEventListener('click', handleDownload);

  // Кнопка «График роста» и переключатель режимов графика
  const chartBtn = document.getElementById('gallery-btn-chart');
  if (chartBtn) {
    chartBtn.addEventListener('click', () => {
      isChartOpen = !isChartOpen;
      if (isChartOpen) {
        chartMode = resolveDefaultGrowthChartMode(currentCameraFrames);
        renderGrowthChart();
      } else {
        updateChartPanelUi();
      }
    });
  }
  const chartModeControl = document.getElementById('gallery-chart-mode-control');
  if (chartModeControl) {
    chartModeControl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const nextMode = btn.dataset.chartMode;
        if (nextMode && nextMode !== chartMode) {
          chartMode = nextMode;
          renderGrowthChart();
        }
      });
    });
  }

  const exportCsvBtn = document.getElementById('gallery-chart-export-csv');
  if (exportCsvBtn) {
    exportCsvBtn.addEventListener('click', handleExportGrowthCsv);
  }

  // Валидация поля длительности и динамический расчет FPS
  const durInput = document.getElementById('gallery-duration-input');
  if (durInput) {
    durInput.addEventListener('input', () => {
      updateFpsAndWarnings(loadedImages.length);
    });
  }

  // Жесты
  setupGestures();

  // Горячие клавиши
  window.addEventListener('keydown', (e) => {
    if (modal.classList.contains('hidden')) return;

    // Не перехватываем клавиши, если фокус в input
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
      return;
    }

    if (e.key === 'Escape') {
      e.preventDefault();
      closeGalleryModal();
    } else if (e.key === ' ' || e.code === 'Space') {
      e.preventDefault();
      togglePlayback();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      goToPrevFrame();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      goToNextFrame();
    } else if (e.key === 'Home') {
      e.preventDefault();
      goToFirstFrame();
    } else if (e.key === 'End') {
      e.preventDefault();
      goToLastFrame();
    }
  });
}
