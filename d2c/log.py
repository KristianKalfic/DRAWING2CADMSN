"""Protokolleinstellung für Server und Web-App: D2C_LOG wählt den Grad, Standard ist info.

Alle Meldungen gehen über logging auf stderr; stdout bleibt dem MCP-Protokoll und den Berichten vorbehalten.
`setup` wird von den Einstiegspunkten aufgerufen (d2c.server, d2c.web); Bibliotheksmodule holen sich nur
ihren Logger.
"""

import logging

from d2c.config import log_level


def setup() -> None:
    """Einen Handler auf stderr mit kurzem Format einrichten; D2C_LOG überschreibt den Grad (debug, info, warning, error)."""
    logging.basicConfig(level=log_level(), format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
