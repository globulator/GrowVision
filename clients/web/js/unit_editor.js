// clients/web/js/unit_editor.js
// Единая модалка создания и редактирования установки (активной и архивной)

import { api } from './api.js';
import { showToast } from './dialogs.js';
import { getPlaceholderHTML, getUnitEditorPlaceholderHTML } from './placeholders.js';

let currentRole = 'guest';
let editingUnit = null;
let loadedIntervalHours = 1;
let initialCameraIds = [];
let onUnitSavedCallback = null;
let isBusy = false;
let unitEditorOpenSeq = 0;

export function setUnitEditorRole(role) {
  currentRole = role || 'guest';
  if (currentRole === 'guest') {
    closeUnitEditorModal();
  }
}

export function setUnitSavedHandler(fn) {
  onUnitSavedCallback = fn;
}

function showEditorError(msg) {
  const errEl = document.getElementById('unit-editor-error');
  if (!errEl) return;
  if (msg) {
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  } else {
    errEl.textContent = '';
    errEl.classList.add('hidden');
  }
}

function hideDeleteConfirm() {
  const box = document.getElementById('unit-editor-delete-confirm');
  const delBtn = document.getElementById('unit-editor-delete-btn');
  if (box) box.classList.add('hidden');
  if (delBtn && editingUnit && editingUnit.is_archived && currentRole === 'developer') {
    delBtn.classList.remove('hidden');
  }
}

function showDeleteConfirm() {
  const box = document.getElementById('unit-editor-delete-confirm');
  const delBtn = document.getElementById('unit-editor-delete-btn');
  if (delBtn) delBtn.classList.add('hidden');
  if (box) box.classList.remove('hidden');
}

function formatNextCaptureLabel(unit) {
  if (!unit) return '';
  if (unit.is_archived) return 'Установка в архиве';
  if (!unit.next_capture_at) return '';
  const dt = new Date(unit.next_capture_at);
  const timeStr = !isNaN(dt.getTime())
    ? dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : unit.next_capture_at;
  return `След. кадр: ${timeStr}`;
}

function renderPreview(unit) {
  const mediaEl = document.getElementById('unit-editor-preview-media');
  const nextEl = document.getElementById('unit-editor-next-capture');
  if (mediaEl) {
    const framesCount = unit ? Number(unit.frames_count || 0) : 0;
    const frameUrl = unit ? unit.last_frame_url || unit.last_frame_raw_url : null;
    if (unit && framesCount > 0 && frameUrl) {
      mediaEl.innerHTML = '';
      const img = document.createElement('img');
      img.className = 'unit-editor-preview-img';
      img.alt = unit.name || 'Превью установки';
      img.src = `${frameUrl}?t=${Date.now()}`;
      img.onerror = () => {
        mediaEl.innerHTML = getPlaceholderHTML('D');
      };
      mediaEl.appendChild(img);
    } else {
      mediaEl.innerHTML = getUnitEditorPlaceholderHTML('Растение ещё не готово к фотосессии...');
    }
  }

  if (nextEl) {
    const label = formatNextCaptureLabel(unit);
    if (label) {
      nextEl.textContent = label;
      nextEl.classList.remove('hidden');
    } else {
      nextEl.textContent = '';
      nextEl.classList.add('hidden');
    }
  }
}

function formatCameraLabel(cam) {
  const name = (cam && cam.auto_name ? String(cam.auto_name) : '').trim();
  if (name && name !== cam.hardware_id) {
    return name;
  }
  return cam && cam.hardware_id ? cam.hardware_id : `Камера #${cam ? cam.id : '?'}`;
}

function populateCameraOptions(cameras, unit) {
  const select = document.getElementById('unit-editor-camera');
  if (!select) return;

  select.innerHTML = '';
  const emptyOpt = document.createElement('option');
  emptyOpt.value = '';
  emptyOpt.textContent = 'Без камеры';
  select.appendChild(emptyOpt);

  const unitId = unit ? Number(unit.id) : null;
  const attachedCams = [];
  const freeCams = [];

  (cameras || []).forEach((cam) => {
    if (unitId !== null && Number(cam.installation_id) === unitId) {
      attachedCams.push(cam);
    } else if (cam.installation_id === null || cam.installation_id === undefined) {
      freeCams.push(cam);
    }
  });

  initialCameraIds = attachedCams.map((c) => Number(c.id));

  attachedCams.forEach((cam) => {
    const opt = document.createElement('option');
    opt.value = String(cam.id);
    const statusLabel = cam.is_online ? 'онлайн' : 'офлайн';
    opt.textContent = `${formatCameraLabel(cam)} (текущая, ${statusLabel})`;
    select.appendChild(opt);
  });

  freeCams.forEach((cam) => {
    const opt = document.createElement('option');
    opt.value = String(cam.id);
    const statusLabel = cam.is_online ? 'онлайн' : 'офлайн';
    opt.textContent = `${formatCameraLabel(cam)} (${statusLabel})`;
    select.appendChild(opt);
  });

  select.value = initialCameraIds.length > 0 ? String(initialCameraIds[0]) : '';
}

