"""durweg-audit: compare a Durweg run with the agent's session transcripts (optional plugin).

Finds the Durweg platform it checks against, in this order: $DURWEG_PLATFORM (a folder with
durweg/), the platform folder in ~/Durweg/.install.json (written by Durweg's installer), or an
importable `durweg` package. Supported platform: Durweg 1.x (run format 1).
"""
import json
import os
import sys
from pathlib import Path

__version__ = "1.0.0"
SUPPORTED_MAJOR = "1."


class PlatformNotFound(Exception):
    pass


def locate_platform():
    candidates = []
    if os.environ.get("DURWEG_PLATFORM"):
        candidates.append(Path(os.environ["DURWEG_PLATFORM"]))
    home = Path(os.environ.get("DURWEG_HOME") or Path.home() / "Durweg")
    manifest = home / ".install.json"
    if manifest.is_file():
        try:
            candidates.append(Path(json.loads(manifest.read_text(encoding="utf-8"))["platform_dir"]))
        except (OSError, ValueError, KeyError):
            pass
    for c in candidates:
        if (c / "durweg" / "__init__.py").is_file():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            break
    try:
        import durweg
    except ImportError:
        raise PlatformNotFound("the Durweg platform was not found: install Durweg, or set DURWEG_PLATFORM "
                               "to its folder")
    if not durweg.__version__.startswith(SUPPORTED_MAJOR):
        raise PlatformNotFound(f"durweg-audit {__version__} supports Durweg {SUPPORTED_MAJOR}x, "
                               f"found {durweg.__version__}")
    return durweg
