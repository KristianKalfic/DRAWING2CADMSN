"""
Prüfung von Zeichnungs- und STEP-Daten für Drawing2CAD.

Drei unabhängige Prüfungen werden zu einem gemeinsamen Bericht in issues.json zusammengeführt:
  1. compare   : vergleicht zwei unabhängige Extraktionen (A, B)
  2. plausible : wendet deterministische Regeln auf das JSON an (ohne Sprachmodell)
  3. freecad   : öffnet die STEP-Datei in FreeCAD ohne Benutzeroberfläche und misst sie nach

Aufruf:
  python issue_check.py extraktion_A.json extraktion_B.json out/teil.step [--freecad "C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe"]
Rückgabecode 0 = freigegeben, 1 = offene Sperrfehler.
"""
import json, os, shutil, subprocess, sys, tempfile

TOL = 0.05  # mm


def issue(sev, code, msg, where=""):
    return {"severity": sev, "code": code, "message": msg, "where": where, "status": "open"}


# ---------- 1. Vergleich zweier Extraktionen ----------
def compare(a, b):
    out = []
    for k in ("length", "width", "height", "diameter"):
        va, vb = a.get("base", {}).get(k), b.get("base", {}).get(k)
        if va != vb and not (va and vb and abs(va - vb) <= TOL):
            out.append(issue("blocker", "EXTRACTION_MISMATCH",
                             f"Grundkörper {k}: A={va} im Vergleich zu B={vb}", f"base.{k}"))
    fa, fb = a.get("features", []), b.get("features", [])
    if len(fa) != len(fb):
        out.append(issue("blocker", "FEATURE_COUNT",
                         f"Anzahl der Merkmale: A={len(fa)} im Vergleich zu B={len(fb)}", "features"))
    # Gerundeter Sortierschlüssel: Kreismuster liefern x = ±1e-15 statt 0 und würden sonst
    # gleiche Bohrungen wegen unterschiedlicher Reihenfolge falsch zuordnen (Fehlalarm).
    key = lambda f: (round(float(f["x"]), 2), round(float(f["y"]), 2))
    holes_a = sorted([f for f in fa if f.get("type") == "hole"], key=key)
    holes_b = sorted([f for f in fb if f.get("type") == "hole"], key=key)
    if len(holes_a) != len(holes_b):
        out.append(issue("blocker", "HOLE_COUNT",
                         f"Anzahl der Bohrungen: A={len(holes_a)} im Vergleich zu B={len(holes_b)}", "features.hole"))
    for i, (h1, h2) in enumerate(zip(holes_a, holes_b)):
        for k in ("x", "y", "diameter"):
            if abs(h1[k] - h2[k]) > TOL:
                out.append(issue("blocker", "EXTRACTION_MISMATCH",
                                 f"Bohrung Nr. {i}, {k}: A={h1[k]} im Vergleich zu B={h2[k]}", f"features.hole[{i}].{k}"))
    return out


# ---------- 2. Plausibilitätsprüfung (deterministisch) ----------
def plausible(spec):
    out, base = [], spec.get("base", {})
    L, W, H = base.get("length"), base.get("width"), base.get("height")
    D = base.get("diameter") if base.get("shape") == "cylinder" else None
    for f in spec.get("features", []):
        if f.get("confidence", 1) < 0.7:
            out.append(issue("warning", "LOW_CONFIDENCE",
                             f"{f.get('type')} mit einer Konfidenz von nur {f['confidence']}", str(f)[:80]))
        if f.get("type") == "hole" and L and W:
            r = f["diameter"] / 2
            if abs(f["x"]) + r > L / 2 or abs(f["y"]) + r > W / 2:
                out.append(issue("blocker", "HOLE_OUTSIDE_BODY",
                                 f"Bohrung d={f['diameter']} @({f['x']},{f['y']}) ragt aus dem Grundkörper "
                                 f"(Annahme zum Nullpunkt: Mitte!)", "features.hole"))
            if f["diameter"] >= min(L, W):
                out.append(issue("blocker", "HOLE_TOO_BIG", "Bohrung ist größer als der Grundkörper", "features.hole"))
        if f.get("type") == "hole" and D:
            if (f["x"] ** 2 + f["y"] ** 2) ** 0.5 + f["diameter"] / 2 > D / 2:
                out.append(issue("blocker", "HOLE_OUTSIDE_BODY",
                                 f"Bohrung d={f['diameter']} @({f['x']},{f['y']}) ragt aus dem Zylinder "
                                 f"D={D} (Annahme zum Nullpunkt: Mitte!)", "features.hole"))
        if f.get("type") == "fillet" and L and W and f["size"] > min(L, W) / 2:
            out.append(issue("blocker", "FILLET_TOO_BIG", "Der Radius ist größer als die halbe Kantenlänge", "features.fillet"))
    holes = [f for f in spec.get("features", []) if f.get("type") == "hole"]
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            d = ((holes[i]["x"] - holes[j]["x"]) ** 2 + (holes[i]["y"] - holes[j]["y"]) ** 2) ** 0.5
            if d < (holes[i]["diameter"] + holes[j]["diameter"]) / 2:
                out.append(issue("blocker", "HOLES_OVERLAP", f"Bohrungen {i} und {j} überschneiden sich", "features.hole"))
    for r in spec.get("review", []):
        out.append(issue("review", "ASSUMPTION", f"{r.get('feature')}={r.get('value')}: {r.get('reason')}", "review"))
    return out


