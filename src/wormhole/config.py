from __future__ import annotations

import os
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from dataclasses import dataclass
from pathlib import Path


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


CONFIG_PATH = _xdg_config_home() / "wormhole" / "config.toml"


def _resolve_vault_dir() -> Path:
    env = os.environ.get("WORMHOLE_VAULT")
    if env:
        return Path(env)
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            raw = tomllib.load(f)
        vault = raw.get("vault")
        if vault:
            return Path(vault)
    return Path.home() / "vault" / "wormhole"


VAULT_DIR = _resolve_vault_dir()


@dataclass
class FoldDefaults:
    include_thinking: bool = False
    include_tools: bool = False


@dataclass
class Config:
    fold: FoldDefaults

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        target = path or CONFIG_PATH
        if not target.exists():
            return cls(fold=FoldDefaults())
        with target.open("rb") as f:
            raw = tomllib.load(f)
        fold_raw = raw.get("fold", {})
        return cls(
            fold=FoldDefaults(
                include_thinking=bool(fold_raw.get("include_thinking", False)),
                include_tools=bool(fold_raw.get("include_tools", False)),
            )
        )
