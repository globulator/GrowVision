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
let availableCameras = [];
let selectedCameraIndex = 0; // индекс в availableCameras
let currentLayer = 'raw'; // 'raw' или 'ann'

// Кеш загруженных изображений для текущей выборки
let loadedImages = []; // Array of Image objects
let currentFrameIndex = 0;
let isPlaying = false;
let playTimer = null;
let currentLoadId = 0;
let isEncodingGif = false;

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
  clearInterval(playTimer);

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
  clearInterval(playTimer);
  currentLoadId++;

  // Очищаем кеш изображений для освобождения памяти
  loadedImages.forEach((img) => {
    img.src = '';
  });
  loadedImages = [];
  allRawFrames = [];
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
          images[idx] = img;
          resolve(img);
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
        img.src = url;
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

  // Открываем последний кадр на паузе
  currentFrameIndex = Math.max(0, loadedImages.length - 1);
  setControlsDisabled(false);
  renderCurrentFrame();
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
      durErrEl.textContent = 'Длительность должна быть целым числом не меньше 1 с';
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
      fpsWarnEl.textContent = 'При экспорте FPS будет ограничен до 30';
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

function renderCurrentFrame() {
  const box = document.getElementById('gallery-image-box');
  const metaBox = document.getElementById('gallery-meta');
  const slider = document.getElementById('gallery-slider');
  if (!box || loadedImages.length === 0) return;

  const img = loadedImages[currentFrameIndex];
  if (!img) return;

  box.innerHTML = '';
  if (img.dataset.broken === 'true') {
    box.innerHTML = getPlaceholderHTML('D');
  } else {
    const displayImg = document.createElement('img');
    displayImg.className = 'gallery-main-img';
    displayImg.src = img.src;
    displayImg.alt = `Кадр ${currentFrameIndex + 1}`;
    displayImg.onerror = () => {
      box.innerHTML = getPlaceholderHTML('D');
    };
    box.appendChild(displayImg);
  }

  // Обновление ползунка
  if (slider) {
    slider.min = '0';
    slider.max = String(loadedImages.length - 1);
    slider.value = String(currentFrameIndex);
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
    metaBox.textContent = `Кадр ${currentFrameIndex + 1} из ${total} | ${timeStr} | Площадь: ${areaStr}`;
  }

  updatePlaybackUi();
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

  const btns = [btnFirst, btnPrev, btnPlay, btnNext, btnLast, slider, durInput, downloadBtn];
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

  // Иконка Play / Pause
  if (btnPlay) {
    btnPlay.disabled = false;
    btnPlay.innerHTML = isPlaying
      ? `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`
      : `<svg class="icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
    btnPlay.title = isPlaying ? 'Пауза' : 'Воспроизвести';
  }
}

function startPlayback() {
  if (loadedImages.length <= 1) return;

  // Если нажали Play на последнем кадре — начинаем с 1-го кадра
  if (currentFrameIndex >= loadedImages.length - 1) {
    currentFrameIndex = 0;
  }

  isPlaying = true;
  updatePlaybackUi();

  const durInput = document.getElementById('gallery-duration-input');
  let durationSec = parseFloat(durInput ? durInput.value : '3') || 3;
  durationSec = Math.max(1, Math.min(loadedImages.length, durationSec));

  const frameIntervalMs = Math.max(30, Math.round((durationSec * 1000) / loadedImages.length));

  clearInterval(playTimer);
  playTimer = setInterval(() => {
    if (currentFrameIndex < loadedImages.length - 1) {
      currentFrameIndex++;
      renderCurrentFrame();
    } else {
      // Завершение одного прохода до последнего кадра
      pausePlayback();
    }
  }, frameIntervalMs);
}

function pausePlayback() {
  isPlaying = false;
  clearInterval(playTimer);
  playTimer = null;
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