function applyRoleAndStatusConstraints(unit) {
  const isDev = currentRole === 'developer';
  const isArchived = Boolean(unit && unit.is_archived);

  const nameInput = document.getElementById('unit-editor-name');
  const cultureInput = document.getElementById('unit-editor-culture');
  const intervalInput = document.getElementById('unit-editor-interval');
  const cameraSelect = document.getElementById('unit-editor-camera');
  const lightPinInput = document.getElementById('unit-editor-light-pin');
  const flashPinInput = document.getElementById('unit-editor-flash-pin');
  const delayInput = document.getElementById('unit-editor-delay');
  const arucoInput = document.getElementById('unit-editor-aruco');
  const roleHintEl = document.getElementById('unit-editor-role-hint');

  if (nameInput) nameInput.disabled = false;
  if (cultureInput) cultureInput.disabled = false;
  if (delayInput) delayInput.disabled = false;

  if (intervalInput) intervalInput.disabled = !isDev;
  if (arucoInput) arucoInput.disabled = !isDev;
  if (lightPinInput) lightPinInput.disabled = !isDev || isArchived;
  if (flashPinInput) flashPinInput.disabled = !isDev || isArchived;
  if (cameraSelect) cameraSelect.disabled = !isDev || isArchived;

  if (roleHintEl) {
    if (!isDev) {
      roleHintEl.textContent =
        'Режим биолога: доступно изменение названия, культуры и задержки съёмки.';
      roleHintEl.classList.remove('hidden');
    } else if (isArchived) {
      roleHintEl.textContent =
        'Установка в архиве: изменение GPIO-пинов и привязка камер доступны после возврата из архива.';
      roleHintEl.classList.remove('hidden');
    } else {
      roleHintEl.textContent = '';
      roleHintEl.classList.add('hidden');
    }
  }

  const archiveBtn = document.getElementById('unit-editor-archive-btn');
  const unarchiveBtn = document.getElementById('unit-editor-unarchive-btn');
  const deleteBtn = document.getElementById('unit-editor-delete-btn');
  const deleteConfirm = document.getElementById('unit-editor-delete-confirm');

  if (deleteConfirm) deleteConfirm.classList.add('hidden');

  if (!unit) {
    if (archiveBtn) archiveBtn.classList.add('hidden');
    if (unarchiveBtn) unarchiveBtn.classList.add('hidden');
    if (deleteBtn) deleteBtn.classList.add('hidden');
  } else if (isArchived) {
    if (archiveBtn) archiveBtn.classList.add('hidden');
    if (unarchiveBtn) unarchiveBtn.classList.toggle('hidden', !isDev);
    if (deleteBtn) deleteBtn.classList.toggle('hidden', !isDev);
  } else {
    if (archiveBtn) archiveBtn.classList.toggle('hidden', !isDev);
    if (unarchiveBtn) unarchiveBtn.classList.add('hidden');
    if (deleteBtn) deleteBtn.classList.add('hidden');
  }

  syncUnitLightControls(unit);
}

