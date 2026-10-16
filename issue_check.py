"""
Issue-Kontrolle fuer Drawing2CAD (ungetestet, bitte lokal ausprobieren).

Drei unabhaengige Pruefungen -> eine gemeinsame issues.json
  1. compare   : zwei unabhaengige Opus-Extraktionen (A, B) werden verglichen
  2. plausible : deterministische Regeln auf dem JSON (kein LLM)
  3. freecad   : STEP wird in FreeCAD (headless) geoeffnet und nachgemessen

Aufruf:
  python issue_check.py extraktion_A.json extraktion_B.json out/teil.step [--freecad "C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe"]
Exit-Code 0 = freigegeben, 1 = offene Blocker.
"""
import json, os, subprocess, sys, tempfile

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
                             f"Grundkoerper {k}: A={va} vs B={vb}", f"base.{k}"))
    fa, fb = a.get("features", []), b.get("features", [])
    if len(fa) != len(fb):
        out.append(issue("blocker", "FEATURE_COUNT",
                         f"Anzahl Features: A={len(fa)} vs B={len(fb)}", "features"))
    holes_a = sorted([f for f in fa if f.get("type") == "hole"], key=lambda f: (f["x"], f["y"]))
    holes_b = sorted([f for f in fb if f.get("type") == "hole"], key=lambda f: (f["x"], f["y"]))
    for i, (h1, h2) in enumerate(zip(holes_a, holes_b)):
        for k in ("x", "y", "diameter"):
            if abs(h1[k] - h2[k]) > TOL:
                out.append(issue("blocker", "EXTRACTION_MISMATCH",
                                 f"Bohrung #{i} {k}: A={h1[k]} vs B={h2[k]}", f"features.hole[{i}].{k}"))
    return out


# ---------- 2. Plausibilitaet (deterministisch) ----------
def plausible(spec):
    out, base = [], spec.get("base", {})
    L, W, H = base.get("length"), base.get("width"), base.get("height")
    for f in spec.get("features", []):
        if f.get("confidence", 1) < 0.7:
            out.append(issue("warning", "LOW_CONFIDENCE",
                             f"{f.get('type')} nur Konfidenz {f['confidence']}", str(f)[:80]))
        if f.get("type") == "hole" and L and W:
            r = f["diameter"] / 2
            if abs(f["x"]) + r > L / 2 or abs(f["y"]) + r > W / 2:
                out.append(issue("blocker", "HOLE_OUTSIDE_BODY",
                                 f"Bohrung d={f['diameter']} @({f['x']},{f['y']}) ragt aus dem Koerper "
                                 f"(Nullpunkt-Annahme: Mitte!)", "features.hole"))
            if f["diameter"] >= min(L, W):
                out.append(issue("blocker", "HOLE_TOO_BIG", "Bohrung groesser als Koerper", "features.hole"))
        if f.get("type") == "fillet" and L and W and f["size"] > min(L, W) / 2:
            out.append(issue("blocker", "FILLET_TOO_BIG", "Radius > halbe Kante", "features.fillet"))
    holes = [f for f in spec.get("features", []) if f.get("type") == "hole"]
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            d = ((holes[i]["x"] - holes[j]["x"]) ** 2 + (holes[i]["y"] - holes[j]["y"]) ** 2) ** 0.5
            if d < (holes[i]["diameter"] + holes[j]["diameter"]) / 2:
                out.append(issue("blocker", "HOLES_OVERLAP", f"Bohrungen {i} und {j} ueberlappen", "features.hole"))
    for r in spec.get("review", []):
        out.append(issue("review", "ASSUMPTION", f"{r['feature']}={r['value']}: {r['reason']}", "review"))
    return out


# ---------- 3. FreeCAD-Nachmessung ----------
FC_SCRIPT = r'''
import json, os, Part
p = os.environ["D2C_STEP"]
s = Part.read(p)
bb = s.BoundBox
res = {"valid": s.isValid(), "solids": len(s.Solids), "volume": s.Volume,
       "bbox": [bb.XLength, bb.YLength, bb.ZLength],
       "holes": [{"d": 2*f.Surface.Radius, "x": f.Surface.Center.x, "y": f.Surface.Center.y}
                 for f in s.Faces if f.Surface.__class__.__name__ == "Cylinder"]}
json.dump(res, open(os.environ["D2C_FC_OUT"], "w"))
'''


def freecad(step, spec, fc_exe):
    out = []
    with tempfile.TemporaryDirectory() as d:
        sp, op = os.path.join(d, "s.py"), os.path.join(d, "r.json")
        open(sp, "w").write(FC_SCRIPT)
        env = dict(os.environ, D2C_STEP=os.path.abspath(step), D2C_FC_OUT=op)
        try:
            subprocess.run([fc_exe, sp], env=env, timeout=120, capture_output=True)
            r = json.load(open(op))
        except Exception as e:
            return [issue("blocker", "FREECAD_FAILED", f"FreeCAD konnte STEP nicht pruefen: {e}", step)]
    if not r["valid"] or r["solids"] != 1:
        out.append(issue("blocker", "STEP_INVALID", f"valid={r['valid']} solids={r['solids']}", step))
    b = spec.get("base", {})
    soll = sorted([b.get("length", 0), b.get("width", 0), b.get("height", 0)])
    ist = sorted(r["bbox"])
    if b.get("shape", "box") == "box":
        for s_, i_ in zip(soll, ist):
            if abs(s_ - i_) > TOL:
                out.append(issue("blocker", "BBOX_MISMATCH", f"Soll {soll} vs FreeCAD {ist}", step))
                break
    for h in [f for f in spec.get("features", []) if f.get("type") == "hole"]:
        ok = any(abs(c["d"] - h["diameter"]) <= TOL and abs(c["x"] - h["x"]) <= TOL and abs(c["y"] - h["y"]) <= TOL
                 for c in r["holes"])
        if not ok:
            out.append(issue("blocker", "HOLE_NOT_FOUND",
                             f"Bohrung d={h['diameter']} @({h['x']},{h['y']}) nicht im STEP gefunden", step))
    return out


if __name__ == "__main__":
    from json_builder import normalize  # pattern-Objekte zu Einzelbohrungen expandieren
    a, b = normalize(json.load(open(sys.argv[1])))[0], normalize(json.load(open(sys.argv[2])))[0]
    step = sys.argv[3]
    fc = sys.argv[sys.argv.index("--freecad") + 1] if "--freecad" in sys.argv else "FreeCADCmd"
    issues = compare(a, b) + plausible(a) + freecad(step, a, fc)
    blockers = [i for i in issues if i["severity"] == "blocker"]
    report = {"step": step, "released": not blockers, "counts": {
        s: len([i for i in issues if i["severity"] == s]) for s in ("blocker", "warning", "review")},
        "issues": issues}
    path = os.path.splitext(step)[0] + "_issues.json"
    json.dump(report, open(path, "w"), indent=2, ensure_ascii=False)
    print(json.dumps(report["counts"]), "->", "FREIGEGEBEN" if report["released"] else "GESPERRT", path)
    sys.exit(0 if report["released"] else 1)
