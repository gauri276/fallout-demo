"""Approved local launcher: confines caches/config and binds loopback only."""

import argparse
import os
import sys

from fallout.models import ROOT
from fallout.runtime import local_runtime


def main():
    parser = argparse.ArgumentParser(description="Launch the local FALLOUT workbench.")
    parser.add_argument("--port", type=int, default=8501, help="Loopback port; use a free port to preserve an existing session.")
    parser.add_argument("--load-env", action="store_true", help="Explicitly load the private workspace .env; its values override shell settings.")
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    if options.load_env:
        from fallout.local_env import load_private_environment
        try:
            load_private_environment()
        except ValueError as exc:
            parser.error(str(exc))
    local_runtime()
    os.chdir(ROOT)
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", str(ROOT / "app.py"), "--server.address=127.0.0.1",
                "--server.port=" + str(options.port),
                "--server.headless=true", "--browser.gatherUsageStats=false", "--server.fileWatcherType=none"]
    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())