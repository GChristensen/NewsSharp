"""Loading and validation of config.yaml (config/file.py). The file is optional: every key has a default,
and a missing file gives the defaults. Unknown keys and values of the wrong type stop the process, so a
typo is reported instead of silently ignored."""
from pathlib import Path

import yaml
from django.core.exceptions import ImproperlyConfigured

DEFAULTS = {
    "port": 8000,
    "bind": "0.0.0.0",
    "data_dir": "./data",
    "trust_proxy": False,
    "fetch_interval_minutes": 60,
    "stories_per_feed": 500,
    "worker_processes": 2,
    "push_lease_seconds": 2147483647,
    "image_cache_mb": 0,
    "log_level": "info",
    "proxy": {
        "url": None,
        "verify": True,
    },
}

PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")


def resolve_path(base_dir: Path, value: str) -> Path:
    """A path from the config, relative to the installation directory unless absolute."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def _fail(path: Path, message: str):
    raise ImproperlyConfigured(f"{path}: {message}")


def _check_type(path: Path, key: str, value, default):
    # bool is a subclass of int, so it is checked first: `port: true` is not a port.
    if isinstance(default, bool):
        if not isinstance(value, bool):
            _fail(path, f"{key} must be true or false")
    elif isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            _fail(path, f"{key} must be an integer")
    elif isinstance(default, str):
        if not isinstance(value, str) or not value:
            _fail(path, f"{key} must be a non-empty string")


def _merge_proxy(path: Path, section) -> dict:
    proxy = dict(DEFAULTS["proxy"])
    if section is None:
        return proxy
    if not isinstance(section, dict):
        _fail(path, "proxy must be a mapping")
    unknown = set(section) - set(proxy)
    if unknown:
        _fail(path, f"unknown key(s) in proxy: {', '.join(sorted(map(str, unknown)))}")
    proxy.update(section)

    url = proxy["url"]
    if url is not None:
        if not isinstance(url, str) or not url.lower().startswith(PROXY_SCHEMES):
            _fail(path, f"proxy.url must start with one of {', '.join(PROXY_SCHEMES)}")

    verify = proxy["verify"]
    if isinstance(verify, str):
        if not verify:
            _fail(path, "proxy.verify must be true, false or the path of a CA bundle")
    elif not isinstance(verify, bool):
        _fail(path, "proxy.verify must be true, false or the path of a CA bundle")
    return proxy


def load_config(path: Path) -> dict:
    """The settings from `path` merged over DEFAULTS."""
    config = {key: value for key, value in DEFAULTS.items() if key != "proxy"}
    data = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as error:
            _fail(path, f"invalid YAML: {error}")
        if not isinstance(data, dict):
            _fail(path, "the top level must be a mapping")

    unknown = set(data) - set(DEFAULTS)
    if unknown:
        _fail(path, f"unknown key(s): {', '.join(sorted(map(str, unknown)))}")
    for key, value in data.items():
        if key == "proxy":
            continue
        _check_type(path, key, value, DEFAULTS[key])
        config[key] = value

    config["proxy"] = _merge_proxy(path, data.get("proxy"))
    verify = config["proxy"]["verify"]
    if isinstance(verify, str) and not resolve_path(path.parent, verify).is_file():
        _fail(path, f"proxy.verify: CA bundle not found: {verify}")
    return config
