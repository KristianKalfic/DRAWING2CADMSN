# Drawing2CAD (Hackathon-Prototyp)

Aus einer 2D-Zeichnung (PDF/Scan/Bild) entsteht ein einfaches 3D-Modell als STEP-Datei.
Alles läuft lokal; der CAD-Kern ist CadQuery (OpenCascade, quelloffene Software).

## Architektur

```
Zeichnung ──► das Sprachmodell liest sie, schreibt JSON (Schema unten, Beispiel: examples/beispiel_v2.json)
          ──► MCP-Server server.py ── build_from_json
                 └─ json_builder.py: JSON ──► CadQuery-Code (deterministisch, nur validierte Zahlen
                                              + Selektor-Whitelist, keine Freitext-Einschleusung)
          ──► Validierung (Volumenkörper gültig, genau 1 Körper, Maße + Lage, Bohrungen)
          ──► export_step: STEP schreiben, erneut einlesen, Volumen vergleichen
          ──► issue_check.py: 2 Extraktionen vergleichen, Plausibilität, Nachmessen in FreeCAD
```

Nicht Umsetzbares wird **nicht geraten**, sondern in `unsupported` / `warnings` (Server) bzw.
als Prüfhinweis (`issue_check`) gemeldet. Unsichere Maße gehören mit Begründung in `review`.

Dateien: `server.py` (MCP-Werkzeuge), `json_builder.py` (JSON → CadQuery), `issue_check.py`
(unabhängige Prüfung), `tests/` (pytest), `Dockerfile`, `environment.yml`.

## Installation

```bash
conda env create -f environment.yml      # legt Umgebung "d2c" an (Python 3.11, CadQuery, mcp<2, pytest)
conda activate d2c
```

`mcp` ist auf `<2` gepinnt: In mcp 2.x gibt es `mcp.server.fastmcp` nicht mehr.

Docker (Server ohne Netzwerk):

```bash
docker build -t drawing2cad .
docker run -i --rm --network none -v "$PWD/out:/data/out" drawing2cad
# Linux-Host zusätzlich: --user $(id -u):$(id -g)   (sonst keine Schreibrechte in out/)
```

## Prüfungen

```bash
python json_builder.py examples/beispiel_v2.json   # ohne KI: -> out/Flanschplatte_4711.step
python -m pytest tests -q                          # Modellaufbau, Validierung, STEP-Rückimport, issue_check
python issue_check.py A.json B.json out/teil.step --freecad "C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe"
```

`issue_check.py` gibt ohne FreeCAD den Blocker `FREECAD_NOT_FOUND` aus und gibt nicht frei (Exit 1).
Die FreeCAD-Prüfung in `tests/` wird übersprungen, wenn `FreeCADCmd` nicht im PATH ist.

## MCP-Clients

Einrichtung für MCP-Clients: siehe das Haupt-README.

## JSON-Schema (Kurzreferenz)

Einheiten **mm**. Nullpunkt = **Mitte der Unterseite** (X/Y zentriert, Z=0 unten, Z=height oben).
Reihenfolge beim Bauen: Grundkörper → Kanten (fillet/chamfer) → Taschen → Bohrungen.

```jsonc
{
  "part_name": "Flanschplatte_4711",
  "base": {"shape": "box", "length": 100, "width": 60, "height": 20},
  //   | {"shape": "cylinder", "diameter": 50, "height": 10}
  //   | {"shape": "profile", "points": [[x, y], ...], "height": 10}  // Punkte um (0,0) zentriert angeben
  "features": [
    {"type": "fillet", "edges": "|Z", "size": 5},           // oder "chamfer"; edges: |Z >Z <Z #Z ...
    {"type": "pocket", "x": 0, "y": 0, "length": 30, "width": 20, "depth": 5, "face": "top"},
    {"type": "hole", "x": 0, "y": 0, "diameter": 8.5, "through": true, "face": "top",
     "pattern": {"type": "rect", "nx": 2, "ny": 2, "dx": 70, "dy": 40}}
    // Blindbohrung: "depth": 8 statt "through"; face: "top" | "bottom"
    // Senkung: "counterbore_diameter": 11, "counterbore_depth": 6 (Tiefe Pflicht)
    // Kreismuster: "pattern": {"type": "circle", "n": 6, "radius": 30, "start_angle": 0}
  ],
  "review": [{"feature": "Taschentiefenmaß", "value": 5, "reason": "nicht bemaßt", "confidence": 0.6}]
}
```

Optional pro Eintrag: `evidence` (wo in der Zeichnung), `confidence` (0…1; < 0.7 → Warnung).
Muster nur als Text (`"pattern": "4x"`) wird **nicht** vervielfacht, sondern als Warnung gemeldet.

## Offene Punkte

- Bohrungen und Taschen auf **Seitenflächen** (nur `top`/`bottom`); Taschen mit Drehwinkel
  oder nicht-rechteckiger Kontur.
- Kegelsenkungen, Gewinde, Nuten, Fasen und Rundungen an Bohrungs- oder Taschenkanten.
- `validate()` prüft Bohrungen nur über Ø und X/Y (nicht Tiefe, nicht Senkungsmaße); Taschen
  werden gar nicht nachgemessen.
- Profil: Nullpunkt wird nicht automatisch zentriert (nur Warnung); `plausible()` prüft
  Bohrungslage nur bei Box und Zylinder.
- `compare()` vergleicht nur Grundkörper und Bohrungen, nicht Taschen/Kanten.
- Der FreeCAD-Teil von `issue_check.py` wurde nicht getestet, weil FreeCAD in der Testumgebung nicht installiert war.
- Frühere Werkzeuge (`create_base_body`, `add_hole`, …, `revolve_profile`): nur Basisfälle geprüft;
  Bedeutung von x/y auf Seitenflächen (`>X` usw.) ist undefiniert.
- Einlesen der Zeichnung (PDF/Bild) ist nicht Teil des Servers; `pymupdf`/`pillow` sind
  installiert, werden aber noch nicht verwendet.
- Der Betrieb unter Windows (lokal und mit Docker Desktop) wurde nicht geprüft.