function syncUnitLightControls(unit) {
  const lightCard = document.getElementById('unit-editor-light-card');
  const flashCard = document.getElementById('unit-editor-flash-card');
  const lightToggle = document.getElementById('unit-editor-light-toggle');
  const flashToggle = document.getElementById('unit-editor-flash-toggle');
  const lightLockedEl = document.getElementById('unit-editor-light-locked');
  const flashLockedEl = document.getElementById('unit-editor-flash-locked');

  const visible = Boolean(unit) && currentRole === 'developer';
  if (lightCard) lightCard.classList.toggle('hidden', !visible);
  if (flashCard) flashCard.classList.toggle('hidden', !visible);

  if (!visible) {
    if (lightLockedEl) lightLockedEl.classList.add('hidden');
    if (flashLockedEl) flashLockedEl.classList.add('hidden');
    return;
  }

  const isArchived = Boolean(unit.is_archived);
  const indLight =
    unit.individual_light_state !== undefined
      ? Boolean(unit.individual_light_state)
      : Boolean(unit.light_state !== undefined ? unit.light_state : true);
  const indFlash =
    unit.individual_flash_state !== undefined
      ? Boolean(unit.individual_flash_state)
      : Boolean(unit.flash_state !== undefined ? unit.flash_state : false);

  const lightLocked =
    Boolean(unit.light_locked) ||
    (unit.global_light_mode !== undefined && unit.global_light_mode !== 'default');
  const flashLocked =
    Boolean(unit.flash_locked) ||
    (unit.global_flash_mode !== undefined && unit.global_flash_mode !== 'default');

  if (lightToggle) {
    lightToggle.checked = indLight;
    lightToggle.disabled = lightLocked || isArchived;
  }
  if (lightLockedEl) {
    lightLockedEl.classList.toggle('hidden', !lightLocked);
  }

  if (flashToggle) {
    flashToggle.checked = indFlash;
    flashToggle.disabled = flashLocked || isArchived;
  }
  if (flashLockedEl) {
    flashLockedEl.classList.toggle('hidden', !flashLocked);
  }
}

async function handleUnitLightToggle(isFlash) {
  if (!editingUnit || currentRole !== 'developer' || editingUnit.is_archived) return;
  const toggle = document.getElementById(
    isFlash ? 'unit-editor-flash-toggle' : 'unit-editor-light-toggle'
  );
  if (!toggle) return;

  const isLocked = isFlash
    ? Boolean(editingUnit.flash_locked) ||
      (editingUnit.global_flash_mode !== undefined && editingUnit.global_flash_mode !== 'default')
    : Boolean(editingUnit.light_locked) ||
      (editingUnit.global_light_mode !== undefined && editingUnit.global_light_mode !== 'default');
  if (isLocked) {
    syncUnitLightControls(editingUnit);
    return;
  }

  const nextState = Boolean(toggle.checked);
  const targetId = editingUnit.id;
  showEditorError('');
  toggle.disabled = true;

  try {
    const res = isFlash
      ? await api.controlLightFlash(targetId, nextState)
      : await api.controlLightMain(targetId, nextState);
    if (res && res.status === 'error') {
      throw new Error(res.result_message || 'Ошибка переключения реле');
    }
    if (editingUnit && Number(editingUnit.id) === Number(targetId)) {
      if (isFlash) {
        editingUnit.individual_flash_state = nextState;
        editingUnit.flash_state =
          res && res.effective_state !== undefined ? Boolean(res.effective_state) : nextState;
      } else {
        editingUnit.individual_light_state = nextState;
        editingUnit.light_state =
          res && res.effective_state !== undefined ? Boolean(res.effective_state) : nextState;
      }
      syncUnitLightControls(editingUnit);
    }
    showToast(
      isFlash
        ? `Вспышка «${editingUnit ? editingUnit.name : ''}»: ${nextState ? 'включена' : 'выключена'}`
        : `Основной свет «${editingUnit ? editingUnit.name : ''}»: ${nextState ? 'включён' : 'выключен'}`
    );
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    toggle.checked = !nextState;
    showEditorError(
      err.message ||
        (isFlash ? 'Не удалось переключить вспышку' : 'Не удалось переключить основной свет')
    );
  } finally {
    if (editingUnit && Number(editingUnit.id) === Number(targetId)) {
      syncUnitLightControls(editingUnit);
    }
  }
}

