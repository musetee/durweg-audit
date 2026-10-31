"""durweg-audit RUN --transcript FILE...  (exit 0 PASS, 1 VIOLATION, 2 INSUFFICIENT, 4 setup problem)"""
import argparse
import sys
from pathlib import Path


def main(argv=None):
    from . import PlatformNotFound, locate_platform
    ap = argparse.ArgumentParser(prog="durweg-audit", description=__doc__)
    ap.add_argument("run")
    ap.add_argument("--transcript", nargs="+", required=True, metavar="FILE")
    a = ap.parse_args(argv)
    try:
        locate_platform()
    except PlatformNotFound as e:
        print(f"durweg-audit: {e}", file=sys.stderr)
        return 4
    from durweg.util import WfError
    from .audit import audit
    try:
        return audit(Path(a.run).expanduser().resolve(), [Path(t).expanduser() for t in a.transcript])
    except WfError as e:
        print(str(e), file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
