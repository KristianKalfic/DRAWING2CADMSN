"""Drawing2CAD: MCP-Server, der technische 2D-Zeichnungen in geprüfte STEP-Modelle umwandelt.

Schichten, von unten nach oben (erlaubte Importe prüft tests/test_layers.py):

- Kern: `spec` (Spezifikation und Abnahmeschwellen), `config` (Umgebungsvariablen), `errors` (Fehlertexte),
  `oppath` (Punktpfade in Vorgängen), `log` (Protokollierung)
- `drawing`: Zeichnung lesen (Seiten, Textebene, Maßstab, Vektorgeometrie), ohne CAD-Kern
- `cad`: Geometrie mit CadQuery/OpenCascade (Vorgänge, Prüfungen, Export, isolierter Aufbau-Worker)
- `quality`: Prüfliste, Plausibilität und Toleranzen
- `session`: Bauteilsitzungen, die Kern, `cad` und `quality` verbinden
- `server`: MCP-Werkzeuge für das Sprachmodell
- `agent`: Agent auf Basis des Claude Agent SDK
- `web`: Webansicht des Ergebnisordners
- `demo`: Demo ohne Sprachmodell
"""
