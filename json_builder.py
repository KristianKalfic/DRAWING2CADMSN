"""
json_builder.py - uebersetzt die JSON-Beschreibung (aus der Zeichnung) deterministisch in CadQuery-Code.

- generate(spec) -> (cadquery_code: str, info: dict)   erzeugt lesbaren CadQuery-Quelltext
- execute(code)  -> cq.Workplane                       fuehrt genau diesen Text aus
- Der Code entsteht nur aus geprueften Zahlen und Selektor-Whitelist (keine Freitext-Einschleusung).
- Nicht Unterstuetztes wird NICHT still ignoriert, sondern in info["unsupported"] gemeldet.

Konventionen (muessen mit dem Prompt/Schema uebereinstimmen):
  Einheiten mm. Nullpunkt = Mitte der Unterseite (X/Y zentriert, Z=0 unten, Z=height oben).
  Reihenfolge: Basis -> Fillet/Chamfer (auf dem Grundkoerper) -> Taschen -> Bohrungen.
  face: "top" (>Z) oder "bottom" (<Z). Seitenflaechen sind noch nicht unterstuetzt.

Schema (Auszug):
  base: {shape:"box", length, width, height} | {shape:"cylinder", diameter, height}
        | {shape:"profile", points:[[x,y],...], height}
  features:
    {type:"hole", x, y, diameter, through:true | depth, face:"top",
        counterbore_diameter?, counterbore_depth?,
        pattern?: {type:"rect", nx, ny, dx, dy}   # x,y = Mitte des Musters
                | {type:"circle", n, radius, start_angle}}  # x,y = Mittelpunkt
    {type:"pocket", x, y, length, width, depth, face:"top"}
    {type:"fillet"|"chamfer", size, edges:"|Z"}
Aufruf als Skript:  python json_builder.py teil.json   -> out/<name>_cadquery.py + out/<name>.step
"""
import json, math, os, re, sys

SEL = re.compile(r"^[|<>#]?[XYZ]$")  # z. B. |Z  >Z  <Z  #Z


def _n(v, name, positive=False):
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' ist keine Zahl: {v!r}")
    if not math.isfinite(v):
        raise ValueError(f"'{name}' ist nicht endlich")
    if positive and v <= 0:
        raise ValueError(f"'{name}' muss > 0 sein (ist {v})")
    return repr(round(v, 6))


def _face(f):
    v = str(f.get("face", "top")).lower()
    if v in ("top", ">z", "oben"):
        return "top"
    if v in ("bottom", "<z", "unten"):
        return "bottom"
    return None


def normalize(spec):
    """Expandiert pattern-Dicts zu einzelnen Bohrungen. Gibt (spec, warnings) zurueck."""
    feats, warns = [], []
    for f in spec.get("features", []):
        p = f.get("pattern")
        if f.get("type") == "hole" and isinstance(p, dict):
            b = {k: v for k, v in f.items() if k != "pattern"}
            if p.get("type") == "rect":
                nx, ny, dx, dy = int(p["nx"]), int(p["ny"]), float(p["dx"]), float(p["dy"])
                for i in range(nx):
                    for j in range(ny):
                        feats.append({**b, "x": f["x"] + (i - (nx - 1) / 2) * dx,
                                      "y": f["y"] + (j - (ny - 1) / 2) * dy})
            elif p.get("type") == "circle":
                n, R, a0 = int(p["n"]), float(p["radius"]), float(p.get("start_angle", 0))
                for i in range(n):
                    a = math.radians(a0 + 360.0 * i / n)
                    feats.append({**b, "x": f["x"] + R * math.cos(a), "y": f["y"] + R * math.sin(a)})
            else:
                warns.append(f"Unbekannter Mustertyp {p.get('type')!r}: nur eine Bohrung gebaut")
                feats.append(b)
        else:
            if f.get("type") == "hole" and isinstance(p, str):
                warns.append(f"Muster nur als Text ('{p}') angegeben: NUR EINE Bohrung gebaut. "
                             f"Alle Bohrungen einzeln oder als pattern-Objekt angeben!")
            feats.append(f)
    return {**spec, "features": feats}, warns


