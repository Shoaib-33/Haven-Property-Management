"""Small, dependency-free loader for local environment settings."""
from __future__ import annotations

import os
from pathlib import Path


def load_local_env() -> None:
    """Load project-root .env values without overriding deployment environment."""
    env_file = Path(__file__).resolve().parent.parent / '.env'
    try:
        lines = env_file.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not key or not key.replace('_', '').isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        os.environ.setdefault(key, value)
