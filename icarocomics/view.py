"""Widget di visualizzazione della pagina: zoom, adattamento, pan, scorrimento con cambio pagina ai bordi."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PyQt6.QtWidgets import QWidget

from icarocomics.layout import ViewTarget, ZoomMode, compute_scale
from icarocomics.loader import PageImage

_BACKGROUND = QColor(28, 28, 30)
_WHEEL_STEP = 160           # pixel logici per "scatto" di rotellina
_TOUCHPAD_FLIP = 220        # overscroll accumulato (touchpad) necessario per girare pagina
_DRAG_THRESHOLD = 6


class PageView(QWidget):
    flip_requested = pyqtSignal(int)          # +1 avanti / -1 indietro in ordine di lettura
    zoom_step_requested = pyqtSignal(int, QPointF)
    clicked_zone = pyqtSignal(str)            # "left" | "center" | "right"
    double_clicked = pyqtSignal()
    viewport_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(False)
        self.setMinimumSize(200, 200)
        self.mode = ZoomMode.FIT_PAGE
        self.factor = 1.0
        self.manga = False
        self.osd_text = ""
        self.message = "Apri un fumetto (Ctrl+O) oppure trascinalo qui"
        self.loading = False
        self._page: PageImage | None = None
        self._scale = 1.0
        self._off = QPointF(0.0, 0.0)   # angolo in alto a sinistra della finestra, in coordinate pagina scalata
        self._press_pos: QPointF | None = None
        self._press_off = QPointF()
        self._dragging = False
        self._overscroll = 0.0

    # --- stato ----------------------------------------------------------------------
    @property
    def page(self) -> PageImage | None:
        return self._page

    @property
    def scale(self) -> float:
        return self._scale

    def target(self) -> ViewTarget:
        return ViewTarget(self.mode, self.factor, self.width(), self.height(), self.devicePixelRatioF())

    def set_page(self, page: PageImage | None, anchor: str = "start") -> None:
        self._page = page
        self.loading = False
        self._overscroll = 0.0
        self._relayout()
        if anchor == "start":
            self._off = QPointF(self._max_x() if self.manga else 0.0, 0.0)
        elif anchor == "end":
            self._off = QPointF(0.0 if self.manga else self._max_x(), self._max_y())
        self._clamp()
        self.update()

    def show_message(self, text: str) -> None:
        self._page = None
        self.loading = False
        self.message = text
        self.update()

    def set_loading(self, loading: bool) -> None:
        self.loading = loading
        self.update()

    def set_zoom(self, mode: ZoomMode, factor: float | None = None, anchor: QPointF | None = None) -> None:
        # Mantengo fermo il punto sotto il cursore (o il centro) durante lo zoom.
        pivot = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        old_scale = self._scale
        rel = None
        if self._page is not None and old_scale > 0:
            dest = self._dest_rect()
            rel = QPointF((pivot.x() - dest.x()) / old_scale, (pivot.y() - dest.y()) / old_scale)
        self.mode = mode
        if factor is not None:
            self.factor = factor
        self._relayout()
        if rel is not None:
            self._off = QPointF(rel.x() * self._scale - pivot.x(), rel.y() * self._scale - pivot.y())
        self._clamp()
        self.update()

    # --- geometria ------------------------------------------------------------------
    def _relayout(self) -> None:
        if self._page is None:
            return
        self._scale = compute_scale(self._page.width, self._page.height, self.target())

    def _display_size(self) -> tuple[float, float]:
        if self._page is None:
            return 0.0, 0.0
        return self._page.width * self._scale, self._page.height * self._scale

    def _max_x(self) -> float:
        return max(0.0, self._display_size()[0] - self.width())

    def _max_y(self) -> float:
        return max(0.0, self._display_size()[1] - self.height())

    def _clamp(self) -> None:
        self._off = QPointF(min(max(0.0, self._off.x()), self._max_x()),
                            min(max(0.0, self._off.y()), self._max_y()))

    def _dest_rect(self) -> QRectF:
        dw, dh = self._display_size()
        # Pagina più piccola della finestra: centrata; altrimenti traslata dall'offset di scorrimento.
        x = (self.width() - dw) / 2 if dw <= self.width() else -self._off.x()
        y = (self.height() - dh) / 2 if dh <= self.height() else -self._off.y()
        return QRectF(x, y, dw, dh)

    def at_top(self) -> bool:
        return self._off.y() <= 0.5

    def at_bottom(self) -> bool:
        return self._off.y() >= self._max_y() - 0.5

    def scroll_by(self, dx: float, dy: float) -> bool:
        """Scorre e restituisce True se la vista si è effettivamente mossa."""
        before = QPointF(self._off)
        self._off = QPointF(self._off.x() + dx, self._off.y() + dy)
        self._clamp()
        moved = (self._off - before).manhattanLength() > 0.01
        if moved:
            self.update()
        return moved

    def scroll_or_flip(self, dy: float) -> None:
        if not self.scroll_by(0.0, dy):
            self.flip_requested.emit(1 if dy > 0 else -1)

    # --- disegno --------------------------------------------------------------------
    def _scaled_image(self) -> QImage | None:
        """Versione ridotta coerente con la scala attuale: quella del worker se combacia,
        altrimenti la ricalcolo qui (succede solo dopo un cambio di zoom/finestra)."""
        page = self._page
        if page is None:
            return None
        dpr = self.devicePixelRatioF()
        phys = self._scale * dpr
        if phys >= 0.999:
            return None
        want = (max(1, round(page.width * phys)), max(1, round(page.height * phys)))
        if page.scaled is None or page.scaled_for != want:
            img = page.image.scaled(want[0], want[1], Qt.AspectRatioMode.IgnoreAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            img = img.convertToFormat(QImage.Format.Format_RGB32)
            img.setDevicePixelRatio(dpr)
            page.scaled, page.scaled_for = img, want
        return page.scaled

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), _BACKGROUND)
        if self._page is None:
            p.setPen(QColor(170, 170, 175))
            f = QFont(self.font())
            f.setPointSizeF(f.pointSizeF() * 1.3)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        else:
            dest = self._dest_rect()
            scaled = self._scaled_image()
            if scaled is not None:
                p.drawImage(dest.topLeft(), scaled)
            else:
                # Ingrandimento: disegno solo la porzione visibile, così anche al 400% non
                # alloco mai un'immagine gigante.
                visible = dest.intersected(QRectF(event.rect()))
                if not visible.isEmpty():
                    s = self._scale
                    src = QRectF((visible.x() - dest.x()) / s, (visible.y() - dest.y()) / s,
                                 visible.width() / s, visible.height() / s)
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    p.drawImage(visible, self._page.image, src)
        self._paint_badges(p)
        p.end()

    def _paint_badges(self, p: QPainter) -> None:
        texts = []
        if self.loading:
            texts.append("Caricamento…")
        if self.osd_text:
            texts.append(self.osd_text)
        if not texts:
            return
        text = "   ".join(texts)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 20
        h = fm.height() + 10
        r = QRectF(self.width() - w - 12, self.height() - h - 12, w, h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(235, 235, 235))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    # --- eventi ---------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
        self._clamp()
        self.viewport_changed.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        mods = event.modifiers()
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        if mods & Qt.KeyboardModifier.ControlModifier:
            if angle.y():
                self.zoom_step_requested.emit(1 if angle.y() > 0 else -1, event.position())
            event.accept()
            return
        touchpad = not pixel.isNull()
        dx = -(pixel.x() if touchpad else angle.x() / 120 * _WHEEL_STEP)
        dy = -(pixel.y() if touchpad else angle.y() / 120 * _WHEEL_STEP)
        if mods & Qt.KeyboardModifier.ShiftModifier:
            dx, dy = dy, 0.0
        if dx:
            self.scroll_by(dx, 0.0)
        if dy:
            if self.scroll_by(0.0, dy):
                self._overscroll = 0.0
            elif touchpad:
                # Con il touchpad arrivano decine di piccoli eventi: giro pagina solo dopo
                # un "tiro" deciso oltre il bordo, non per inerzia.
                self._overscroll += dy
                if abs(self._overscroll) >= _TOUCHPAD_FLIP:
                    self._overscroll = 0.0
                    self.flip_requested.emit(1 if dy > 0 else -1)
            else:
                self.flip_requested.emit(1 if dy > 0 else -1)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            self._press_off = QPointF(self._off)
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_pos is None:
            return
        delta = event.position() - self._press_pos
        if not self._dragging and delta.manhattanLength() > _DRAG_THRESHOLD:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self._off = self._press_off - delta
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        if not self._dragging:
            self.clicked_zone.emit(self._zone(event.position().x()))
        self.unsetCursor()
        self._press_pos = None
        self._dragging = False

    def _zone(self, x: float) -> str:
        third = self.width() / 3
        return "left" if x < third else "right" if x > 2 * third else "center"

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Il primo clic è già stato consegnato come clic singolo: sui lati il secondo clic
        # vale come un ulteriore cambio pagina, al centro attiva lo schermo intero.
        zone = self._zone(event.position().x())
        if zone == "center":
            self.double_clicked.emit()
        else:
            self.clicked_zone.emit(zone)
