"""One Keyword radar tick, run by Windows Task Scheduler (every 2 minutes; skips itself when it is not time yet).

Run by hand: .venv/Scripts/python scripts/radar_tick.py [--force]
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yt_niche import config as cfg  # noqa: E402
from yt_niche import settings  # noqa: E402
from yt_niche.db.connection import connect  # noqa: E402
from yt_niche.services import radar  # noqa: E402


def main() -> None:
    settings.init()
    conn = connect()
    try:
        result = radar.tick(conn, force="--force" in sys.argv)
    except Exception:
        log = cfg.DATA_DIR / "radar_errors.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{traceback.format_exc()}\n")
        raise
    if sys.stdout:
        print(result)


if __name__ == "__main__":
    main()
