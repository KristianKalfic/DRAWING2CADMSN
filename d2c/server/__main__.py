"""python -m d2c.server [--http HOST:PORT]: MCP-Server über stdio oder HTTP starten."""

import argparse

from d2c.log import setup
from d2c.server.tools import server

DESCRIPTION = "Start the MCP server over stdio or HTTP."


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m d2c.server", description=DESCRIPTION, add_help=False)
    parser.add_argument("-h", "--help", action="help", help="show this help and exit")
    parser.add_argument("--http", metavar="HOST:PORT", help="serve streamable HTTP at /mcp instead of stdio")
    args = parser.parse_args()
    setup()
    if args.http is None:
        server.run("stdio")
        return
    host, _, port = args.http.rpartition(":")
    if not host or not port.isdigit():
        parser.error("--http expects HOST:PORT, for example 0.0.0.0:8765")
    # Auf 127.0.0.1 oder localhost akzeptiert mcp nur diese beiden Host-Header; im Container an 0.0.0.0 binden.
    server.run("streamable-http", host=host, port=int(port))


if __name__ == "__main__":
    main()
