import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from herdr_repetui.config import ConfigError, load_config  # noqa: E402
from herdr_repetui.launcher import main  # noqa: E402

if "--owner" not in sys.argv:
    try:
        owner = str(load_config(os.environ).socket)
    except (ConfigError, OSError, ValueError):
        owner = ""
    if owner:
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--owner", owner])
sys.exit(main())
