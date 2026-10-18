"""Keep third-party runtime configuration and caches inside the workspace."""

import os

from .models import ROOT


def local_runtime():
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    temporary = ROOT / ".tmp"
    temporary.mkdir(exist_ok=True)
    # Do not read company home-directory Streamlit config or credentials.
    for variable in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        os.environ[variable] = str(runtime)
    for variable in ("TMPDIR", "TEMP", "TMP"):
        os.environ[variable] = str(temporary)
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    # Streamlit installs GitPython transitively. Prevent Git executable discovery.
    os.environ["GIT_PYTHON_GIT_EXECUTABLE"] = str(runtime / "git-disabled")
    os.environ["GIT_PYTHON_REFRESH"] = "quiet"