# ---------- 3. FreeCAD-Nachmessung ----------
FC_SCRIPT = r'''
import json, os, Part
p = os.environ["D2C_STEP"]
s = Part.read(p)
bb = s.BoundBox
res = {"valid": s.isValid(), "solids": len(s.Solids), "volume": s.Volume,
       "bbox": [bb.XLength, bb.YLength, bb.ZLength],
       "holes": []}
# Nur konkave Zylinder mit Z-paralleler Achse; sonst würden Eckenrundungen als Bohrungen gezählt.
for f in s.Faces:
    c = f.Surface
    if c.__class__.__name__ != "Cylinder" or abs(abs(c.Axis.z) - 1) > 1e-6:
        continue
    u0, u1, v0, v1 = f.ParameterRange
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    p, n = f.valueAt(u, v), f.normalAt(u, v)
    if n.x * (p.x - c.Center.x) + n.y * (p.y - c.Center.y) < 0:
        res["holes"].append({"d": 2 * c.Radius, "x": c.Center.x, "y": c.Center.y})
json.dump(res, open(os.environ["D2C_FC_OUT"], "w", encoding="utf-8"))
'''


def freecad(step, spec, fc_exe):
    out = []
    if not os.path.isfile(step):
        return [issue("blocker", "STEP_MISSING", f"STEP-Datei nicht gefunden: {step}", step)]
    exe = fc_exe if os.path.isfile(fc_exe) else shutil.which(fc_exe)
    if not exe:
        return [issue("blocker", "FREECAD_NOT_FOUND",
                      f"FreeCAD wurde unter {fc_exe!r} nicht gefunden. Installieren Sie FreeCAD oder geben Sie den Pfad an: "
                      f"--freecad \"C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe\". "
                      f"Ohne diese unabhängige Prüfung wird keine Freigabe erteilt.", step)]
    with tempfile.TemporaryDirectory() as d:
        sp, op = os.path.join(d, "s.py"), os.path.join(d, "r.json")
        open(sp, "w", encoding="utf-8").write(FC_SCRIPT)
        env = dict(os.environ, D2C_STEP=os.path.abspath(step), D2C_FC_OUT=op)
        try:
            p = subprocess.run([exe, sp], env=env, timeout=120, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            return [issue("blocker", "FREECAD_FAILED", "FreeCAD-Prüfung nach 120 s abgebrochen", step)]
        except OSError as e:
            return [issue("blocker", "FREECAD_FAILED", f"FreeCAD konnte nicht gestartet werden: {e}", step)]
        if not os.path.isfile(op):
            tail = ((p.stderr or "") + (p.stdout or ""))[-500:]
            return [issue("blocker", "FREECAD_FAILED",
                          f"FreeCAD lieferte kein Ergebnis (Rückgabecode {p.returncode}): {tail}", step)]
        r = json.load(open(op, encoding="utf-8"))
    if not r["valid"] or r["solids"] != 1:
        out.append(issue("blocker", "STEP_INVALID", f"STEP-Prüfung fehlgeschlagen (valid={r['valid']}, solids={r['solids']})", step))
    b = spec.get("base", {})
    soll = sorted([b.get("length", 0), b.get("width", 0), b.get("height", 0)])
    ist = sorted(r["bbox"])
    if b.get("shape", "box") == "box":
        for s_, i_ in zip(soll, ist):
            if abs(s_ - i_) > TOL:
                out.append(issue("blocker", "BBOX_MISMATCH", f"Sollmaße {soll} stimmen nicht mit den FreeCAD-Messwerten {ist} überein", step))
                break
    for h in [f for f in spec.get("features", []) if f.get("type") == "hole"]:
        ok = any(abs(c["d"] - h["diameter"]) <= TOL and abs(c["x"] - h["x"]) <= TOL and abs(c["y"] - h["y"]) <= TOL
                 for c in r["holes"])
        if not ok:
            out.append(issue("blocker", "HOLE_NOT_FOUND",
                             f"Bohrung d={h['diameter']} @({h['x']},{h['y']}) nicht im STEP gefunden", step))
    return out


if __name__ == "__main__":
    from json_builder import normalize  # Musterobjekte (pattern) zu einzelnen Bohrungen erweitern
    a, b = normalize(json.load(open(sys.argv[1], encoding="utf-8")))[0], normalize(json.load(open(sys.argv[2], encoding="utf-8")))[0]
    step = sys.argv[3]
    fc = sys.argv[sys.argv.index("--freecad") + 1] if "--freecad" in sys.argv else "FreeCADCmd"
    issues = compare(a, b) + plausible(a) + freecad(step, a, fc)
    blockers = [i for i in issues if i["severity"] == "blocker"]
    report = {"step": step, "released": not blockers, "counts": {
        s: len([i for i in issues if i["severity"] == s]) for s in ("blocker", "warning", "review")},
        "issues": issues}
    path = os.path.splitext(step)[0] + "_issues.json"
    json.dump(report, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(report["counts"]), "->", "FREIGEGEBEN" if report["released"] else "GESPERRT", path)
    sys.exit(0 if report["released"] else 1)
