// clients/web/js/components/svg_chart.js
// Общий переиспользуемый построитель SVG-графиков для галереи и климата

const SVG_NS = 'http://www.w3.org/2000/svg';

function resolveSvg(svgOrId) {
  if (!svgOrId) return null;
  if (typeof svgOrId === 'string') {
    return typeof document !== 'undefined' ? document.getElementById(svgOrId) : null;
  }
  return svgOrId;
}

export function createSvgElement(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, val]) => {
    if (val !== undefined && val !== null) {
      el.setAttribute(key, String(val));
    }
  });
  return el;
}

/**
 * Рендерит одно- или многосерийный линейный SVG-график с сеткой, подписями осей и интерактивными точками.
 */
export function renderSvgLineChart(svgOrId, options = {}) {
  const svg = resolveSvg(svgOrId);
  if (!svg) return { rendered: false };
  svg.innerHTML = '';

  const width = Number(options.width) || 360;
  const height = Number(options.height) || 120;
  const padL = options.padL !== undefined ? Number(options.padL) : 36;
  const padR = options.padR !== undefined ? Number(options.padR) : 10;
  const padT = options.padT !== undefined ? Number(options.padT) : 12;
  const padB = options.padB !== undefined ? Number(options.padB) : 18;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  const seriesList = Array.isArray(options.series) ? options.series : [];
  const allPoints = seriesList.flatMap((s) => (Array.isArray(s.points) ? s.points : []));
  const allYValues = allPoints.map((p) => Number(p.y)).filter(Number.isFinite);

  if (allPoints.length === 0 || allYValues.length === 0) {
    if (options.emptyText) {
      const txt = createSvgElement('text', {
        x: width / 2,
        y: height / 2,
        fill: '#94a3b8',
        'font-size': 12,
        'text-anchor': 'middle',
      });
      txt.textContent = options.emptyText;
      svg.appendChild(txt);
    }
    return { rendered: false };
  }

  const yCfg = options.yAxis || {};
  let minY = yCfg.minValue !== undefined ? Number(yCfg.minValue) : Math.min(...allYValues);
  let maxY = yCfg.maxValue !== undefined ? Number(yCfg.maxValue) : Math.max(...allYValues);

  const minSpan = yCfg.minSpan !== undefined ? Number(yCfg.minSpan) : 0.01;
  const padRatio = yCfg.paddingRatio !== undefined ? Number(yCfg.paddingRatio) : 0.12;
  if (Math.abs(maxY - minY) < minSpan) {
    if (yCfg.minValue === undefined) minY -= 1;
    if (yCfg.maxValue === undefined) maxY += 1;
  } else if (padRatio > 0) {
    const margin = (maxY - minY) * padRatio;
    if (yCfg.minValue === undefined) minY -= margin;
    if (yCfg.maxValue === undefined) maxY += margin;
  }
  const ySpan = Math.max(1e-6, maxY - minY);

  // Сетка и подписи по оси Y
  const ySteps = yCfg.steps !== undefined ? Number(yCfg.steps) : 2;
  const formatY =
    typeof yCfg.formatLabel === 'function' ? yCfg.formatLabel : (val) => val.toFixed(1);

  for (let i = 0; i <= ySteps; i += 1) {
    const ratio = ySteps > 0 ? i / ySteps : 0;
    const y = padT + plotH * ratio;
    const val = maxY - ySpan * ratio;

    svg.appendChild(
      createSvgElement('line', {
        x1: padL,
        y1: y.toFixed(1),
        x2: width - padR,
        y2: y.toFixed(1),
        stroke: yCfg.gridStroke || 'rgba(148,163,184,0.18)',
        'stroke-width': 1,
      })
    );

    const lblAttrs = {
      x: padL - (yCfg.labelOffset !== undefined ? Number(yCfg.labelOffset) : 5),
      y: (y + (yCfg.labelDy !== undefined ? Number(yCfg.labelDy) : 3)).toFixed(1),
      fill: '#94a3b8',
      'font-size': yCfg.fontSize || 10,
      'text-anchor': 'end',
    };
    if (yCfg.fontFamily) lblAttrs['font-family'] = yCfg.fontFamily;
    const lbl = createSvgElement('text', lblAttrs);
    lbl.textContent = formatY(val);
    svg.appendChild(lbl);
  }

  const maxCount = Math.max(
    1,
    ...seriesList.map((s) => (Array.isArray(s.points) ? s.points.length : 0))
  );
  const getX = (idx, count = maxCount) =>
    count <= 1 ? padL + plotW / 2 : padL + (plotW * idx) / (count - 1);
  const getY = (val) => padT + plotH * (1 - (Number(val) - minY) / ySpan);

  // Подписи по оси X
  const xCfg = options.xAxis || null;
  if (xCfg && Array.isArray(xCfg.indices) && xCfg.indices.length > 0) {
    const totalX = xCfg.totalCount || maxCount;
    xCfg.indices.forEach((idx, pos) => {
      const x = getX(idx, totalX);
      const anchor =
        xCfg.indices.length === 1
          ? 'middle'
          : pos === 0
            ? 'start'
            : pos === xCfg.indices.length - 1
              ? 'end'
              : 'middle';
      const lbl = createSvgElement('text', {
        x: x.toFixed(1),
        y: height - (xCfg.bottomOffset !== undefined ? Number(xCfg.bottomOffset) : 8),
        'text-anchor': anchor,
        fill: '#94a3b8',
        'font-size': xCfg.fontSize || 11,
      });
      lbl.textContent =
        typeof xCfg.formatLabel === 'function' ? xCfg.formatLabel(idx) : String(idx);
      svg.appendChild(lbl);
    });
  }

  // Линии и заливка серий
  const seriesCoords = [];
  seriesList.forEach((s, seriesIndex) => {
    const pts = Array.isArray(s.points) ? s.points : [];
    if (pts.length === 0) {
      seriesCoords.push([]);
      return;
    }

    const coords = pts.map((pt, idx) => ({
      x: getX(idx, pts.length),
      y: getY(pt.y),
      point: pt,
      seriesIndex,
      pointIndex: idx,
    }));
    seriesCoords.push(coords);

    if (coords.length >= 2) {
      const linePts = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
      if (s.fillArea) {
        const baselineY = padT + plotH;
        const areaPts = `${coords[0].x.toFixed(1)},${baselineY} ${linePts} ${coords[coords.length - 1].x.toFixed(1)},${baselineY}`;
        svg.appendChild(
          createSvgElement('polygon', {
            points: areaPts,
            fill: s.color || '#10b981',
            'fill-opacity': s.fillOpacity !== undefined ? s.fillOpacity : 0.14,
          })
        );
      }

      const polyAttrs = {
        fill: 'none',
        stroke: s.color || '#10b981',
        'stroke-width': s.strokeWidth !== undefined ? s.strokeWidth : 2,
        'stroke-linejoin': 'round',
        'stroke-linecap': 'round',
        points: linePts,
      };
      if (s.dasharray) {
        polyAttrs['stroke-dasharray'] = s.dasharray;
      }
      svg.appendChild(createSvgElement('polyline', polyAttrs));
    }
  });

  // Точки (отдельно поверх линий)
  seriesList.forEach((s, seriesIndex) => {
    const coords = seriesCoords[seriesIndex] || [];
    const radius =
      typeof s.pointRadius === 'function'
        ? s.pointRadius(coords.length)
        : s.pointRadius !== undefined
          ? s.pointRadius
          : coords.length > 35
            ? 2
            : 3;

    coords.forEach((c) => {
      const attrs = {
        cx: c.x.toFixed(1),
        cy: c.y.toFixed(1),
        r: radius,
        fill: s.color || '#10b981',
      };
      if (s.pointClass) attrs.class = s.pointClass;
      if (s.pointStroke) attrs.stroke = s.pointStroke;
      if (s.pointStrokeWidth !== undefined) attrs['stroke-width'] = s.pointStrokeWidth;

      const circle = createSvgElement('circle', attrs);
      if (s.pointDatasetIdx) {
        circle.dataset.idx = String(c.pointIndex);
      }
      if (typeof options.onPointElement === 'function') {
        options.onPointElement(circle, {
          point: c.point,
          series: s,
          seriesIndex,
          pointIndex: c.pointIndex,
          cx: c.x,
          cy: c.y,
          width,
          height,
        });
      }
      svg.appendChild(circle);
    });
  });

  return { rendered: true, width, height };
}

