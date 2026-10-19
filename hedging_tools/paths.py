"""Shared checkout paths; generated files never live beside source modules."""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
CONFIG_PATH = PACKAGE_DIR / 'config' / 'default.json'
RUNTIME_DIR = PROJECT_ROOT / 'runtime'
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
