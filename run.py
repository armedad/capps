"""Run the c-apps dashboard on port 8000."""

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="c-apps dashboard")
    parser.add_argument(
        "--startup",
        action="store_true",
        help="start any managed app that is not already running, then serve the dashboard",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.startup:
        os.environ["CAPPS_STARTUP"] = "1"

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
