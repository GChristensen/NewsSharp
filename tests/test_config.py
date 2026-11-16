"""Tests for loading config.yaml (tests/test_config.py)."""
import pytest
from django.core.exceptions import ImproperlyConfigured

from config.file import DEFAULTS, load_config


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "config.yaml") == DEFAULTS


def test_values_override_defaults(tmp_path):
    config = load_config(write(tmp_path, "port: 10000\ntrust_proxy: true\nproxy:\n  url: https://p.lan:3129\n"))
    assert config["port"] == 10000
    assert config["trust_proxy"] is True
    assert config["stories_per_feed"] == DEFAULTS["stories_per_feed"]
    assert config["proxy"] == {"url": "https://p.lan:3129", "verify": True}


def test_proxy_verify_accepts_false_and_ca_bundle(tmp_path):
    assert load_config(write(tmp_path, "proxy:\n  verify: false\n"))["proxy"]["verify"] is False
    (tmp_path / "proxy.pem").write_text("pem", encoding="ascii")
    assert load_config(write(tmp_path, "proxy:\n  verify: proxy.pem\n"))["proxy"]["verify"] == "proxy.pem"


@pytest.mark.parametrize(
    "text",
    [
        "prot: 10000\n",
        "port: '10000'\n",
        "port: true\n",
        "trust_proxy: 1\n",
        "proxy: https://p.lan\n",
        "proxy:\n  url: ftp://p.lan\n",
        "proxy:\n  verify: missing.pem\n",
        "proxy:\n  verify: 0\n",
        "proxy:\n  insecure: true\n",
        "- port\n",
    ],
)
def test_invalid_config_is_rejected(tmp_path, text):
    with pytest.raises(ImproperlyConfigured):
        load_config(write(tmp_path, text))
