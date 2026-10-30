"""Punto di ingresso di IcaroComics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from urllib.parse import unquote, urlparse

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from icarocomics import APP_ID, APP_NAME, __version__
from icarocomics.layout import ZoomMode
from icarocomics.mainwindow import MainWindow

_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{APP_ID}.svg")


def _install_excepthook() -> None:
    # Con PyQt6 un'eccezione non gestita in uno slot termina il processo: preferisco
    # registrarla e mostrarla, lasciando l'utente libero di continuare a leggere.
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        app = QApplication.instance()
        if app is not None and not os.environ.get("ICAROCOMICS_SELFTEST"):
            QMessageBox.warning(None, APP_NAME, f"Errore inatteso:\n\n{exc_type.__name__}: {exc}")

    sys.excepthook = hook


def _normalize_path(arg: str) -> str:
    if arg.startswith("file://"):
        return unquote(urlparse(arg).path)
    return arg


class SelfTest(QObject):
    """Verifica automatica end-to-end: apertura, prima pagina, sfoglio con prefetch, salto
    all'ultima pagina, auto-miglioramento, manga + adatta larghezza. Salva screenshot e
    stampa un riepilogo JSON dei tempi."""

    def __init__(self, win: MainWindow, path: str, outdir: str):
        super().__init__(win)
        self.win = win
        self.path = path
        self.outdir = outdir
        self.results: dict = {"file": path, "flips_ms": []}
        self._waiting: str | None = None
        self._steps = [
            self._s_open, self._s_grab_first, self._s_flip, self._s_flip, self._s_flip,
            self._s_flip, self._s_flip, self._s_last, self._s_enhance, self._s_manga_width, self._s_done,
        ]
        win.page_shown.connect(self._on_shown)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(lambda: self._fail("timeout"))

    def start(self) -> None:
        os.makedirs(self.outdir, exist_ok=True)
        self._deadline.start(60_000)
        QTimer.singleShot(200, self._next)

    def _next(self, delay: int = 0) -> None:
        if not self._steps:
            return
        step = self._steps.pop(0)
        QTimer.singleShot(delay, step)

    def _expect(self, tag: str) -> None:
        self._waiting = tag

    def _on_shown(self, index: int, enhanced: bool, ms: float) -> None:
        tag, self._waiting = self._waiting, None
        if tag is None:
            return
        if tag == "flip":
            self.results["flips_ms"].append(round(ms, 1))
        else:
            self.results[f"{tag}_ms"] = round(ms, 1)
        # Lascio respirare il prefetch come farebbe un lettore reale tra una pagina e l'altra.
        self._next(400)

    def _grab(self, name: str) -> None:
        self.win.grab().save(os.path.join(self.outdir, name))

    def _s_open(self) -> None:
        t0 = time.perf_counter()
        self._expect("first_page")
        if not self.win.open_file(self.path, page=0):
            self._fail("apertura fallita")
            return
        archive = self.win.loader.archive
        self.results.update(
            open_call_ms=round((time.perf_counter() - t0) * 1000, 1),
            index_ms=round(archive.index_time_ms, 1), kind=archive.kind,
            pages=len(archive), solid=archive.solid,
        )

    def _s_grab_first(self) -> None:
        self._grab("01_prima_pagina.png")
        self._next()

    def _s_flip(self) -> None:
        self._expect("flip")
        self.win.step(1)

    def _s_last(self) -> None:
        self._expect("jump_last")
        self.win._go_last()

    def _s_enhance(self) -> None:
        self._grab("02_ultima_pagina.png")
        self._expect("enhance_toggle")
        self.win.act_enhance.setChecked(True)

    def _s_manga_width(self) -> None:
        self._grab("03_auto_miglioramento.png")
        self.win.act_manga.setChecked(True)
        self.win._set_zoom(ZoomMode.FIT_WIDTH)
        QTimer.singleShot(300, lambda: (self._grab("04_manga_larghezza.png"), self._next()))

    def _s_done(self) -> None:
        cached, size = self.win.loader.stats()
        self.results.update(cache_pages=cached, cache_mb=round(size / 1048576, 1), ok=True)
        # Ripristino le preferenze toccate dal test per non alterare quelle dell'utente.
        self.win.act_enhance.setChecked(False)
        self.win.act_manga.setChecked(False)
        self.win._set_zoom(ZoomMode.FIT_PAGE)
        self._finish(0)

    def _fail(self, reason: str) -> None:
        self.results.update(ok=False, error=reason)
        self._finish(1)

    def _finish(self, code: int) -> None:
        self._deadline.stop()
        print(json.dumps(self.results, ensure_ascii=False, indent=2), flush=True)
        self.win.close()
        QApplication.instance().exit(code)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} — lettore CBZ/CBR/CB7")
    parser.add_argument("file", nargs="?", help="fumetto da aprire")
    parser.add_argument("--page", type=int, help="pagina iniziale (1 = prima)")
    parser.add_argument("--threads", type=int, default=2, help="thread di decodifica (default 2)")
    parser.add_argument("--selftest", metavar="DIR", help="esegue il test automatico salvando gli screenshot in DIR")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args, qt_args = parser.parse_known_args(argv[1:])

    if args.selftest:
        os.environ["ICAROCOMICS_SELFTEST"] = "1"
    # Il desktop file name deve combaciare con il file .desktop: su Wayland diventa l'app_id
    # e GNOME lo usa per associare finestra, icona e voce nella dash.
    QGuiApplication.setDesktopFileName(APP_ID)
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    app = QApplication([argv[0], *qt_args])
    app.setWindowIcon(QIcon.fromTheme(APP_ID, QIcon(_ICON_PATH)))
    _install_excepthook()

    win = MainWindow(loader_threads=args.threads)
    win.show()

    if args.selftest:
        if not args.file:
            parser.error("--selftest richiede un file")
        win.resize(1000, 1100)
        SelfTest(win, os.path.abspath(_normalize_path(args.file)), args.selftest).start()
    elif args.file:
        path = _normalize_path(args.file)
        page = args.page - 1 if args.page else None
        # Apro dopo che il loop eventi è partito: la finestra compare subito, con la
        # geometria definitiva, e la prima pagina viene pre-ridimensionata correttamente.
        QTimer.singleShot(0, lambda: win.open_file(path, page))
    win.view.setFocus(Qt.FocusReason.OtherFocusReason)
    return app.exec()
