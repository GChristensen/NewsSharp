#!/usr/bin/env python
"""NewsSharp command line (manage.py). `reader <command>` stands for `uv run python manage.py <command>`."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