function setButtonsBusy(busy) {
  isBusy = busy;
  const ids = [
    'unit-editor-save-btn',
    'unit-editor-cancel-btn',
    'unit-editor-archive-btn',
    'unit-editor-unarchive-btn',
    'unit-editor-delete-btn',
    'unit-editor-delete-confirm-yes',
  ];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

async function openUnitModal(unit = null) {
  if (currentRole === 'guest') return;
  if (!unit && currentRole !== 'developer') return;

  const modal = document.getElementById('modal-unit-editor');
  if (!modal) return;

  const openSeq = ++unitEditorOpenSeq;
  editingUnit = unit || null;
  initialCameraIds = [];
  loadedIntervalHours = 1;
  showEditorError('');
  setButtonsBusy(false);

  const titleEl = document.getElementById('unit-editor-title');
  if (titleEl) {
    titleEl.textContent = editingUnit
      ? `Редактирование установки: ${editingUnit.name}`
      : 'Создание новой установки';
  }

  renderPreview(editingUnit);

  const nameInput = document.getElementById('unit-editor-name');
  const cultureInput = document.getElementById('unit-editor-culture');
  const intervalInput = document.getElementById('unit-editor-interval');
  const lightPinInput = document.getElementById('unit-editor-light-pin');
  const flashPinInput = document.getElementById('unit-editor-flash-pin');
  const delayInput = document.getElementById('unit-editor-delay');
  const arucoInput = document.getElementById('unit-editor-aruco');

  if (nameInput) nameInput.value = editingUnit ? editingUnit.name || '' : '';
  if (cultureInput) cultureInput.value = editingUnit ? editingUnit.culture_name || '' : '';
  if (lightPinInput) {
    lightPinInput.value =
      editingUnit && editingUnit.light_gpio_pin !== undefined
        ? String(editingUnit.light_gpio_pin)
        : '17';
  }
  if (flashPinInput) {
    flashPinInput.value =
      editingUnit && editingUnit.flash_gpio_pin !== undefined
        ? String(editingUnit.flash_gpio_pin)
        : '27';
  }
  if (delayInput) {
    delayInput.value =
      editingUnit &&
      editingUnit.capture_delay_sec !== null &&
      editingUnit.capture_delay_sec !== undefined
        ? String(editingUnit.capture_delay_sec)
        : '';
  }
  if (arucoInput) {
    arucoInput.value =
      editingUnit &&
      editingUnit.aruco_size_mm !== null &&
      editingUnit.aruco_size_mm !== undefined
        ? String(editingUnit.aruco_size_mm)
        : '';
  }
  if (intervalInput) {
    intervalInput.value = '1';
  }

  // Предварительно заполняем select из unit.cameras до ответа /api/cameras
  populateCameraOptions(
    editingUnit && Array.isArray(editingUnit.cameras)
      ? editingUnit.cameras.map((c) => ({ ...c, installation_id: editingUnit.id }))
      : [],
    editingUnit
  );

  applyRoleAndStatusConstraints(editingUnit);

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');

  // Подгружаем настройки расписания, список камер и свежее состояние установки с сервера
  try {
    const requests = [
      api.getSystemSettings(),
      api.getCameras(),
    ];
    if (editingUnit && currentRole === 'developer' && typeof api.getInstallation === 'function') {
      requests.push(api.getInstallation(editingUnit.id));
    }
    const [settingsRes, camerasRes, freshUnitRes] = await Promise.allSettled(requests);

    if (openSeq !== unitEditorOpenSeq || modal.classList.contains('hidden')) return;

    if (settingsRes.status === 'fulfilled' && settingsRes.value) {
      const s = settingsRes.value;
      loadedIntervalHours = Number(s.capture_interval_hours || 1);
      if (intervalInput) {
        intervalInput.value = String(loadedIntervalHours);
      }
      if (delayInput) {
        const globalDelay =
          s.capture_delay_sec !== undefined && s.capture_delay_sec !== null
            ? s.capture_delay_sec
            : 1.5;
        delayInput.placeholder = `По умолчанию (${globalDelay} с)`;
      }
    }

    if (camerasRes.status === 'fulfilled' && Array.isArray(camerasRes.value)) {
      populateCameraOptions(camerasRes.value, editingUnit);
    }

    if (
      freshUnitRes &&
      freshUnitRes.status === 'fulfilled' &&
      freshUnitRes.value &&
      editingUnit &&
      Number(freshUnitRes.value.id) === Number(editingUnit.id)
    ) {
      editingUnit = { ...editingUnit, ...freshUnitRes.value };
      syncUnitLightControls(editingUnit);
    }
  } catch (_) {
    // Ошибки фоновой подгрузки не блокируют форму
  }
}

export function openUnitCreateModal() {
  return openUnitModal(null);
}

export function openUnitEditModal(unit) {
  return openUnitModal(unit);
}

export function closeUnitEditorModal() {
  unitEditorOpenSeq += 1;
  const modal = document.getElementById('modal-unit-editor');
  if (modal) {
    modal.classList.add('hidden');
  }
  document.body.classList.remove('modal-open');
  editingUnit = null;
  initialCameraIds = [];
  isBusy = false;
  showEditorError('');
  hideDeleteConfirm();
  syncUnitLightControls(null);
}

function showWarningsIfAny(warnings) {
  if (!Array.isArray(warnings) || warnings.length === 0) return;
  warnings.forEach((w) => showToast(w, 'info', 5500));
}

async function handleSaveSubmit(e) {
  e.preventDefault();
  if (isBusy || currentRole === 'guest') return;
  showEditorError('');

  const isDev = currentRole === 'developer';
  const name = (document.getElementById('unit-editor-name')?.value || '').trim();
  const culture = (document.getElementById('unit-editor-culture')?.value || '').trim();
  const rawInterval = (document.getElementById('unit-editor-interval')?.value || '').trim();
  const rawLightPin = (document.getElementById('unit-editor-light-pin')?.value || '').trim();
  const rawFlashPin = (document.getElementById('unit-editor-flash-pin')?.value || '').trim();
  const rawDelay = (document.getElementById('unit-editor-delay')?.value || '').trim();
  const rawAruco = (document.getElementById('unit-editor-aruco')?.value || '').trim();
  const selectedCamVal = document.getElementById('unit-editor-camera')?.value ?? '';

  if (!name) {
    showEditorError('Укажите название установки');
    return;
  }
  if (!culture) {
    showEditorError('Укажите выращиваемую культуру');
    return;
  }

  let delayVal = null;
  if (rawDelay !== '') {
    const parsed = Number(rawDelay.replace(',', '.'));
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 30) {
      showEditorError('Задержка прогрева должна быть от 0 до 30 секунд');
      return;
    }
    delayVal = Math.round(parsed * 100) / 100;
  }

  let arucoVal = null;
  if (isDev && rawAruco !== '') {
    const parsed = Number(rawAruco.replace(',', '.'));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      showEditorError('Размер маркера ArUco должен быть положительным числом');
      return;
    }
    arucoVal = parsed;
  }

  let intervalVal = loadedIntervalHours;
  if (isDev && rawInterval !== '') {
    const parsed = Number(rawInterval);
    if (!Number.isInteger(parsed) || parsed < 1) {
      showEditorError('Расписание съёмки должно быть целым числом часов (≥ 1)');
      return;
    }
    intervalVal = parsed;
  }

  let lightPin = editingUnit ? Number(editingUnit.light_gpio_pin) : 17;
  let flashPin = editingUnit ? Number(editingUnit.flash_gpio_pin) : 27;
  if (isDev && (!editingUnit || !editingUnit.is_archived)) {
    lightPin = Number(rawLightPin);
    flashPin = Number(rawFlashPin);
    if (!Number.isInteger(lightPin) || lightPin < 0 || lightPin > 53) {
      showEditorError('GPIO пин света должен быть целым числом от 0 до 53');
      return;
    }
    if (!Number.isInteger(flashPin) || flashPin < 0 || flashPin > 53) {
      showEditorError('GPIO пин вспышки должен быть целым числом от 0 до 53');
      return;
    }
  }

  setButtonsBusy(true);
  try {
    let savedUnit = null;
    if (!editingUnit) {
      if (!isDev) {
        showEditorError('Создавать установки может только разработчик');
        setButtonsBusy(false);
        return;
      }
      savedUnit = await api.createInstallation({
        name,
        culture_name: culture,
        light_gpio_pin: lightPin,
        flash_gpio_pin: flashPin,
        aruco_size_mm: arucoVal,
        capture_delay_sec: delayVal,
      });

      if (intervalVal !== loadedIntervalHours) {
        await api.patchSystemSettings({ capture_interval_hours: intervalVal });
      }
      if (selectedCamVal !== '') {
        await api.bindCamera(Number(selectedCamVal), savedUnit.id);
      }
      showToast(`Установка «${savedUnit.name}» создана`);
      showWarningsIfAny(savedUnit.warnings);
    } else {
      const patchPayload = {
        name,
        culture_name: culture,
        capture_delay_sec: delayVal,
      };
      if (isDev) {
        patchPayload.aruco_size_mm = arucoVal;
        if (!editingUnit.is_archived) {
          if (lightPin !== Number(editingUnit.light_gpio_pin)) {
            patchPayload.light_gpio_pin = lightPin;
          }
          if (flashPin !== Number(editingUnit.flash_gpio_pin)) {
            patchPayload.flash_gpio_pin = flashPin;
          }
        }
      }

      savedUnit = await api.patchInstallation(editingUnit.id, patchPayload);

      if (isDev && intervalVal !== loadedIntervalHours) {
        await api.patchSystemSettings({ capture_interval_hours: intervalVal });
      }

      if (isDev && !editingUnit.is_archived) {
        const prevPrimary = initialCameraIds.length > 0 ? String(initialCameraIds[0]) : '';
        if (selectedCamVal !== prevPrimary) {
          for (const prevId of initialCameraIds) {
            if (String(prevId) !== selectedCamVal) {
              await api.bindCamera(prevId, null);
            }
          }
          if (selectedCamVal !== '') {
            await api.bindCamera(Number(selectedCamVal), editingUnit.id);
          }
        }
      }

      showToast(`Установка «${savedUnit.name}» сохранена`);
      showWarningsIfAny(savedUnit.warnings);
    }

    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Не удалось сохранить установку');
  } finally {
    setButtonsBusy(false);
  }
}

