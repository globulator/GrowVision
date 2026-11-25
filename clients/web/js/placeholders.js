/**
 * GrowVision Web SPA — Тематические медиа-заглушки и весёлая загрузка.
 * Инварианты: 0 Unicode-эмодзи, только чистые SVG, серый текст #94a3b8.
 */

const PLACEHOLDERS = {
  // Категория A: 0 кадров в установке или галерее
  A: [
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M32 56V32" />
        <path d="M32 32C32 20 20 16 16 16C16 28 24 32 32 32Z" />
        <path d="M32 26C36 18 46 16 48 16C48 26 40 30 32 32" />
        <path d="M22 56H42" />
      </svg>`,
      text: 'Растение застеснялось. Фотографий нет',
    },
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="32" cy="32" r="22" />
        <circle cx="32" cy="32" r="14" />
        <circle cx="32" cy="32" r="6" />
        <line x1="12" y1="12" x2="52" y2="52" />
      </svg>`,
      text: 'Похоже, фотограф забыл снять крышку с объектива',
    },
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M20 28L16 16L28 22C30 21 34 21 36 22L48 16L44 28C48 32 48 38 44 42L48 52H16L20 42C16 38 16 32 20 28Z" />
        <circle cx="26" cy="32" r="2" fill="currentColor" />
        <circle cx="38" cy="32" r="2" fill="currentColor" />
        <path d="M32 36V38" />
        <rect x="12" y="52" width="40" height="6" rx="2" />
      </svg>`,
      text: 'Здесь ничего нет — или просто кто-то прошёлся по клавиатуре и стёр базу',
    },
  ],

  // Категория B: RPi / камера недоступна
  B: [
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 22H22L26 16H38L42 22H52C54.2 22 56 23.8 56 26V48C56 50.2 54.2 52 52 52H12C9.8 52 8 50.2 8 48V26C8 23.8 9.8 22 12 22Z" />
        <circle cx="32" cy="37" r="10" />
        <line x1="8" y1="56" x2="24" y2="56" />
        <line x1="24" y1="56" x2="28" y2="52" />
      </svg>`,
      text: 'Картинки не будет — фотограф сбежал',
    },
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M26 16H38L42 22H52C54.2 22 56 23.8 56 26V48C56 49.3 55.4 50.4 54.4 51.1M18 22H12C9.8 22 8 23.8 8 26V48C8 50.2 9.8 52 12 52H48" />
        <path d="M25 29C23.8 30.5 23 32.4 23 34.5C23 39.5 27 43.5 32 43.5C34.1 43.5 36 42.7 37.5 41.5" />
        <line x1="10" y1="10" x2="54" y2="54" />
      </svg>`,
      text: 'Камера ушла на перерыв. Проверьте подключение платы',
    },
  ],

  // Категория C: Обрыв связи с сервером
  C: [
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M8 32H24M24 28L28 36M40 32H56" />
        <path d="M34 26C38 24 44 26 44 32C44 38 38 40 34 38" />
        <circle cx="38" cy="29" r="1.5" fill="currentColor" />
        <line x1="20" y1="44" x2="28" y2="36" />
      </svg>`,
      text: 'Возможно, кто-то перегрыз провод',
    },
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="10" y="24" width="16" height="16" rx="2" />
        <line x1="4" y1="32" x2="10" y2="32" />
        <line x1="26" y1="28" x2="32" y2="28" />
        <line x1="26" y1="36" x2="32" y2="36" />
        <rect x="38" y="24" width="16" height="16" rx="2" />
        <line x1="54" y1="32" x2="60" y2="32" />
      </svg>`,
      text: 'Связь с сервером временно оборвалась',
    },
  ],

  // Категория D: Битый кадр / onerror
  D: [
    {
      svg: `<svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="10" y="12" width="44" height="40" rx="4" />
        <path d="M10 40L24 26L36 38L42 32L54 44" />
        <line x1="28" y1="12" x2="34" y2="30" stroke-dasharray="2 2" />
        <line x1="34" y1="30" x2="40" y2="52" stroke-dasharray="2 2" />
      </svg>`,
      text: 'Кадр потерялся по дороге с грядки',
    },
  ],
};

const LOADING_PHRASES = [
  'Ждём, когда растение улыбнётся на камеру...',
  'Наводим резкость на самый фотогеничный листик...',
  'Просим росток не моргать...',
  'Ищем удачный ракурс для фотосессии...',
  'Уговариваем бутон повернуться к свету...',
];

/**
 * Возвращает HTML случайной тематической заглушки указанной категории ('A', 'B', 'C', 'D').
 */
export function getPlaceholderHTML(category = 'A') {
  const list = PLACEHOLDERS[category] || PLACEHOLDERS.A;
  const item = list[Math.floor(Math.random() * list.length)];
  return `
    <div class="media-placeholder category-${category.toLowerCase()}">
      <div class="placeholder-icon-wrap">${item.svg}</div>
      <p class="placeholder-caption">${item.text}</p>
    </div>
  `.trim();
}

/**
 * Возвращает случайную весёлую фразу для индикатора съёмки.
 */
export function getRandomLoadingPhrase() {
  return LOADING_PHRASES[Math.floor(Math.random() * LOADING_PHRASES.length)];
}

/**
 * Возвращает HTML фирменной заглушки для модалки создания/редактирования установки.
 */
export function getUnitEditorPlaceholderHTML(caption = 'Растение ещё не готово к фотосессии...') {
  return `
    <div class="media-placeholder category-a unit-editor-placeholder">
      <div class="placeholder-icon-wrap">
        <svg viewBox="0 0 64 64" width="52" height="52" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M14 22H22L26 16H38L42 22H50C52.2 22 54 23.8 54 26V48C54 50.2 52.2 52 50 52H14C11.8 52 10 50.2 10 48V26C10 23.8 11.8 22 14 22Z" />
          <path d="M32 43V33" />
          <path d="M32 35C32 30 26 28 24 28C24 34 28 36 32 36Z" />
          <path d="M32 33C34 29 39 28 40 28C40 33 36 35 32 36" />
        </svg>
      </div>
      <p class="placeholder-caption">${caption}</p>
    </div>
  `.trim();
}

/**
 * Возвращает HTML фирменного лоадера GrowVision (анимация ростка + случайная фраза).
 */
export function getLoadingSpinnerHTML(phrase = getRandomLoadingPhrase()) {
  return `
    <div class="snap-loading-placeholder plant-lifecycle-spinner">
      <svg viewBox="0 0 64 64" width="56" height="56" class="plant-cycle-svg" aria-hidden="true">
        <path class="plant-ground" d="M12 52 H52" stroke="#334155" stroke-width="2" stroke-linecap="round"/>
        <ellipse class="plant-seed" cx="32" cy="50" rx="4" ry="3" fill="#10b981"/>
        <path class="plant-stem" d="M32 50 Q32 36 32 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round"/>
        <path class="plant-sprout-left" d="M32 38 Q22 36 20 28 Q28 28 32 36" fill="#10b981"/>
        <path class="plant-leaf-right" d="M32 30 Q44 26 44 16 Q34 18 32 26" fill="#059669"/>
        <path class="plant-top-leaf" d="M32 24 Q32 10 32 8 Q36 14 32 24" fill="#34d399"/>
      </svg>
      <span class="placeholder-text placeholder-caption">${phrase}</span>
    </div>
  `.trim();
}
