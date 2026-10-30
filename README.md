# IcaroComics

Lettore di fumetti CBZ / CBR / CB7 per Fedora/GNOME pensato per archivi molto grandi: all'apertura legge solo l'indice dell'archivio ed estrae in RAM esclusivamente le pagine da mostrare, con prefetch asincrono delle pagine vicine. Non scrive mai nulla in `/tmp`.

## Requisiti

- `python3`, `python3-pyqt6`, `python3-pillow`
- `unrar` per i CBR (RPM Fusion nonfree)
- `7zip` per i CB7 (facoltativo)

## Installazione e disinstallazione

```sh
./install.sh             # installa per l'utente e registra i tipi MIME
./uninstall.sh           # rimuove e ripristina le associazioni MIME precedenti
./uninstall.sh --purge   # rimuove anche le preferenze
```

## Uso

```sh
icarocomics fumetto.cbz [--page N]
icarocomics --selftest /percorso/screenshot fumetto.cbz   # test automatico con tempi in JSON
```

Premi **F1** nell'applicazione per l'elenco delle scorciatoie. Le principali:

| Tasto | Azione |
|---|---|
| ← / → | cambio pagina (direzione invertita in modalità Manga) |
| Spazio, PagGiù | scorre e poi passa alla pagina successiva |
| W / H / B / 1 | adatta a larghezza / altezza / pagina, oppure 100% |
| M | modalità Manga |
| E | auto-miglioramento |
| F | schermo intero |

Per i dettagli di architettura vedi [`docs/lettore-fumetti-accesso-casuale.md`](docs/lettore-fumetti-accesso-casuale.md).
