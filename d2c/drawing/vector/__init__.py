"""Exakte Messungen anhand der Vektorgeometrie technischer PDF-Zeichnungen.

Viele Zeichnungen sind Vektor-PDFs aus CAD-Exporten: Kreise und Bögen liegen als Bézierkurven oder
aus Linien angenäherte Polylinien vor. Durchmesser, Radien und Kantenlängen lassen sich daher aus der Geometrie
ermitteln, ohne gedruckte Zahlen abzulesen. So wird ein in der Zeichnung fehlendes Maß bestimmt, ein aus dem Bild
gelesenes Maß geprüft und ein falscher Maßstab im Schriftfeld erkennbar.

Seiten sind 1-basiert. Ein Bereich ist (x0, y0, x1, y1) als Anteile der angezeigten Seite, Ursprung oben links,
wie beim Zuschnitt des Seitendarstellers. Ergebnisse sind Modell-mm (Blatt-mm geteilt durch den Maßstab),
x zeigt nach rechts, y nach oben. Der Ursprung liegt links unten im Bereich oder, wenn kein Bereich angegeben ist,
links unten auf dem Blatt.

Module: primitives (Grundelemente), chains (Ketten), fit (Anpassung), contours (Konturen, Strichklassen),
views (Messung einer Seite), match (Abgleich gedruckter Maße)."""

from d2c.drawing.vector.match import STANDARD_SCALES, suggest_scale, verify
from d2c.drawing.vector.views import extract

__all__ = ["STANDARD_SCALES", "extract", "suggest_scale", "verify"]