async function handleArchiveClick() {
  if (!editingUnit || isBusy || currentRole !== 'developer') return;
  showEditorError('');
  setButtonsBusy(true);
  try {
    await api.archiveInstallation(editingUnit.id);
    showToast('Установка переведена в архив');
    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Ошибка перевода в архив');
  } finally {
    setButtonsBusy(false);
  }
}

async function handleUnarchiveClick() {
  if (!editingUnit || isBusy || currentRole !== 'developer') return;
  showEditorError('');
  setButtonsBusy(true);
  try {
    const res = await api.unarchiveInstallation(editingUnit.id);
    showToast('Установка возвращена из архива');
    showWarningsIfAny(res && res.warnings);
    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Ошибка восстановления из архива');
  } finally {
    setButtonsBusy(false);
  }
}

async function handleDeleteConfirmYes() {
  if (!editingUnit || !editingUnit.is_archived || isBusy || currentRole !== 'developer') return;
  showEditorError('');
  setButtonsBusy(true);
  try {
    await api.deleteInstallation(editingUnit.id);
    showToast('Установка удалена');
    closeUnitEditorModal();
    if (typeof onUnitSavedCallback === 'function') {
      await onUnitSavedCallback();
    }
  } catch (err) {
    showEditorError(err.message || 'Не удалось удалить установку');
    hideDeleteConfirm();
  } finally {
    setButtonsBusy(false);
  }
}

