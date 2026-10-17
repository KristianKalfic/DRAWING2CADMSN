# Drawing2CAD

Drawing2CAD erzeugt aus einer technischen 2D-Zeichnung ein geprüftes 3D-Volumenmodell. Ein Sprachmodell liest die Zeichnung und baut das Teil ausschließlich über die Werkzeuge eines lokalen MCP-Servers. Der Server prüft jeden Bauschritt, exportiert das Modell als STEP und markiert jede Annahme farbig im Modell.

## Schnellstart

```
docker build -t d2c .
docker run --rm d2c pytest -q
```

## MCP-Server

Der Server spricht MCP über stdio und läuft ohne Netzwerk:

```
docker run -i --rm --network none \
  -v "$PWD/drawings":/data:ro \
  -v "$PWD/results":/app/results \
  d2c
```

`drawings/` enthält die PDF-Zeichnungen, `results/` nimmt die Ergebnisse auf. Unter Windows PowerShell steht `${PWD}` statt `$PWD`.

## Ergebnisansicht

```
docker run --rm -p 8080:8080 -v "$PWD/results":/app/results -v "$PWD/drawings":/data d2c python -m d2c.web
```

Danach im Browser `http://localhost:8080` öffnen.

## Dokumentation

- [Systemspezifikation](docs/spec.md)
- [Architektur](docs/architektur.png)

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `d2c/` | MCP-Server, Datenmodell, Bau, Prüfungen, Export, Web-Ansicht |
| `tests/` | Tests (pytest) |
| `scripts/` | Erzeugung der Demo-Zeichnung |
| `prototype/` | erster Prototyp: JSON wird zu CadQuery-Code |
