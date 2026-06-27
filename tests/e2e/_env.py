"""Load tests/config.env (gitignored — your values) into os.environ, so the harness ships with no
hardcoded workspace specifics. Already-set env vars take precedence (file uses setdefault), so an
inline `WORKSHOP_X=... python3 run.py` still overrides a line in the file."""
import os

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.env")


def load_config_env():
    if not os.path.exists(_CONFIG):
        return
    for raw in open(_CONFIG):
        line = raw.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                os.environ.setdefault(k, v)
