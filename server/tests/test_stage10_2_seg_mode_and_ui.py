import io
import json
import re
import shutil
import subprocess
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.api import edge as edge_api
from server.api.deps import create_session_cookie, get_db
from server.config import get_settings
from server.core.live_manager import live_manager
from server.core.state import app_state
from server.db.database import Base
from server.db.models import Camera, EdgeCommand, Installation, User
from server.main import app

EDGE_KEY = "test-edge-secret-key"


def _make_jpeg() -> bytes:
    img = Image.new("RGB", (32, 32), color=(90, 170, 80))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_stage10_2.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(edge_api, "get_media_dir", lambda: media_dir)

    settings = get_settings()
    monkeypatch.setattr(settings, "edge_api_key", EDGE_KEY)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app_state.clear()
    live_manager.clear()

    yield session_factory

    app.dependency_overrides.clear()
    app_state.clear()
    live_manager.clear()
    engine.dispose()


@pytest.fixture
def client(test_env) -> TestClient:
    return TestClient(app)


def _create_dev_cookie(session_factory) -> dict[str, str]:
    db: Session = session_factory()
    now = datetime.now(UTC)
    user = User(
        client_type="web",
        external_id="u10-2-dev",
        login="Dev 10.2",
        role="developer",
        is_active=True,
        authorized_at=now,
        last_active_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    cookie_val = create_session_cookie(user)
    db.close()
    return {"gv_session": cookie_val}


def test_segmentation_mode_sync_across_controls_live_and_edge(
    client: TestClient, test_env
):
    """Параметр segmentation_mode ('auto'/'onnx'/'hsv') синхронизируется через controls, live, heartbeat и camera-settings."""
    session_factory = test_env
    cookies = _create_dev_cookie(session_factory)
    edge_headers = {"X-Edge-Key": EDGE_KEY}

    with session_factory() as db:
        inst = Installation(
            name="Бокс 10.2",
            culture_name="Салат",
            light_gpio_pin=4,
            flash_gpio_pin=5,
            is_archived=False,
        )
        db.add(inst)
        db.flush()
        cam = Camera(
            hardware_id="cam-seg-10-2",
            auto_name="Cam 10.2",
            installation_id=inst.id,
            is_online=True,
        )
        db.add(cam)
        db.commit()
        cam_id = cam.id

    hb_payload = {
        "software": {
            "edge_version": "0.10.2",
            "python_version": "3.12",
            "model_name": "plant_seg.onnx",
        },
        "hardware": {
            "board_model": "Raspberry Pi 5",
            "os_info": "Linux",
            "uptime_sec": 100,
            "cpu_temp_c": 42.0,
            "cpu_usage_percent": 15.0,
            "ram_used_mb": 512,
            "ram_total_mb": 4096,
            "disk_free_gb": 20.0,
            "gpio_mode": "mock",
            "mock_cameras": True,
        },
        "offline_queue_count": 0,
        "detected_cameras": [{"hardware_id": "cam-seg-10-2", "auto_name": "Cam 10.2"}],
    }

    # 0. Фиксируем онлайн-статус Edge-агента через heartbeat (по умолчанию 'auto')
    r_hb_init = client.post(
        "/api/edge/heartbeat",
        headers=edge_headers,
        json=hb_payload,
    )
    assert r_hb_init.status_code == 200
    assert r_hb_init.json()["active_installations"][0]["camera_segmentation_modes"] == {
        "cam-seg-10-2": "auto"
    }

    # 1. По умолчанию segmentation_mode == 'auto'
    r_ctrl_init = client.get(f"/api/cameras/{cam_id}/controls", cookies=cookies)
    assert r_ctrl_init.status_code == 200
    assert r_ctrl_init.json()["segmentation_mode"] == "auto"

    r_status_init = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies)
    assert r_status_init.status_code == 200
    assert r_status_init.json()["segmentation_mode"] == "auto"

    # 2. Запуск Live и переключение через PATCH /controls на 'onnx'
    r_start = client.post(f"/api/cameras/{cam_id}/live/start", cookies=cookies)
    assert r_start.status_code == 200
    assert r_start.json()["segmentation_mode"] == "auto"

    r_patch = client.patch(
        f"/api/cameras/{cam_id}/controls",
        cookies=cookies,
        json={"segmentation_mode": "onnx"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["segmentation_mode"] == "onnx"

    # Живая сессия, статус и live-frame от Edge-агента получают 'onnx'
    r_status_onnx = client.get(f"/api/cameras/{cam_id}/live/status", cookies=cookies)
    assert r_status_onnx.status_code == 200
    assert r_status_onnx.json()["segmentation_mode"] == "onnx"

    r_live_frame = client.post(
        "/api/edge/cameras/cam-seg-10-2/live-frame",
        headers={
            **edge_headers,
            "X-Resolution": "640x480",
            "X-Frame-Mode": "annotated",
        },
        content=_make_jpeg(),
    )
    assert r_live_frame.status_code == 200
    assert r_live_frame.json()["segmentation_mode"] == "onnx"

    # 3. Heartbeat и /api/edge/camera-settings отдают 'onnx'
    r_hb = client.post(
        "/api/edge/heartbeat",
        headers=edge_headers,
        json=hb_payload,
    )
    assert r_hb.status_code == 200
    assert r_hb.json()["active_installations"][0]["camera_segmentation_modes"] == {
        "cam-seg-10-2": "onnx"
    }

    r_cfg = client.get(
        "/api/edge/camera-settings?hardware_id=cam-seg-10-2", headers=edge_headers
    )
    assert r_cfg.status_code == 200
    assert r_cfg.json()["segmentation_mode"] == "onnx"

    # 4. Сохранение через /controls/save с 'hsv' и проверка команды в БД
    r_save = client.post(
        f"/api/cameras/{cam_id}/controls/save",
        cookies=cookies,
        json={"scope": "next_frame", "segmentation_mode": "hsv"},
    )
    assert r_save.status_code == 200
    assert r_save.json()["segmentation_mode"] == "hsv"

    with session_factory() as db:
        last_cmd = (
            db.query(EdgeCommand)
            .filter(EdgeCommand.command_type == "SET_CAMERA_CONTROLS")
            .order_by(EdgeCommand.id.desc())
            .first()
        )
        assert last_cmd is not None
        assert json.loads(last_cmd.payload)["segmentation_mode"] == "hsv"

    # 5. Невалидное значение segmentation_mode отклоняется с 422
    r_bad = client.patch(
        f"/api/cameras/{cam_id}/controls",
        cookies=cookies,
        json={"segmentation_mode": "unknown_engine"},
    )
    assert r_bad.status_code == 422


def test_web_ui_stage10_2_seg_mode_live_polish_and_gallery_pause():
    """Проверяет UI-полировку этапа 10.2: Авто/ONNX/HSV, без обрезаемой тени, Скрыть по клику вне, Пауза и короткие подписи."""
    web_dir = Path(__file__).resolve().parents[2] / "clients" / "web"
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    css = (web_dir / "style.css").read_text(encoding="utf-8")
    cameras_js = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(
            [web_dir / "js" / "cameras.js", *(web_dir / "js" / "cameras").glob("*.js")]
        )
    )
    gallery_js = (web_dir / "js" / "gallery.js").read_text(encoding="utf-8")

    # 1. Segmented control Авто / ONNX / HSV в Live-окне
    assert 'id="live-seg-mode-control"' in html
    assert 'data-seg-mode="auto"' in html and ">Авто<" in html
    assert 'data-seg-mode="onnx"' in html and ">ONNX<" in html
    assert 'data-seg-mode="hsv"' in html and ">HSV<" in html
    assert "switchSegmentationMode" in cameras_js
    assert "currentSegmentationMode" in cameras_js

    # 2. У оверлейных плашек настроек в Live убрана обрезаемая box-shadow
    pill_match = re.search(r"\.live-ctrl-pill\s*\{([^}]*)\}", css, re.DOTALL)
    assert pill_match is not None
    assert "box-shadow: none" in pill_match.group(1)

    # 3. Плашка автоцветокоррекции: кнопка 'Скрыть' вместо 'Отмена' и закрытие по клику вне плашки
    assert 'id="btn-live-auto-color-cancel"' in html
    assert ">Скрыть<" in html
    assert (
        'id="btn-live-auto-color-cancel" class="btn btn-secondary btn-compact">Отмена<'
        not in html
    )
    assert "hideAutoColorPreview()" in cameras_js
    assert "previewPanel.contains(" in cameras_js

    # 4. Галерея: короткие сообщения без переносов строк и фикс паузы
    assert "Минимум 1 с" in gallery_js
    assert "Макс. 30 FPS" in gallery_js
    assert "Длительность должна быть целым числом не меньше 1 с" not in gallery_js
    assert "При экспорте FPS будет ограничен до 30" not in gallery_js
    assert "white-space: nowrap;" in css
    assert "playbackSessionId" in gallery_js
    assert "cancelActivePreloads" in gallery_js

    # 5. Функциональная проверка паузы и коротких сообщений в gallery.js через node
    if shutil.which("node"):
        node_script = f"""
        const elements = {{}};
        function makeEl(id) {{
          return {{
            id,
            classList: {{
              _set: new Set(),
              add(c) {{ this._set.add(c); }},
              remove(c) {{ this._set.delete(c); }},
              toggle(c, force) {{
                if (force === undefined) {{
                  if (this._set.has(c)) this._set.delete(c); else this._set.add(c);
                }} else if (force) {{
                  this._set.add(c);
                }} else {{
                  this._set.delete(c);
                }}
              }},
              contains(c) {{ return this._set.has(c); }},
            }},
            dataset: {{}},
            style: {{}},
            listeners: {{}},
            children: [],
            value: '3',
            min: '0',
            max: '0',
            step: '1',
            disabled: false,
            textContent: '',
            innerHTML: '',
            title: '',
            attrs: {{}},
            setAttribute(k, v) {{ this.attrs[k] = String(v); }},
            getAttribute(k) {{ return this.attrs[k] ?? null; }},
            addEventListener(ev, fn) {{
              this.listeners[ev] = this.listeners[ev] || [];
              this.listeners[ev].push(fn);
            }},
            async trigger(ev, eventObj = {{}}) {{
              for (const fn of (this.listeners[ev] || [])) {{
                await fn(eventObj);
              }}
            }},
            appendChild(ch) {{ ch.parentElement = this; this.children.push(ch); }},
            querySelectorAll() {{ return []; }},
            querySelector() {{ return null; }},
          }};
        }}
        const ids = [
          'modal-gallery', 'gallery-title', 'gallery-layer-container', 'gallery-image-box',
          'gallery-meta', 'gallery-btn-camera-switch', 'gallery-camera-label',
          'gallery-duration-input', 'gallery-fps-value', 'gallery-fps-warning',
          'gallery-duration-error', 'gallery-btn-download', 'gallery-btn-chart',
          'gallery-chart-panel', 'gallery-slider', 'gallery-btn-first', 'gallery-btn-prev',
          'gallery-btn-play', 'gallery-btn-next', 'gallery-btn-last', 'modal-gallery-close',
          'gallery-layer-raw', 'gallery-layer-ann', 'gallery-chart-mode-control'
        ];
        ids.forEach((id) => {{ elements[id] = makeEl(id); }});
        globalThis.document = {{
          getElementById: (id) => elements[id] || null,
          createElement: () => makeEl('dyn'),
          body: {{ classList: makeEl('body').classList }},
          querySelectorAll: () => [],
        }};
        globalThis.window = {{ addEventListener: () => {{}} }};
        globalThis.Image = class {{
          constructor() {{ this.dataset = {{}}; this.naturalWidth = 10; }}
          set src(v) {{
            this._src = v;
            this.complete = Boolean(v);
            if (v && this.onload) {{
              setTimeout(() => {{ if (this._src && this.onload) this.onload(); }}, 20);
            }}
          }}
          get src() {{ return this._src || ''; }}
          decode() {{ return Promise.resolve(); }}
        }};
        const frameList = Array.from({{ length: 40 }}, (_, i) => ({{
          captured_at: `2026-11-25T10:${{String(i).padStart(2, '0')}}:00Z`,
          raw_url: `/media/f${{i}}.jpg`,
          area_display: '100 px',
        }}));
        globalThis.fetch = async () => ({{
          ok: true,
          status: 200,
          headers: {{ get: () => 'application/json' }},
          json: async () => ({{ frames: frameList }}),
        }});

        const mod = await import({json.dumps(str(web_dir / "js" / "gallery.js"))});
        mod.initGallery();
        await mod.openGalleryModal({{ id: 1, name: 'Test Box' }});

        // Проверяем короткие подписи ошибок и FPS
        elements['gallery-duration-input'].value = '0';
        await elements['gallery-duration-input'].trigger('input');
        if (elements['gallery-duration-error'].textContent !== 'Минимум 1 с') {{
          throw new Error('Expected Минимум 1 с, got: ' + elements['gallery-duration-error'].textContent);
        }}
        elements['gallery-duration-input'].value = '1';
        await elements['gallery-duration-input'].trigger('input');
        if (elements['gallery-fps-warning'].textContent !== 'Макс. 30 FPS') {{
          throw new Error('Expected Макс. 30 FPS, got: ' + elements['gallery-fps-warning'].textContent);
        }}

        // Запускаем Play, затем сразу нажимаем Pause и ждём 120 мс — кадр не должен продвинуться
        await elements['gallery-btn-play'].trigger('click');
        if (elements['gallery-btn-play'].dataset.playState !== 'playing') {{
          throw new Error('Play did not enter playing state');
        }}
        const frameAtPauseClick = elements['gallery-slider'].value;
        await elements['gallery-btn-play'].trigger('click');
        if (elements['gallery-btn-play'].dataset.playState !== 'paused') {{
          throw new Error('Pause did not immediately switch to paused state');
        }}
        await new Promise((r) => setTimeout(r, 120));
        if (elements['gallery-slider'].value !== frameAtPauseClick) {{
          throw new Error('Frame advanced after pause: ' + frameAtPauseClick + ' -> ' + elements['gallery-slider'].value);
        }}
        """
        res = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
