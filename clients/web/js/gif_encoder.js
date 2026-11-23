// clients/web/js/gif_encoder.js
// Нативный клиентский кодировщик анимированных GIF (GIF89a) на Canvas2D
// Без внешних зависимостей, библиотек и CDN.

class ByteArray {
  constructor(capacity = 65536) {
    this.buffer = new Uint8Array(capacity);
    this.length = 0;
  }

  ensureCapacity(extra) {
    if (this.length + extra > this.buffer.length) {
      const newCap = Math.max(this.buffer.length * 2, this.length + extra + 65536);
      const newBuf = new Uint8Array(newCap);
      newBuf.set(this.buffer);
      this.buffer = newBuf;
    }
  }

  writeByte(val) {
    this.ensureCapacity(1);
    this.buffer[this.length++] = val & 0xff;
  }

  writeBytes(bytes) {
    this.ensureCapacity(bytes.length);
    this.buffer.set(bytes, this.length);
    this.length += bytes.length;
  }

  writeShort(val) {
    this.writeByte(val & 0xff);
    this.writeByte((val >> 8) & 0xff);
  }

  writeString(str) {
    for (let i = 0; i < str.length; i++) {
      this.writeByte(str.charCodeAt(i));
    }
  }

  toBlob() {
    return new Blob([this.buffer.subarray(0, this.length)], { type: 'image/gif' });
  }
}

// Квантование RGB: построение 256-цветной палитры и сопоставление пикселей
function buildPaletteAndIndexMap(framesData) {
  // Собираем выборку цветов со всех кадров
  const colorMap = new Map();
  for (const rgba of framesData) {
    const step = 4 * 4; // шаг для быстрой выборки
    for (let i = 0; i < rgba.length; i += step) {
      // Квантуем до 5 бит на канал (32x32x32 = 32768)
      const r = rgba[i] >> 3;
      const g = rgba[i + 1] >> 3;
      const b = rgba[i + 2] >> 3;
      const key = (r << 10) | (g << 5) | b;
      colorMap.set(key, (colorMap.get(key) || 0) + 1);
    }
  }

  // Сортируем по частоте использования
  const sorted = Array.from(colorMap.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 256);

  const palette = new Uint8Array(256 * 3);
  const colorLookup = new Map();

  for (let idx = 0; idx < sorted.length; idx++) {
    const key = sorted[idx][0];
    const r = ((key >> 10) & 0x1f) << 3;
    const g = ((key >> 5) & 0x1f) << 3;
    const b = (key & 0x1f) << 3;
    palette[idx * 3] = r;
    palette[idx * 3 + 1] = g;
    palette[idx * 3 + 2] = b;
    colorLookup.set(key, idx);
  }

  // Заполняем оставшиеся до 256
  for (let idx = sorted.length; idx < 256; idx++) {
    palette[idx * 3] = 0;
    palette[idx * 3 + 1] = 0;
    palette[idx * 3 + 2] = 0;
  }

  // Функция отображения пикселя RGBA на индекс в палитре
  const getIndex = (r, g, b) => {
    const key = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
    const exact = colorLookup.get(key);
    if (exact !== undefined) return exact;

    // Ближайший цвет по расстоянию
    let bestDist = Infinity;
    let bestIdx = 0;
    for (let i = 0; i < sorted.length; i++) {
      const pr = palette[i * 3];
      const pg = palette[i * 3 + 1];
      const pb = palette[i * 3 + 2];
      const dist = (r - pr) * (r - pr) + (g - pg) * (g - pg) + (b - pb) * (b - pb);
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
        if (dist === 0) break;
      }
    }
    colorLookup.set(key, bestIdx);
    return bestIdx;
  };

  return { palette, getIndex };
}

// LZW-компрессор для GIF
function lzwEncode(minCodeSize, indexedPixels, out) {
  out.writeByte(minCodeSize);

  const clearCode = 1 << minCodeSize;
  const eoiCode = clearCode + 1;

  let codeSize = minCodeSize + 1;
  let nextCode = eoiCode + 1;
  const maxCodeLimit = 4096;

  let curAccum = 0;
  let curBits = 0;

  const packet = new Uint8Array(256);
  let packetLen = 0;

  function flushPacket() {
    if (packetLen > 0) {
      out.writeByte(packetLen);
      for (let i = 0; i < packetLen; i++) {
        out.writeByte(packet[i]);
      }
      packetLen = 0;
    }
  }

  function writeBits(code) {
    curAccum |= code << curBits;
    curBits += codeSize;
    while (curBits >= 8) {
      packet[packetLen++] = curAccum & 0xff;
      curAccum >>= 8;
      curBits -= 8;
      if (packetLen === 254) {
        flushPacket();
      }
    }
  }

  function finishBits() {
    if (curBits > 0) {
      packet[packetLen++] = curAccum & 0xff;
      curAccum = 0;
      curBits = 0;
    }
    flushPacket();
    out.writeByte(0); // завершающий пустой блок субпакета
  }

  let codeTable = new Map();

  function resetTable() {
    codeTable.clear();
    for (let i = 0; i < clearCode; i++) {
      codeTable.set(String.fromCharCode(i), i);
    }
    codeSize = minCodeSize + 1;
    nextCode = eoiCode + 1;
  }

  resetTable();
  writeBits(clearCode);

  let prefix = '';
  for (let i = 0; i < indexedPixels.length; i++) {
    const k = String.fromCharCode(indexedPixels[i]);
    const combo = prefix + k;
    if (codeTable.has(combo)) {
      prefix = combo;
    } else {
      writeBits(codeTable.get(prefix));
      if (nextCode < maxCodeLimit) {
        codeTable.set(combo, nextCode++);
        if (nextCode > (1 << codeSize) && codeSize < 12) {
          codeSize++;
        }
      } else {
        writeBits(clearCode);
        resetTable();
      }
      prefix = k;
    }
  }

  if (prefix !== '') {
    writeBits(codeTable.get(prefix));
  }

  writeBits(eoiCode);
  finishBits();
}

