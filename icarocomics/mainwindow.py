"""Finestra principale di IcaroComics."""

from __future__ import annotations

import hashlib
import os
import time

from PyQt6.QtCore import QPointF, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox, QSizePolicy, QSlider, QSpinBox, QToolBar,
)

from icarocomics import APP_NAME, __version__
from icarocomics.archive import ArchiveError, open_archive
from icarocomics.icons import icon
from icarocomics.layout import ZOOM_MAX, ZOOM_MIN, ZoomMode
from icarocomics.loader import PageImage, PageLoader
from icarocomics.view import PageView

_ZOOM_STEP = 1.25
_MAX_RECENT = 12
_FILE_FILTER = "Fumetti (*.cbz *.cbr *.cb7 *.zip *.rar *.7z);;Tutti i file (*)"

_HELP = """
<h3>Scorciatoie</h3>
<table cellspacing="4">
<tr><td><b>→ / ←</b></td><td>pagina successiva / precedente (invertite in modalità Manga)</td></tr>
<tr><td><b>↓ / ↑</b>, rotellina</td><td>scorre la pagina; al bordo passa alla pagina seguente / precedente</td></tr>
<tr><td><b>Spazio / PagGiù</b></td><td>scorre di una schermata, poi pagina successiva</td></tr>
<tr><td><b>Maiusc+Spazio / PagSu</b></td><td>scorre indietro di una schermata, poi pagina precedente</td></tr>
<tr><td><b>Home / Fine</b></td><td>prima / ultima pagina</td></tr>
<tr><td><b>G</b></td><td>vai alla pagina…</td></tr>
<tr><td><b>+ / −</b>, Ctrl+rotellina</td><td>zoom avanti / indietro</td></tr>
<tr><td><b>1</b></td><td>dimensione reale (100%)</td></tr>
<tr><td><b>W / H / B</b></td><td>adatta alla larghezza / altezza / pagina intera</td></tr>
<tr><td><b>M</b></td><td>modalità Manga (lettura da destra a sinistra)</td></tr>
<tr><td><b>E</b></td><td>auto-miglioramento dell'immagine</td></tr>
<tr><td><b>F / F11</b>, doppio clic al centro</td><td>schermo intero (Esc per uscire)</td></tr>
<tr><td><b>Clic</b> sul terzo sinistro / destro</td><td>pagina a sinistra / a destra</td></tr>
<tr><td><b>Trascinamento</b></td><td>sposta la pagina ingrandita</td></tr>
</table>
"""


def _file_key(path: str) -> str:
    return hashlib.sha1(os.path.abspath(path).encode("utf-8", "surrogateescape")).hexdigest()[:20]


