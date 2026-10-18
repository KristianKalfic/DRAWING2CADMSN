"""Laufprotokolle der Ansicht: eine JSONL-Datei je Lauf unter results/<teil>/runs/.

Das Format ist für Web- und Kommandozeilenläufe dasselbe: die Kopfzeile (Typ `meta`) nennt run_id, Teil,
Zeichnung, Modell und Startzeit; jede weitere Zeile ist ein Ereignis mit `at`, den Sekunden seit dem Start.
Fehlt die letzte Zeile `done`, gilt der Lauf als abgebrochen (so zeigt ihn die Ansicht).

Das Modul kommt ohne Fremdbibliotheken aus, damit `python -m d2c.agent` auch ohne CadQuery läuft.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from d2c.config import RUNS

log = logging.getLogger(__name__)


def new_meta(part: str, pdf: str, model: str | None) -> dict:
    """Die Kopfzeile eines Laufs, samt run_id, die auch den Dateinamen mitbestimmt."""
    return {
        "run_id": uuid.uuid4().hex,
        "part": part,
        "pdf": pdf,
        "model": model,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class Trace:
    """Ein offenes Laufprotokoll. Ein Fehler beim Schreiben beendet nur das Protokoll, nie den Lauf.

    Der Dateiname beginnt mit dem Startzeitpunkt, damit die Liste der Läufe ohne Lesen sortiert ist.
    """

    def __init__(self, folder: Path, meta: dict) -> None:
        self.t0 = time.monotonic()
        self.file: IO[str] | None = None
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        try:
            path = folder / RUNS / f"{stamp}-{str(meta.get('run_id', ''))[:8] or 'run'}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.file = path.open("a", encoding="utf-8")
            self._write({"type": "meta"} | meta)
        except OSError:
            log.warning("Run log under %s is not writable; the run continues without it", folder, exc_info=True)
            self.file = None

    def _write(self, line: dict) -> None:
        assert self.file is not None
        self.file.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        self.file.flush()  # jederzeit lesbar, auch wenn der Lauf oder der Server danach abbricht

    def event(self, event: dict) -> None:
        """Ein Ereignis mit Zeitversatz als Zeile anhängen."""
        if self.file is None:
            return
        try:
            self._write({"at": round(time.monotonic() - self.t0, 3)} | event)
        except (OSError, TypeError, ValueError):
            log.warning("Run log no longer writable; the run continues", exc_info=True)
            self.close()

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
