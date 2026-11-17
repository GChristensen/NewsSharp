"""Tests of install.py (tests/test_install.py): the choice of parts and the pinned requirement files."""
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("install_script", ROOT / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def parts(saved, argv):
    return install.choose_parts(saved, install.parse_args(argv))


def test_choice_adds_to_saved_parts():
    assert parts([], []) == []
    assert parts([], ["--with-dev"]) == ["dev"]
    assert parts(["dev"], []) == ["dev"]
    assert parts(["unknown"], []) == []


def test_without_removes():
    assert parts(["dev"], ["--without", "dev"]) == []
    assert parts([], ["--with-dev", "--without", "DEV"]) == []


def test_bad_names_are_refused():
    with pytest.raises(SystemExit):
        install.parse_args(["--without", "gpu"])


def test_port_in_config():
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert install.configured_port(example) == int(re.search(r"^port:\s*(\d+)", example, re.M).group(1))
    assert install.configured_port("bind: 0.0.0.0\n") == install.DEFAULT_PORT


def test_groups_match_the_script():
    groups = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["dependency-groups"]
    assert set(groups) == set(install.PARTS)


def pins(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;]+)", text, re.M))


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def test_requirements_follow_the_lock():
    """requirements*.txt are exported from uv.lock (just lock); pip installs must get the same versions."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {normalize(p["name"]): p["version"] for p in lock["package"] if p["name"] != "newssharp"}
    pinned = {}
    for name in install.REQUIREMENTS.values():
        for package, version in pins(name).items():
            assert pinned.setdefault(normalize(package), version) == version, package
    assert pinned == locked