export function initUnitEditor() {
  const modal = document.getElementById('modal-unit-editor');
  const closeBtn = document.getElementById('modal-unit-editor-close');
  const cancelBtn = document.getElementById('unit-editor-cancel-btn');
  const form = document.getElementById('unit-editor-form');
  const archiveBtn = document.getElementById('unit-editor-archive-btn');
  const unarchiveBtn = document.getElementById('unit-editor-unarchive-btn');
  const deleteBtn = document.getElementById('unit-editor-delete-btn');
  const deleteYesBtn = document.getElementById('unit-editor-delete-confirm-yes');
  const deleteNoBtn = document.getElementById('unit-editor-delete-confirm-no');
  const lightToggle = document.getElementById('unit-editor-light-toggle');
  const flashToggle = document.getElementById('unit-editor-flash-toggle');

  if (closeBtn) closeBtn.addEventListener('click', closeUnitEditorModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeUnitEditorModal);
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeUnitEditorModal();
    });
  }

  if (form) form.addEventListener('submit', handleSaveSubmit);
  if (archiveBtn) archiveBtn.addEventListener('click', handleArchiveClick);
  if (unarchiveBtn) unarchiveBtn.addEventListener('click', handleUnarchiveClick);
  if (deleteBtn) deleteBtn.addEventListener('click', showDeleteConfirm);
  if (deleteYesBtn) deleteYesBtn.addEventListener('click', handleDeleteConfirmYes);
  if (deleteNoBtn) deleteNoBtn.addEventListener('click', hideDeleteConfirm);
  if (lightToggle) {
    lightToggle.addEventListener('change', () => handleUnitLightToggle(false));
  }
  if (flashToggle) {
    flashToggle.addEventListener('change', () => handleUnitLightToggle(true));
  }

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
      e.preventDefault();
      closeUnitEditorModal();
    }
  });
}