/**
 * Рендерит SVG-график дорожек активности событий по времени (полив, обдув и т.п.).
 */
export function renderSvgActivityChart(svgOrId, options = {}) {
  const svg = resolveSvg(svgOrId);
  if (!svg) return;
  svg.innerHTML = '';

  const width = Number(options.width) || 360;
  const height = Number(options.height) || 120;
  const padL = options.padL !== undefined ? Number(options.padL) : 62;
  const padR = options.padR !== undefined ? Number(options.padR) : 12;
  const plotW = width - padL - padR;
  const lanes = Array.isArray(options.lanes) ? options.lanes : [];

  const allTs = lanes
    .flatMap((l) => (Array.isArray(l.items) ? l.items : []))
    .map((item) => new Date(item.timestamp).getTime())
    .filter(Number.isFinite);

  lanes.forEach((lane) => {
    const lbl = createSvgElement('text', {
      x: padL - 8,
      y: lane.y + 4,
      fill: '#94a3b8',
      'font-size': 11,
      'text-anchor': 'end',
    });
    lbl.textContent = lane.label;
    svg.appendChild(lbl);
    svg.appendChild(
      createSvgElement('line', {
        x1: padL,
        y1: lane.y,
        x2: width - padR,
        y2: lane.y,
        stroke: 'rgba(148,163,184,0.2)',
        'stroke-width': 2,
      })
    );
  });

  if (allTs.length === 0) {
    const txt = createSvgElement('text', {
      x: padL + plotW / 2,
      y: height - 10,
      fill: '#94a3b8',
      'font-size': 11,
      'text-anchor': 'middle',
    });
    txt.textContent = options.emptyText || 'Нет срабатываний за выбранный период';
    svg.appendChild(txt);
    return;
  }

  const nowMs = Date.now();
  const spanMs = Number(options.spanMs) || 24 * 3600 * 1000;
  const minMs = Math.min(nowMs - spanMs, ...allTs);
  const maxMs = Math.max(nowMs, ...allTs);
  const denom = Math.max(1, maxMs - minMs);

  lanes.forEach((lane) => {
    (lane.items || []).forEach((ev) => {
      const ts = new Date(ev.timestamp).getTime();
      if (!Number.isFinite(ts)) return;
      const x = padL + ((ts - minMs) / denom) * plotW;
      const isOn = ev.state === 'on';
      const rect = createSvgElement('rect', {
        x: Math.max(padL, x - 3).toFixed(1),
        y: (lane.y - (isOn ? 11 : 5)).toFixed(1),
        width: 6,
        height: isOn ? 22 : 10,
        rx: 2,
        fill: isOn ? lane.color : 'rgba(148,163,184,0.55)',
      });
      if (typeof options.formatTooltip === 'function') {
        const tip = createSvgElement('title');
        tip.textContent = options.formatTooltip(lane, ev, isOn);
        rect.appendChild(tip);
      }
      svg.appendChild(rect);
    });
  });
}
