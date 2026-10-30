"""Icone vettoriali della toolbar, colorate con il colore del testo della palette corrente
(così restano leggibili sia con il tema chiaro sia con quello scuro di GNOME)."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

_STROKE = 'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'

_SHAPES = {
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" {s}/>',
    "first": '<path d="M6 5v14M18 6l-7 6 7 6" {s}/>',
    "prev": '<path d="M15 5l-7 7 7 7" {s}/>',
    "next": '<path d="M9 5l7 7-7 7" {s}/>',
    "last": '<path d="M18 5v14M6 6l7 6-7 6" {s}/>',
    "zoom-in": '<circle cx="10.5" cy="10.5" r="6" {s}/><path d="M15 15l5 5M8 10.5h5M10.5 8v5" {s}/>',
    "zoom-out": '<circle cx="10.5" cy="10.5" r="6" {s}/><path d="M15 15l5 5M8 10.5h5" {s}/>',
    "zoom-1": '<rect x="3" y="4" width="18" height="16" rx="2" {s}/>'
              '<path d="M8 9.5l1.5-1v7M15 9.5l1.5-1v7M12 11v.01M12 14v.01" {s}/>',
    "fit-width": '<rect x="4" y="3" width="16" height="18" rx="1.5" {s}/><path d="M7 12h10M9 10l-2 2 2 2M15 10l2 2-2 2" {s}/>',
    "fit-height": '<rect x="5" y="3" width="14" height="18" rx="1.5" {s}/><path d="M12 6v12M10 8l2-2 2 2M10 16l2 2 2-2" {s}/>',
    "fit-page": '<rect x="5" y="3" width="14" height="18" rx="1.5" {s}/><path d="M9 7H8v2M15 7h1v2M9 17H8v-2M15 17h1v-2" {s}/>',
    "manga": '<path d="M12 6c-2-1.5-5-2-8-1.5v14c3-.5 6 0 8 1.5 2-1.5 5-2 8-1.5v-14c-3-.5-6 0-8 1.5zM12 6v14" {s}/>'
             '<path d="M17 10h-3.5M15 8.5L13.5 10l1.5 1.5" {s}/>',
    "enhance": '<path d="M4 20L15 9M13 7l2-2 4 4-2 2z" {s}/>'
               '<path d="M6 4v3M4.5 5.5h3M19 14v3M17.5 15.5h3M10 3v2M9 4h2" {s}/>',
    "fullscreen": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" {s}/>',
    "goto": '<path d="M5 4h10l4 4v12H5z" {s}/><path d="M9 13h6M13 11l2 2-2 2" {s}/>',
}


def _render(shape: str, color: QColor, size: int, dpr: float) -> QPixmap:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        + _SHAPES[shape].format(s=_STROKE.format(c=color.name()))
        + "</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(shape: str) -> QIcon:
    app = QApplication.instance()
    pal = app.palette() if app else QPalette()
    dpr = app.devicePixelRatio() if app else 1.0
    normal = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
    disabled = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
    highlight = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
    ic = QIcon()
    for size in (16, 24, 32):
        ic.addPixmap(_render(shape, normal, size, dpr), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(_render(shape, highlight, size, dpr), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(_render(shape, disabled, size, dpr), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic
