"""
Drawing2CAD MCP-Server (Prototyp)
- Baut 3D-Modelle ausschließlich über strukturierte Tools (CadQuery/OpenCascade)
- Validiert nach jedem Build (Solid gültig, Maße vs. Zeichnung, STEP-Reimport)
- Freier Code nur als Fallback in isolierter Sandbox (Subprozess, Timeout, kein Netz im Container)
Start:  python server.py   (stdio-Transport, z. B. für Claude Desktop / LM Studio / Ollama-Clients)
"""
import json, os, subprocess, sys, tempfile, uuid
import cadquery as cq
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("drawing2cad")
OUT = os.environ.get("D2C_OUT", "/data/out")
os.makedirs(OUT, exist_ok=True)

# Arbeitsspeicher: part_id -> {"wp": Workplane, "spec": dict, "log": [...]}
PARTS: dict[str, dict] = {}


def _part(pid):
    if pid not in PARTS:
        raise ValueError(f"Unbekannte part_id {pid}. Erst create_base_body aufrufen.")
    return PARTS[pid]


def _bbox(wp):
    bb = wp.val().BoundingBox()
    return {"x": round(bb.xlen, 3), "y": round(bb.ylen, 3), "z": round(bb.zlen, 3)}


# ---------------- Basiskörper ----------------
@mcp.tool()
def create_base_body(shape: str, length: float = 0, width: float = 0, height: float = 0,
                     diameter: float = 0, part_name: str = "part") -> dict:
    """Erzeugt den Grundkörper. shape: 'box' (length x width x height, mm)
    oder 'cylinder' (diameter, height, Achse = Z). Ursprung: Mitte der Unterseite."""
    if shape == "box":
        wp = cq.Workplane("XY").box(length, width, height, centered=(True, True, False))
    elif shape == "cylinder":
        wp = cq.Workplane("XY").circle(diameter / 2).extrude(height)
    else:
        return {"ok": False, "error": "shape muss 'box' oder 'cylinder' sein"}
    pid = f"{part_name}-{uuid.uuid4().hex[:6]}"
    PARTS[pid] = {"wp": wp, "spec": {}, "log": [f"base {shape}"]}
    return {"ok": True, "part_id": pid, "bbox": _bbox(wp)}


@mcp.tool()
def extrude_profile(points: list[list[float]], height: float, part_name: str = "part") -> dict:
    """Grundkörper aus geschlossener 2D-Kontur (Liste [x,y] in mm, XY-Ebene) extrudiert um height."""
    wp = cq.Workplane("XY").polyline([tuple(p) for p in points]).close().extrude(height)
    pid = f"{part_name}-{uuid.uuid4().hex[:6]}"
    PARTS[pid] = {"wp": wp, "spec": {}, "log": ["extruded profile"]}
    return {"ok": True, "part_id": pid, "bbox": _bbox(wp)}


@mcp.tool()
def revolve_profile(points: list[list[float]], part_name: str = "part") -> dict:
    """Drehteil: Halbkontur [r, z] (r>=0) wird 360° um die Z-Achse rotiert."""
    wp = (cq.Workplane("XZ").polyline([tuple(p) for p in points]).close()
          .revolve(360, (0, 0, 0), (0, 1, 0)))
    pid = f"{part_name}-{uuid.uuid4().hex[:6]}"
    PARTS[pid] = {"wp": wp, "spec": {}, "log": ["revolved profile"]}
    return {"ok": True, "part_id": pid, "bbox": _bbox(wp)}


# ---------------- Features ----------------
@mcp.tool()
def add_hole(part_id: str, x: float, y: float, diameter: float, depth: float = 0,
             face: str = ">Z", counterbore_diameter: float = 0, counterbore_depth: float = 0) -> dict:
    """Bohrung an Position (x,y) auf der Fläche face ('>Z' oben, '<Z' unten, '>X' ...).
    depth=0 -> Durchgangsbohrung. Optional Senkung (counterbore)."""
    p = _part(part_id)
    wp = p["wp"].faces(face).workplane(centerOption="ProjectedOrigin", origin=(0, 0, 0)).pushPoints([(x, y)])
    if counterbore_diameter:
        wp = wp.cboreHole(diameter, counterbore_diameter, counterbore_depth, depth or None)
    else:
        wp = wp.hole(diameter, depth or None)
    p["wp"] = wp
    p["log"].append(f"hole d={diameter} @({x},{y}) {face}")
    return {"ok": True, "bbox": _bbox(wp)}


@mcp.tool()
def add_pocket(part_id: str, x: float, y: float, length: float, width: float, depth: float,
               face: str = ">Z") -> dict:
    """Rechteckige Tasche (Mitte x,y) mit Tiefe depth in Fläche face."""
    p = _part(part_id)
    p["wp"] = (p["wp"].faces(face).workplane(centerOption="ProjectedOrigin", origin=(0, 0, 0))
               .center(x, y).rect(length, width).cutBlind(-depth))
    p["log"].append(f"pocket {length}x{width}x{depth}")
    return {"ok": True, "bbox": _bbox(p["wp"])}


@mcp.tool()
def add_fillet_or_chamfer(part_id: str, kind: str, size: float, edges: str = "|Z") -> dict:
    """Rundung/Fase. kind: 'fillet'|'chamfer'. edges: CadQuery-Selektor, z. B. '|Z' (senkrechte Kanten), '>Z' (obere Kanten)."""
    p = _part(part_id)
    sel = p["wp"].edges(edges)
    p["wp"] = sel.fillet(size) if kind == "fillet" else sel.chamfer(size)
    p["log"].append(f"{kind} {size} on {edges}")
    return {"ok": True, "bbox": _bbox(p["wp"])}


# ---------------- Validierung & Export ----------------
@mcp.tool()
def set_expected_dimensions(part_id: str, spec: dict) -> dict:
    """Soll-Maße aus der Zeichnung hinterlegen, z. B.
    {"bbox": {"x":100,"y":60,"z":20}, "holes":[{"x":30,"y":0,"d":8.5}], "tol": 0.05}"""
    _part(part_id)["spec"] = spec
    return {"ok": True}


def _validate(p) -> dict:
    solid = p["wp"].val()
    res = {"is_valid": solid.isValid(), "solids": len(p["wp"].solids().vals()),
           "volume": round(solid.Volume(), 3), "bbox": _bbox(p["wp"]), "checks": []}
    spec, tol = p["spec"], p["spec"].get("tol", 0.05)
    for ax, val in spec.get("bbox", {}).items():
        act = res["bbox"][ax]
        res["checks"].append({"check": f"bbox_{ax}", "soll": val, "ist": act, "pass": abs(act - val) <= tol})
    # Bohrungen: zylindrische Flächen mit passendem Radius und Achsposition suchen
    cyl = [f for f in solid.Faces() if f.geomType() == "CYLINDER"]
    found = []
    for f in cyl:
        ad = f._geomAdaptor().Cylinder()
        loc = ad.Location()
        found.append({"d": round(2 * ad.Radius(), 3), "x": round(loc.X(), 3), "y": round(loc.Y(), 3)})
    for h in spec.get("holes", []):
        ok = any(abs(c["d"] - h["d"]) <= tol and abs(c["x"] - h["x"]) <= tol and abs(c["y"] - h["y"]) <= tol
                 for c in found)
        res["checks"].append({"check": "hole", "soll": h, "pass": ok})
    res["all_pass"] = res["is_valid"] and res["solids"] == 1 and all(c["pass"] for c in res["checks"])
    return res


@mcp.tool()
def validate(part_id: str) -> dict:
    """Prüft: Solid geschlossen/gültig, genau 1 Körper, Maße & Bohrungen gegen Soll-Spec. Maschinenlesbar."""
    return _validate(_part(part_id))


@mcp.tool()
def export_step(part_id: str, filename: str = "") -> dict:
    """Exportiert STEP, re-importiert die Datei und validiert erneut. Gibt Pfad + Prüfergebnis zurück."""
    p = _part(part_id)
    path = os.path.join(OUT, (filename or part_id) + ".step")
    cq.exporters.export(p["wp"], path)
    try:
        re = cq.importers.importStep(path)
        reimport = {"ok": re.val().isValid(), "volume": round(re.val().Volume(), 3)}
    except Exception as e:
        reimport = {"ok": False, "error": str(e)}
    v = _validate(p)
    v["reimport"] = reimport
    v["volume_match"] = reimport.get("volume") is not None and abs(reimport["volume"] - v["volume"]) < 1e-3 * max(v["volume"], 1)
    v["all_pass"] = v["all_pass"] and reimport["ok"] and v["volume_match"]
    return {"path": path, "validation": v, "history": p["log"]}


# ---------------- Fallback: Sandbox ----------------
@mcp.tool()
def run_cadquery_script(part_id: str, script: str, timeout_s: int = 20) -> dict:
    """NUR Fallback für Geometrie ohne passendes Tool. Script muss eine Variable `result` (cq.Workplane)
    setzen. Läuft in separatem Prozess mit Timeout (Netzwerk ist im Container gesperrt)."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "r.brep")
        code = "import cadquery as cq\n" + script + f"\ncq.exporters.export(result, {out!r}, 'BREP')\n"
        try:
            r = subprocess.run([sys.executable, "-I", "-c", code], cwd=d, timeout=timeout_s,
                               capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout"}
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr[-2000:]}
        shape = cq.importers.importBrep(out)
    PARTS.setdefault(part_id, {"spec": {}, "log": []})
    PARTS[part_id]["wp"] = cq.Workplane("XY").add(shape)
    PARTS[part_id]["log"].append("sandbox script")
    return {"ok": True, "part_id": part_id, "bbox": _bbox(PARTS[part_id]["wp"])}


@mcp.tool()
def write_review_list(part_id: str, items: list[dict]) -> dict:
    """Review-Liste speichern: [{"feature":..,"value":..,"reason":"angenommen aus Zeichnungsgeometrie ...","confidence":0.6}]"""
    path = os.path.join(OUT, part_id + "_review.json")
    json.dump(items, open(path, "w"), indent=2, ensure_ascii=False)
    return {"ok": True, "path": path, "count": len(items)}


# ---------------- JSON -> CadQuery (Hauptweg) ----------------
from json_builder import generate, execute


@mcp.tool()
def get_cadquery_code(spec: dict) -> dict:
    """Wandelt die JSON-Beschreibung des Teils in lesbaren CadQuery-Code um, OHNE zu bauen.
    Gibt cadquery_code, warnings und unsupported (nicht Umgesetztes) zurueck."""
    try:
        code, info = generate(spec)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "cadquery_code": code, **info}


@mcp.tool()
def build_from_json(spec: dict, part_name: str = "") -> dict:
    """HAUPTTOOL: baut das komplette Teil aus der JSON-Beschreibung (base + features).
    Erzeugt CadQuery-Code, fuehrt ihn aus, hinterlegt Soll-Maße automatisch und validiert.
    Danach export_step(part_id) aufrufen. Pruefe 'unsupported' und 'warnings' - dort steht, was NICHT gebaut wurde."""
    try:
        code, info = generate(spec)
        wp = execute(code)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    pid = f"{part_name or spec.get('part_name', 'part')}-{uuid.uuid4().hex[:6]}"
    PARTS[pid] = {"wp": wp, "spec": info["expected"], "log": ["build_from_json"]}
    return {"ok": True, "part_id": pid, "cadquery_code": code,
            "warnings": info["warnings"], "unsupported": info["unsupported"],
            "validation": _validate(PARTS[pid])}


if __name__ == "__main__":
    mcp.run()
