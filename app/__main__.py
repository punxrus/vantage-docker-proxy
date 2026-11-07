"""Entry point.

    python -m app                 run the server (HOST/PORT env, default 0.0.0.0:8080)
    python -m app check [FILE]    validate a config and print problems
    python -m app hash-password   print a bcrypt hash for the `auth:` block
    python -m app flatten [FILE]  print the config with all !include files inlined
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path


def _serve() -> None:
    import uvicorn

    from .logging_setup import setup_logging

    setup_logging()
    uvicorn.run(
        "app.main:app_factory",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


def _check(path: Path) -> int:
    from .config import ConfigError, load_dotenv
    from .pages import load
    from .registry import Registry
    from .runtime import Paths, Runtime

    load_dotenv(Path(".env"))
    registry = Registry()
    paths = Paths(path, Path("assets"), Path("data"))
    registry.discover(paths.plugin_dir)
    runtime = Runtime(paths, disk_cache=False)
    try:
        loaded = load(path, registry, runtime)
    except ConfigError as exc:
        print(f"{path}: INVALID")
        for e in exc.errors:
            print(f"  - {e}")
        return 1
    print(f"{path}: OK ({len(loaded.pages)} pages: {', '.join(p.name for p in loaded.pages)})")
    for w in loaded.warnings:
        print(f"  ! {w}")
    return 2 if loaded.warnings else 0


def _flatten(path: Path) -> int:
    import yaml

    from .config import load_yaml_tree

    class Dumper(yaml.SafeDumper):
        pass

    def text(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
        style = "|" if "\n" in value else None  # keep templates readable
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    Dumper.add_representer(str, text)
    yaml.dump(load_yaml_tree(path), sys.stdout, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=120)
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "serve"
    target = Path(argv[1]) if len(argv) > 1 else Path(os.environ.get("VANTAGE_CONFIG", "config/vantage.yml"))
    if cmd == "serve":
        _serve()
        return 0
    if cmd == "check":
        return _check(target)
    if cmd == "flatten":
        return _flatten(target)
    if cmd == "hash-password":
        from .auth import hash_password

        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Again: "):
            print("passwords differ", file=sys.stderr)
            return 1
        print(hash_password(pw))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