class MainWindow(QMainWindow):
    # Emesso quando una pagina richiesta diventa visibile: usato da statistiche e self-test.
    page_shown = pyqtSignal(int, bool, float)

    def __init__(self, loader_threads: int = 2):
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.loader = PageLoader(threads=loader_threads, parent=self)
        self.loader.page_ready.connect(self._on_page_ready)
        self.loader.page_failed.connect(self._on_page_failed)

        self.path: str | None = None
        self.index = 0
        self.enhanced = bool(self.settings.value("enhance", False, bool))
        self.manga_default = bool(self.settings.value("manga", False, bool))
        self._anchor = "start"
        self._requested_at = 0.0
        self._last_step = 1
        self._shown_key: tuple[int, bool] | None = None

        self.view = PageView(self)
        self.view.mode = ZoomMode(self.settings.value("zoom_mode", ZoomMode.FIT_PAGE.value))
        self.view.factor = float(self.settings.value("zoom_factor", 1.0))
        self.view.manga = self.manga_default
        self.setCentralWidget(self.view)
        self.view.flip_requested.connect(self._on_flip)
        self.view.zoom_step_requested.connect(self._on_zoom_step)
        self.view.clicked_zone.connect(self._on_click_zone)
        self.view.double_clicked.connect(self.toggle_fullscreen)
        self.view.viewport_changed.connect(self._on_viewport_changed)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._sync_ui()

        self.setAcceptDrops(True)
        self.setWindowTitle(APP_NAME)
        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1100, 1000)

    # ================================================================== costruzione UI
    def _action(self, text: str, slot, shortcuts=(), icon_name: str | None = None,
                checkable: bool = False, tip: str | None = None) -> QAction:
        act = QAction(text, self)
        if icon_name:
            act.setIcon(icon(icon_name))
        if shortcuts:
            act.setShortcuts([QKeySequence(s) for s in shortcuts])
        act.setCheckable(checkable)
        if tip or shortcuts:
            hint = f" ({QKeySequence(shortcuts[0]).toString(QKeySequence.SequenceFormat.NativeText)})" if shortcuts else ""
            act.setToolTip((tip or text.replace("&", "").rstrip("…")) + hint)
        (act.toggled if checkable else act.triggered).connect(slot)
        # Le azioni aggiunte alla finestra restano attive anche a schermo intero, quando
        # barra dei menu e toolbar sono nascoste.
        self.addAction(act)
        return act

    def _build_actions(self) -> None:
        A = self._action
        self.act_open = A("&Apri…", self.open_dialog, [QKeySequence.StandardKey.Open], "open")
        self.act_close = A("&Chiudi fumetto", self.close_document, ["Ctrl+W"])
        self.act_quit = A("&Esci", self.close, ["Ctrl+Q"])

        self.act_first = A("Prima pagina", lambda: self.go_to(0), ["Home"], "first")
        self.act_last = A("Ultima pagina", lambda: self._go_last(), ["End"], "last")
        self.act_left = A("Pagina a sinistra", self.nav_left, ["Left"], "prev")
        self.act_right = A("Pagina a destra", self.nav_right, ["Right"], "next")
        self.act_next = A("Pagina successiva", lambda: self.step(1), ["N"])
        self.act_prev = A("Pagina precedente", lambda: self.step(-1), ["Backspace", "P"])
        self.act_down = A("Scorri giù", lambda: self.view.scroll_or_flip(self._line_step()), ["Down"])
        self.act_up = A("Scorri su", lambda: self.view.scroll_or_flip(-self._line_step()), ["Up"])
        self.act_pgdn = A("Schermata successiva", lambda: self.view.scroll_or_flip(self._page_step()),
                          ["PgDown", "Space"])
        self.act_pgup = A("Schermata precedente", lambda: self.view.scroll_or_flip(-self._page_step()),
                          ["PgUp", "Shift+Space"])
        self.act_goto = A("&Vai alla pagina…", self.goto_dialog, ["G", "Ctrl+G"], "goto")

        self.act_zoom_in = A("Zoom avanti", lambda: self._zoom_by(_ZOOM_STEP), ["+", "=", "Ctrl++"], "zoom-in")
        self.act_zoom_out = A("Zoom indietro", lambda: self._zoom_by(1 / _ZOOM_STEP), ["-", "Ctrl+-"], "zoom-out")
        self.act_zoom_1 = A("Dimensione reale (100%)", lambda: self._set_zoom(ZoomMode.CUSTOM, 1.0), ["1", "Ctrl+0"], "zoom-1")
        self.act_fit_w = A("Adatta alla larghezza", lambda c: c and self._set_zoom(ZoomMode.FIT_WIDTH), ["W"], "fit-width", True)
        self.act_fit_h = A("Adatta all'altezza", lambda c: c and self._set_zoom(ZoomMode.FIT_HEIGHT), ["H"], "fit-height", True)
        self.act_fit_p = A("Adatta alla pagina", lambda c: c and self._set_zoom(ZoomMode.FIT_PAGE), ["B"], "fit-page", True)
        self.fit_group = QActionGroup(self)
        self.fit_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        for act in (self.act_fit_w, self.act_fit_h, self.act_fit_p):
            self.fit_group.addAction(act)

        self.act_manga = A("Modalità &Manga (destra → sinistra)", self.set_manga, ["M"], "manga", True)
        self.act_enhance = A("Auto-&miglioramento immagine", self.set_enhanced, ["E"], "enhance", True,
                             tip="Auto-miglioramento: livelli, bilanciamento, gamma e contrasto locale")
        self.act_fullscreen = A("&Schermo intero", self.toggle_fullscreen, ["F11", "F"], "fullscreen", True)
        self.act_escape = A("Esci da schermo intero", self._escape, ["Esc"])
        self.act_help = A("&Scorciatoie da tastiera", self.show_help, ["F1"])
        self.act_about = A("&Informazioni su IcaroComics", self.show_about)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.act_open)
        self.recent_menu = m.addMenu("File &recenti")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addAction(self.act_close)
        m.addSeparator()
        m.addAction(self.act_quit)

        m = mb.addMenu("&Vai")
        for act in (self.act_next, self.act_prev, self.act_first, self.act_last, self.act_goto):
            m.addAction(act)

        m = mb.addMenu("V&ista")
        for act in (self.act_zoom_in, self.act_zoom_out, self.act_zoom_1):
            m.addAction(act)
        m.addSeparator()
        for act in (self.act_fit_w, self.act_fit_h, self.act_fit_p):
            m.addAction(act)
        m.addSeparator()
        m.addAction(self.act_fullscreen)

        m = mb.addMenu("&Lettura")
        m.addAction(self.act_manga)
        m.addAction(self.act_enhance)

        m = mb.addMenu("&Aiuto")
        m.addAction(self.act_help)
        m.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Barra principale", self)
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbar = tb

        tb.addAction(self.act_open)
        tb.addSeparator()
        tb.addAction(self.act_first)
        tb.addAction(self.act_left)

        self.page_spin = QSpinBox(self)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.page_spin.setMinimum(1)
        self.page_spin.setToolTip("Pagina corrente (G per saltare)")
        self.page_spin.valueChanged.connect(self._on_spin_changed)
        tb.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0 ", self)
        tb.addWidget(self.page_total)

        tb.addAction(self.act_right)
        tb.addAction(self.act_last)

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setMinimum(0)
        self.slider.setMinimumWidth(140)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.slider.sliderReleased.connect(lambda: self.go_to(self.slider.value()))
        tb.addWidget(self.slider)

        tb.addSeparator()
        for act in (self.act_zoom_out, self.act_zoom_in, self.act_zoom_1,
                    self.act_fit_w, self.act_fit_h, self.act_fit_p):
            tb.addAction(act)
        tb.addSeparator()
        tb.addAction(self.act_manga)
        tb.addAction(self.act_enhance)
        tb.addSeparator()
        tb.addAction(self.act_fullscreen)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.status_info = QLabel(self)
        self.status_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sb.addWidget(self.status_info, 1)
        self.status_flags = QLabel(self)
        sb.addPermanentWidget(self.status_flags)
        self.status_zoom = QLabel(self)
        self.status_zoom.setMinimumWidth(60)
        sb.addPermanentWidget(self.status_zoom)
        self.status_page = QLabel(self)
        sb.addPermanentWidget(self.status_page)

    # ================================================================== documento
    @property
    def page_count(self) -> int:
        return len(self.loader.archive) if self.loader.archive is not None else 0

    def open_dialog(self) -> None:
        start = os.path.dirname(self.path) if self.path else self.settings.value("last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Apri fumetto", start, _FILE_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str, page: int | None = None) -> bool:
        path = os.path.abspath(path)
        self._save_progress()
        try:
            archive = open_archive(path)
        except (ArchiveError, OSError) as exc:
            QMessageBox.critical(self, APP_NAME, f"Impossibile aprire il fumetto:\n{path}\n\n{exc}")
            return False

        self.loader.set_archive(archive)
        self.path = path
        self._shown_key = None
        self.view.show_message("Caricamento…")
        key = _file_key(path)
        self.view.manga = bool(self.settings.value(f"manga_by_file/{key}", self.manga_default, bool))
        self._add_recent(path)
        self.settings.setValue("last_dir", os.path.dirname(path))

        self.setWindowTitle(f"{os.path.basename(path)} — {APP_NAME}")
        self._sync_ui()
        start = page if page is not None else int(self.settings.value(f"progress/{key}", 0))
        self.go_to(start, anchor="start")
        solid = " · RAR solido: le pagine lontane richiedono più tempo" if archive.solid else ""
        self._info(f"{archive.kind}, {len(archive)} pagine, indice letto in {archive.index_time_ms:.1f} ms{solid}")
        return True

    def close_document(self) -> None:
        self._save_progress()
        self.loader.set_archive(None)
        self.path = None
        self._shown_key = None
        self.index = 0
        self.view.show_message("Apri un fumetto (Ctrl+O) oppure trascinalo qui")
        self.setWindowTitle(APP_NAME)
        self._info("")
        self._sync_ui()

    def _save_progress(self) -> None:
        if self.path and self.page_count:
            self.settings.setValue(f"progress/{_file_key(self.path)}", self.index)

    # ================================================================== navigazione
    def go_to(self, index: int, anchor: str = "start") -> None:
        n = self.page_count
        if n == 0:
            return
        index = max(0, min(n - 1, index))
        self._last_step = 1 if index >= self.index else -1
        self.index = index
        self._anchor = anchor
        self._requested_at = time.perf_counter()
        self.loader.set_target(self.view.target())
        page = self.loader.get(index, self.enhanced)
        if page is not None:
            self._show(page)
        else:
            self.view.set_loading(True)
        self.loader.schedule(index, self.enhanced, self._last_step)
        self._sync_ui()

    def step(self, delta: int, anchor: str = "start") -> None:
        if not self.page_count:
            return
        target = self.index + delta
        if 0 <= target < self.page_count:
            self.go_to(target, anchor)

    def _go_last(self) -> None:
        if self.page_count:
            self.go_to(self.page_count - 1)

    def nav_left(self) -> None:
        self.step(1 if self.view.manga else -1)

    def nav_right(self) -> None:
        self.step(-1 if self.view.manga else 1)

    def _on_flip(self, direction: int) -> None:
        # Arrivando dal basso alla pagina precedente la mostro dal fondo: lo scorrimento
        # all'indietro resta continuo come in un rotolo.
        self.step(direction, "start" if direction > 0 else "end")

    def _on_click_zone(self, zone: str) -> None:
        if zone == "left":
            self.nav_left()
        elif zone == "right":
            self.nav_right()

    def _line_step(self) -> float:
        return max(40.0, self.view.height() * 0.15)

    def _page_step(self) -> float:
        return max(80.0, self.view.height() * 0.9)

    def goto_dialog(self) -> None:
        if not self.page_count:
            return
        value, ok = QInputDialog.getInt(self, "Vai alla pagina", f"Pagina (1–{self.page_count}):",
                                        self.index + 1, 1, self.page_count)
        if ok:
            self.go_to(value - 1)

    def _on_spin_changed(self, value: int) -> None:
        if value - 1 != self.index:
            self.go_to(value - 1)
        self.view.setFocus()

    def _on_slider_changed(self, value: int) -> None:
        if self.slider.isSliderDown():
            # Durante il trascinamento aggiorno solo l'indicatore: niente decodifiche inutili.
            self.status_page.setText(f"Pagina {value + 1} / {self.page_count}")
        elif value != self.index:
            self.go_to(value)

    # ================================================================== pagine pronte
    def _show(self, page: PageImage) -> None:
        anchor = self._anchor
        self._anchor = "keep"
        self.view.set_page(page, anchor)
        self._shown_key = (page.index, page.enhanced)
        elapsed = (time.perf_counter() - self._requested_at) * 1000.0
        self.page_shown.emit(page.index, page.enhanced, elapsed)
        self._sync_ui()

    def _on_page_ready(self, page: PageImage) -> None:
        if page.index == self.index and page.enhanced == self.enhanced and self._shown_key != (page.index, page.enhanced):
            self._show(page)
        self._update_status()

    def _on_page_failed(self, index: int, enhanced: bool, message: str) -> None:
        if index == self.index and enhanced == self.enhanced:
            self._shown_key = None
            self.view.show_message(f"Impossibile mostrare la pagina {index + 1}:\n{message}")
            self._info(message)

    def _on_viewport_changed(self) -> None:
        self.loader.set_target(self.view.target())
        self._update_status()

    # ================================================================== zoom / modalità
    def _set_zoom(self, mode: ZoomMode, factor: float | None = None, pivot: QPointF | None = None) -> None:
        if factor is not None:
            factor = min(ZOOM_MAX, max(ZOOM_MIN, factor))
        self.view.set_zoom(mode, factor, pivot)
        self.loader.set_target(self.view.target())
        self.settings.setValue("zoom_mode", mode.value)
        self.settings.setValue("zoom_factor", self.view.factor)
        self._sync_ui()

    def _zoom_by(self, ratio: float, pivot: QPointF | None = None) -> None:
        self._set_zoom(ZoomMode.CUSTOM, self.view.scale * ratio, pivot)

    def _on_zoom_step(self, direction: int, pos: QPointF) -> None:
        self._zoom_by(_ZOOM_STEP if direction > 0 else 1 / _ZOOM_STEP, pos)

    def set_manga(self, enabled: bool) -> None:
        if self.view.manga == enabled:
            return
        self.view.manga = enabled
        self.settings.setValue("manga", enabled)
        self.manga_default = enabled
        if self.path:
            self.settings.setValue(f"manga_by_file/{_file_key(self.path)}", enabled)
        self.view.set_page(self.view.page, "start")
        self._sync_ui()

    def set_enhanced(self, enabled: bool) -> None:
        if self.enhanced == enabled:
            return
        self.enhanced = enabled
        self.settings.setValue("enhance", enabled)
        if self.page_count:
            # Mantengo la posizione di lettura: cambia solo l'elaborazione dell'immagine.
            self.go_to(self.index, anchor="keep")
        self._sync_ui()

    def toggle_fullscreen(self, checked: bool | None = None) -> None:
        full = (not self.isFullScreen()) if checked is None else bool(checked)
        if full and not self.isFullScreen():
            self.menuBar().hide()
            self.toolbar.hide()
            self.statusBar().hide()
            self.showFullScreen()
        elif not full and self.isFullScreen():
            self.showNormal()
            self.menuBar().show()
            self.toolbar.show()
            self.statusBar().show()
        self._sync_ui()

    def _escape(self) -> None:
        if self.isFullScreen():
            self.toggle_fullscreen(False)

    # ================================================================== stato UI
    def _sync_ui(self) -> None:
        n = self.page_count
        has = n > 0
        for act in (self.act_first, self.act_last, self.act_left, self.act_right, self.act_next,
                    self.act_prev, self.act_goto, self.act_close):
            act.setEnabled(has)
        if has:
            # In modalità manga la freccia a sinistra porta avanti: i tooltip lo dicono.
            forward, backward = ("Pagina successiva", "Pagina precedente")
            self.act_left.setToolTip(f"{forward if self.view.manga else backward} (←)")
            self.act_right.setToolTip(f"{backward if self.view.manga else forward} (→)")
            self.act_first.setEnabled(self.index > 0)
            self.act_last.setEnabled(self.index < n - 1)

        self.page_spin.blockSignals(True)
        self.slider.blockSignals(True)
        self.page_spin.setMaximum(max(1, n))
        self.page_spin.setValue(self.index + 1 if has else 1)
        self.page_spin.setEnabled(has)
        self.page_total.setText(f" / {n} ")
        self.slider.setMaximum(max(0, n - 1))
        self.slider.setValue(self.index)
        self.slider.setEnabled(has)
        # In modalità manga lo slider cresce da destra verso sinistra, come la lettura.
        self.slider.setInvertedAppearance(self.view.manga)
        self.page_spin.blockSignals(False)
        self.slider.blockSignals(False)

        mode = self.view.mode
        for act, m in ((self.act_fit_w, ZoomMode.FIT_WIDTH), (self.act_fit_h, ZoomMode.FIT_HEIGHT),
                       (self.act_fit_p, ZoomMode.FIT_PAGE)):
            act.blockSignals(True)
            act.setChecked(mode is m)
            act.blockSignals(False)
        for act, val in ((self.act_manga, self.view.manga), (self.act_enhance, self.enhanced),
                         (self.act_fullscreen, self.isFullScreen())):
            act.blockSignals(True)
            act.setChecked(val)
            act.blockSignals(False)

        self.view.osd_text = f"{self.index + 1} / {n}" if has and self.isFullScreen() else ""
        self.view.update()
        self._update_status()

    def _update_status(self) -> None:
        n = self.page_count
        self.status_page.setText(f"Pagina {self.index + 1} / {n}" if n else "")
        page = self.view.page
        self.status_zoom.setText(f"{self.view.scale * 100:.0f}%" if page is not None else "")
        flags = []
        if self.view.manga:
            flags.append("MANGA ←")
        if self.enhanced:
            flags.append("AUTO ✦")
        cached, size = self.loader.stats()
        if n:
            flags.append(f"cache {cached} pag. / {size / 1048576:.0f} MB")
        self.status_flags.setText("  ·  ".join(flags))
        if page is not None and n:
            entry = self.loader.archive.pages[page.index]
            self.status_info.setToolTip(entry.name)

    def _info(self, text: str) -> None:
        self.status_info.setText(text)

    # ================================================================== recenti / dialoghi
    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        if isinstance(value, str):
            value = [value]
        return [p for p in (value or []) if isinstance(p, str)]

    def _add_recent(self, path: str) -> None:
        items = [p for p in self._recent() if p != path]
        items.insert(0, path)
        self.settings.setValue("recent", items[:_MAX_RECENT])

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        items = [p for p in self._recent() if os.path.isfile(p)]
        if not items:
            act = self.recent_menu.addAction("(nessun file recente)")
            act.setEnabled(False)
            return
        for i, path in enumerate(items, 1):
            act = self.recent_menu.addAction(f"&{i % 10}  {os.path.basename(path)}")
            act.setToolTip(path)
            act.triggered.connect(lambda _=False, p=path: self.open_file(p))

    def show_help(self) -> None:
        QMessageBox.information(self, f"{APP_NAME} — scorciatoie", _HELP)

    def show_about(self) -> None:
        QMessageBox.about(
            self, f"Informazioni su {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Lettore di fumetti CBZ / CBR / CB7 ad accesso casuale: legge solo l'indice "
            "dell'archivio ed estrae in memoria esclusivamente le pagine visualizzate, "
            "senza mai scrivere su disco.</p>",
        )

    # ================================================================== eventi finestra
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                event.acceptProposedAction()
                # Rimando l'apertura a dopo il drop, così un eventuale messaggio d'errore
                # modale non blocca il gestore del trascinamento del compositor.
                QTimer.singleShot(0, lambda p=url.toLocalFile(): self.open_file(p))
                return

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_progress()
        if not self.isFullScreen():
            self.settings.setValue("geometry", self.saveGeometry())
        self.loader.shutdown()
        super().closeEvent(event)

