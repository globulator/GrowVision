"""Pydantic-схемы запросов для API камер."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from shared.schemas import SegmentationMode


class CameraBindRequest(BaseModel):
    """Тело запроса POST /api/cameras/{camera_id}/bind."""

    model_config = ConfigDict(extra="ignore")

    installation_id: int | None = None


class CameraControlsPatch(BaseModel):
    """Тело запроса PATCH /api/cameras/{camera_id}/controls."""

    model_config = ConfigDict(extra="ignore")

    controls: dict[str, Any] | None = None
    format: str | None = None
    resolution: str | None = None
    rgb: dict[str, float] | None = None
    segmentation_mode: SegmentationMode | None = None


class CameraControlsSaveRequest(BaseModel):
    """Тело запроса POST /api/cameras/{camera_id}/controls/save."""

    model_config = ConfigDict(extra="ignore")

    scope: Literal["next_frame", "all_gallery"]
    controls: dict[str, Any] | None = None
    format: str | None = None
    resolution: str | None = None
    rgb: dict[str, float] | None = None
    segmentation_mode: SegmentationMode | None = None


class LiveModeRequest(BaseModel):
    """Тело запроса POST /api/cameras/{camera_id}/live/mode."""

    model_config = ConfigDict(extra="ignore")

    mode: Literal["raw", "annotated"]


class AutoColorCalibrateRequest(BaseModel):
    """Параметры запроса автоцветокоррекции (предпросмотр без применения к профилю)."""

    preview: bool = False


__all__ = [
    "AutoColorCalibrateRequest",
    "CameraBindRequest",
    "CameraControlsPatch",
    "CameraControlsSaveRequest",
    "LiveModeRequest",
]
