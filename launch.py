import os
import sys
from pathlib import Path

if sys.version_info < (3, 11):  # tomllib; macOS's /usr/bin/python3 is 3.9
    sys.stderr.write("repetui.bar needs Python 3.11 or newer as python3 on herdr's PATH; found "
                     "%d.%d at %s\n" % (sys.version_info[0], sys.version_info[1], sys.executable))
    sys.exit(1)

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
