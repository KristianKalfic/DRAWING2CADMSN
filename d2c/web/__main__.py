"""python -m d2c.web [--host H] [--port P]: serve the results view."""

import argparse

import uvicorn

from d2c.log import setup
from d2c.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m d2c.web", description=__doc__, add_help=False, usage=argparse.SUPPRESS)
    options = parser.add_argument_group("Options")
    options.add_argument("-h", "--help", action="help", help="Show this help and exit")
    options.add_argument(
        "--host",
        default="127.0.0.1",
        help="Address to serve the results view on (0.0.0.0 exposes it to the network, required in a container)",
    )
    options.add_argument("--port", type=int, default=8080, help="Port for the results view")
    args = parser.parse_args()
    setup()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