def generate(spec):
    spec, warnings = normalize(spec)
    unsupported = []
    base = spec.get("base") or {}
    shape = base.get("shape", "box")
    lines = ["import cadquery as cq", "",
             "# Einheiten mm | Nullpunkt: Mitte Unterseite (Z=0 unten)", ""]

    # ---- Basis ----
    H = float(base.get("height", 0) or 0)
    if H <= 0:
        raise ValueError("base.height fehlt oder <= 0")
    h = _n(H, "base.height", True)
    if shape == "box":
        L, W = base.get("length"), base.get("width")
        lines.append(f'result = cq.Workplane("XY").box({_n(L,"length",True)}, {_n(W,"width",True)}, {h}, '
                     f'centered=(True, True, False))')
        bbox = {"x": float(L), "y": float(W), "z": H}
    elif shape == "cylinder":
        D = base.get("diameter")
        lines.append(f'result = cq.Workplane("XY").circle({_n(float(D)/2,"diameter",True)}).extrude({h})')
        bbox = {"x": float(D), "y": float(D), "z": H}
    elif shape == "profile":
        pts = base.get("points") or []
        if len(pts) < 3:
            raise ValueError("base.points braucht mindestens 3 Punkte")
        ptxt = ", ".join(f"({_n(p[0],'x')}, {_n(p[1],'y')})" for p in pts)
        lines.append(f'result = cq.Workplane("XY").polyline([{ptxt}]).close().extrude({h})')
        xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
        bbox = {"x": max(xs) - min(xs), "y": max(ys) - min(ys), "z": H}
    else:
        raise ValueError(f"Unbekannte base.shape {shape!r}")

    feats = spec.get("features", [])
    edge = [f for f in feats if f.get("type") in ("fillet", "chamfer")]
    pockets = [f for f in feats if f.get("type") == "pocket"]
    holes = [f for f in feats if f.get("type") == "hole"]
    others = [f for f in feats if f.get("type") not in ("fillet", "chamfer", "pocket", "hole")]
    for f in others:
        unsupported.append(f"Feature-Typ {f.get('type')!r} nicht unterstuetzt: {json.dumps(f)[:100]}")

    def cutter(face, extent_depth):
        """Gibt (Workplane-Praefix, Extrusionslaenge) zurueck; 1 mm Ueberstand gegen Koplanaritaet."""
        if face == "top":
            return f'cq.Workplane("XY").workplane(offset={_n(H+1,"o")})', f"-{_n(extent_depth+1,'d')}"
        return f'cq.Workplane("XY").workplane(offset=-1.0)', _n(extent_depth + 1, "d")

    # ---- Kanten (zuerst, solange der Koerper noch einfach ist) ----
    if edge:
        lines += ["", "# Rundungen/Fasen auf dem Grundkoerper (vor den Bohrungen, sonst werden Bohrungsnaehte mitgewaehlt)"]
    for f in edge:
        sel = f.get("edges", "|Z")
        if not SEL.match(str(sel)):
            unsupported.append(f"Kantenselektor {sel!r} nicht erlaubt: {f.get('type')} uebersprungen")
            continue
        op = "fillet" if f["type"] == "fillet" else "chamfer"
        lines.append(f'result = result.edges("{sel}").{op}({_n(f["size"],"size",True)})')

    # ---- Taschen ----
    if pockets:
        lines += ["", "# Taschen"]
    for f in pockets:
        face = _face(f)
        if face is None:
            unsupported.append(f"Tasche auf Flaeche {f.get('face')!r} nicht unterstuetzt (nur top/bottom)")
            continue
        if float(f.get("angle", 0) or 0) != 0:
            unsupported.append("Tasche mit Drehwinkel nicht unterstuetzt: ohne Drehung gebaut -> PRUEFEN")
        pre, ext = cutter(face, float(f["depth"]))
        lines.append(f'result = result.cut({pre}.center({_n(f["x"],"x")}, {_n(f["y"],"y")})'
                     f'.rect({_n(f["length"],"length",True)}, {_n(f["width"],"width",True)}).extrude({ext}))')

    # ---- Bohrungen ----
    if holes:
        lines += ["", "# Bohrungen"]
    for f in holes:
        face = _face(f)
        if face is None:
            unsupported.append(f"Bohrung auf Flaeche {f.get('face')!r} nicht unterstuetzt "
                               f"(@{f.get('x')},{f.get('y')}): NICHT gebaut")
            continue
        r = float(f["diameter"]) / 2
        depth = f.get("depth")
        through = f.get("through", depth is None)
        d = H if through else float(depth)
        pre, ext = cutter(face, d)
        lines.append(f'result = result.cut({pre}.center({_n(f["x"],"x")}, {_n(f["y"],"y")})'
                     f'.circle({_n(r,"diameter",True)}).extrude({ext}))')
        if f.get("counterbore_diameter"):
            pre, ext = cutter(face, float(f.get("counterbore_depth", 0)) or 1.0)
            lines.append(f'result = result.cut({pre}.center({_n(f["x"],"x")}, {_n(f["y"],"y")})'
                         f'.circle({_n(float(f["counterbore_diameter"])/2,"cb",True)}).extrude({ext}))')

    expected = {"bbox": bbox, "tol": 0.05,
                "holes": [{"x": float(f["x"]), "y": float(f["y"]), "d": float(f["diameter"])}
                          for f in holes if _face(f) is not None]}
    return "\n".join(lines) + "\n", {"warnings": warnings, "unsupported": unsupported, "expected": expected}


def execute(code):
    """Fuehrt den generierten Code aus. Der Code stammt nur aus generate() (validierte Zahlen)."""
    ns = {}
    exec(compile(code, "<generated>", "exec"), ns)
    if "result" not in ns:
        raise RuntimeError("Generierter Code hat kein 'result'")
    return ns["result"]


if __name__ == "__main__":
    import cadquery as cq
    spec = json.load(open(sys.argv[1]))
    code, info = generate(spec)
    wp = execute(code)
    os.makedirs("out", exist_ok=True)
    name = spec.get("part_name", "part")
    open(f"out/{name}_cadquery.py", "w", encoding="utf-8").write(code)
    cq.exporters.export(wp, f"out/{name}.step")
    print(code)
    print("Warnungen:", info["warnings"])
    print("NICHT UNTERSTUETZT:", info["unsupported"])
    print(f"-> out/{name}.step, out/{name}_cadquery.py")
