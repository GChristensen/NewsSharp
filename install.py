#!/usr/bin/env python3
"""Installs NewsSharp into .venv next to this script (install.py), with the dependency versions pinned in uv.lock.

    python3 install.py [--with-dev] [--without dev] [--pip]

Run it again after updating the code: without options it reinstalls the parts chosen before, which are kept in
.venv/newssharp-install.json. --with-dev adds the development tools; --without dev removes them.

With uv on PATH the environment is made with `uv sync --locked`, which also fetches a suitable Python and removes
packages that are no longer needed. Otherwise it uses venv and pip with requirements.txt and requirements-dev.txt
(exported from uv.lock by `just lock`), which needs Python 3.12 or later and does not uninstall anything.

The built frontend is committed in frontend/dist, so Node.js is not needed.

Only the standard library is used here, and the syntax is kept old, so any python3 can start the script.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
STATE = VENV / "newssharp-install.json"
WINDOWS = os.name == "nt"

PARTS = ("dev",)
MIN_PYTHON = (3, 12)
REQUIREMENTS = {None: "requirements.txt", "dev": "requirements-dev.txt"}


class InstallError(Exception):
    pass


def say(message=""):
    print(message, flush=True)


def note(message):
    say("  note: " + message)


def venv_python():
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def run(cmd, cwd=ROOT, check=True, **kwargs):
    say("> " + " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=str(cwd), **kwargs)
    if check and result.returncode != 0:
        raise InstallError("command failed with exit code {}: {}".format(result.returncode, cmd[0]))
    return result


def quiet(cmd):
    """Runs a probe; returns (ok, combined output)."""
    try:
        result = subprocess.run([str(c) for c in cmd], cwd=str(ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
    except OSError as e:
        return False, str(e)
    return result.returncode == 0, result.stdout


# --- choice of parts -------------------------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(prog="install.py", description="Install NewsSharp into .venv.")
    parser.add_argument("--with-dev", action="store_true", help="pytest and ruff, for development")
    parser.add_argument("--without", action="append", default=[], metavar="NAME",
                        help="remove a part chosen before: dev")
    parser.add_argument("--pip", action="store_true", help="use venv and pip even when uv is available")
    args = parser.parse_args(argv)
    for name in args.without:
        if name.lower() not in PARTS:
            parser.error("unknown part: {} (one of {})".format(name, ", ".join(PARTS)))
    return args


def choose_parts(saved, args):
    """The saved parts plus those the options add, minus --without; in PARTS order."""
    chosen = set(p for p in saved if p in PARTS)
    if args.with_dev:
        chosen.add("dev")
    chosen -= set(name.lower() for name in args.without)
    return [p for p in PARTS if p in chosen]


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("parts", [])
    except (OSError, ValueError, AttributeError):
        return []


def save_state(parts):
    STATE.write_text(json.dumps({"parts": parts}, indent=2) + "\n", encoding="utf-8")


# --- environment -----------------------------------------------------------------------------------------------

def install_with_uv(uv, parts):
    cmd = [uv, "sync", "--locked", "--project", ROOT]
    if "dev" not in parts:
        cmd.append("--no-dev")
    run(cmd)


def python_version(python):
    ok, out = quiet([python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
    if not ok:
        return None
    major, minor = out.strip().split(".")
    return int(major), int(minor)


def install_with_pip(parts, removed):
    python = venv_python()
    if not python.exists():
        if sys.version_info[:2] < MIN_PYTHON:
            raise InstallError("Python {}.{} or later is needed, this is {}.{}. Run the script with a newer "
                               "python, or install uv, which fetches one.".format(*(MIN_PYTHON + sys.version_info[:2])))
        try:
            run([sys.executable, "-m", "venv", VENV])
        except InstallError:
            raise InstallError("could not create .venv; on Debian/Ubuntu install the python3-venv package")
    version = python_version(python)
    if version is None or version < MIN_PYTHON:
        raise InstallError(".venv has Python {}, but {}.{} or later is needed: delete .venv and run the script "
                           "with a newer python".format("%d.%d" % version if version else "?", *MIN_PYTHON))
    if not quiet([python, "-m", "pip", "--version"])[0]:  # a venv made by uv has no pip
        run([python, "-m", "ensurepip", "--upgrade"])
    cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", "-r", ROOT / REQUIREMENTS[None]]
    for part in parts:
        cmd += ["-r", ROOT / REQUIREMENTS[part]]
    run(cmd)
    if removed:
        note("pip does not uninstall: the packages of {} stay in .venv. Delete .venv and run the script again to "
             "drop them.".format(", ".join(removed)))


# --- config.yaml -----------------------------------------------------------------------------------------------

PORT_RE = re.compile(r"^port:[ \t]*(\d+)", re.M)
DEFAULT_PORT = 8000


def configured_port(text):
    match = PORT_RE.search(text)
    return int(match.group(1)) if match else DEFAULT_PORT


def prepare_config():
    """Creates config.yaml from config.example.yaml when missing. Returns (port, created)."""
    config = ROOT / "config.yaml"
    if config.exists():
        return configured_port(config.read_text(encoding="utf-8")), False
    shutil.copyfile(str(ROOT / "config.example.yaml"), str(config))
    say("Created {}. Edit the port and the other settings there.".format(config))
    return configured_port(config.read_text(encoding="utf-8")), True


# --- main ------------------------------------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    saved = load_state()
    parts = choose_parts(saved, args)
    removed = [p for p in saved if p not in parts]
    say("Installing NewsSharp into {}{}".format(VENV, " with: " + ", ".join(parts) if parts else ""))

    uv = None if args.pip else shutil.which("uv")
    if uv:
        install_with_uv(uv, parts)
    else:
        install_with_pip(parts, removed)
    save_state(parts)

    problems = []
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        problems.append("frontend/dist is missing: build it with `npm install && npm run build` in frontend/")
    port, created = prepare_config()

    for problem in problems:
        say("warning: " + problem)
    say("Done. Start the server with {}, then open http://localhost:{}{}.".format(
        "newssharp.cmd" if WINDOWS else "./newssharp.sh", port,
        " and create the administrator account" if created else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InstallError as e:
        say("error: " + str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
