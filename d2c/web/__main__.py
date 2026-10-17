"""python -m d2c.web [--host H] [--port P]: Ergebnisansicht bereitstellen."""

import argparse

import uvicorn

from d2c.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m d2c.web", description=__doc__,
                                     add_help=False, usage=argparse.SUPPRESS)
    options = parser.add_argument_group("Optionen")
    options.add_argument("-h", "--help", action="help", help="Diese Hilfe anzeigen und beenden")
    options.add_argument("--host", default="0.0.0.0", help="Adresse zum Bereitstellen der Ergebnisansicht")
    options.add_argument("--port", type=int, default=8080, help="Port für die Ergebnisansicht")
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