/**
 * Кодирует набор изображений Image/HTMLImageElement в Blob GIF.
 * @param {Array<HTMLImageElement>} images - загруженные кадры
 * @param {number} totalDurationSec - общая длительность в секундах
 * @param {number} maxDimension - максимальная ширина/высота (по умолчанию 720)
 * @returns {Promise<Blob>}
 */
export async function encodeGifFromImages(images, totalDurationSec, maxDimension = 720) {
  if (!images || images.length === 0) {
    throw new Error('Нет кадров для экспорта GIF');
  }

  const firstImg = images[0];
  const origW = firstImg.naturalWidth || firstImg.width || 640;
  const origH = firstImg.naturalHeight || firstImg.height || 480;

  let width = origW;
  let height = origH;
  if (width > maxDimension || height > maxDimension) {
    if (width >= height) {
      height = Math.round((origH * maxDimension) / origW);
      width = maxDimension;
    } else {
      width = Math.round((origW * maxDimension) / origH);
      height = maxDimension;
    }
  }
  // Ширина и высота должны быть четными
  width = width - (width % 2);
  height = height - (height % 2);

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  // Извлекаем RGBA каждого кадра
  const framesRgba = [];
  for (const img of images) {
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);
    const imgData = ctx.getImageData(0, 0, width, height);
    framesRgba.push(imgData.data);
  }

  // Расчёт задержки кадра в сотых долях секунды
  const frameDelaySec = totalDurationSec / images.length;
  const delayHundredths = Math.max(2, Math.round(frameDelaySec * 100)); // минимум 20мс

  // Построение палитры 256 цветов
  const { palette, getIndex } = buildPaletteAndIndexMap(framesRgba);

  const out = new ByteArray(width * height * images.length);

  // 1. Заголовок GIF89a
  out.writeString('GIF89a');

  // 2. Logical Screen Descriptor
  out.writeShort(width);
  out.writeShort(height);
  // GCT Flag = 1, Color Resolution = 7, Sort Flag = 0, GCT Size = 7 (256 цветов) -> 0xF7
  out.writeByte(0xf7);
  out.writeByte(0); // Background Color Index
  out.writeByte(0); // Pixel Aspect Ratio

  // 3. Global Color Table (256 * 3 bytes)
  out.writeBytes(palette);

  // 4. Netscape 2.0 Application Extension (Зацикливание GIF)
  out.writeByte(0x21); // Extension Introducer
  out.writeByte(0xff); // Application Extension Label
  out.writeByte(11); // Block Size
  out.writeString('NETSCAPE2.0');
  out.writeByte(3); // Sub-block size
  out.writeByte(1); // Sub-block ID
  out.writeShort(0); // Loop count = 0 (бесконечно)
  out.writeByte(0); // Block Terminator

  // 5. Запись каждого кадра
  for (let f = 0; f < framesRgba.length; f++) {
    const rgba = framesRgba[f];

    // Graphic Control Extension
    out.writeByte(0x21);
    out.writeByte(0xf9);
    out.writeByte(4); // Block size
    out.writeByte(0x04); // Disposal method 1 (Do not dispose), no transparency
    out.writeShort(delayHundredths);
    out.writeByte(0); // Transparent color index
    out.writeByte(0); // Terminator

    // Image Descriptor
    out.writeByte(0x2c); // Image Separator
    out.writeShort(0); // Left
    out.writeShort(0); // Top
    out.writeShort(width);
    out.writeShort(height);
    out.writeByte(0); // Local Color Table Flag = 0

    // Индексируем пиксели кадра
    const numPixels = width * height;
    const indexed = new Uint8Array(numPixels);
    for (let p = 0; p < numPixels; p++) {
      const idx = p * 4;
      indexed[p] = getIndex(rgba[idx], rgba[idx + 1], rgba[idx + 2]);
    }

    // LZW кодирование
    lzwEncode(8, indexed, out);
  }

  // 6. GIF Trailer
  out.writeByte(0x3b);

  return out.toBlob();
}
