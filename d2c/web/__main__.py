"""python -m d2c.web [--host H] [--port P]: serve the results browser."""

import argparse

import uvicorn

from d2c.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m d2c.web", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
