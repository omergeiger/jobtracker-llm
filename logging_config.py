import logging
import sys
from pathlib import Path

_LOG_FILE = Path(__file__).parent / "logs" / "jobtracker.log"


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    _LOG_FILE.parent.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(_LOG_FILE)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)

    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)
