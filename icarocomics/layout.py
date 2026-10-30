"""Calcolo della scala di visualizzazione, condiviso tra vista (thread GUI) e worker di decodifica."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ZoomMode(str, Enum):
    FIT_PAGE = "fit_page"
    FIT_WIDTH = "fit_width"
    FIT_HEIGHT = "fit_height"
    CUSTOM = "custom"


ZOOM_MIN = 0.05
ZOOM_MAX = 8.0


@dataclass(frozen=True)
class ViewTarget:
    """Fotografia della vista al momento della richiesta: permette al worker di produrre
    già la versione ridimensionata giusta, così il cambio pagina è istantaneo."""

    mode: ZoomMode
    factor: float
    viewport_w: int
    viewport_h: int
    dpr: float


def compute_scale(img_w: int, img_h: int, target: ViewTarget) -> float:
    if img_w <= 0 or img_h <= 0 or target.viewport_w <= 0 or target.viewport_h <= 0:
        return 1.0
    sx = target.viewport_w / img_w
    sy = target.viewport_h / img_h
    if target.mode is ZoomMode.FIT_WIDTH:
        scale = sx
    elif target.mode is ZoomMode.FIT_HEIGHT:
        scale = sy
    elif target.mode is ZoomMode.FIT_PAGE:
        scale = min(sx, sy)
    else:
        scale = target.factor
    return min(ZOOM_MAX, max(ZOOM_MIN, scale))
