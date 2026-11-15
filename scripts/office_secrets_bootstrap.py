#!/usr/bin/env python3
"""Put named secrets into a GitHub environment without ever showing a value. The owner runs this.

    python scripts/office_secrets_bootstrap.py --repo OWNER/NAME ANTHROPIC_API_KEY
    python scripts/office_secrets_bootstrap.py --repo OWNER/NAME --check DATABASE_URL ANTHROPIC_API_KEY

You name each secret on the command line. A name is accepted only if it is on the short list below. For
each name the script prints one line with one status and nothing else:

    PRESENT             the environment already has it; it is left alone
    CREATED             it was missing and has been set
    REPLACED            it was there and you asked, by name, with --replace, to set it again
    MISSING             it is not in the environment and no value was given
    BLOCKED_PERMISSION  GitHub refused: this login cannot manage that environment's secrets
    FAILED              the GitHub command failed for another reason

A value comes from this process's environment variable of the same name. If that is empty and you pass
--prompt, the script asks for it with the screen echo off. The value is handed to ``gh secret set`` on
standard input. It is never printed, logged, written to a file or placed on a command line, and the output
of ``gh`` is never shown. ``--check`` sets nothing: it only reports PRESENT or MISSING.

It uses your own ``gh`` login. It needs no token of its own and uses the standard library only.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys

ALLOWED = ("DATABASE_URL", "OFFICE_DATABASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FMP_API_KEY",
           "MASSIVE_API_KEY")
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ENVIRONMENT = re.compile(r"^[A-Za-z0-9_-]{1,60}$")
_DENIED = ("403", "forbidden", "permission", "admin rights", "not authorized", "must have", "resource not accessible")


def gh(arguments: list[str], value: str | None = None) -> tuple[int, str, str]:
    """Run ``gh`` quietly. Its output is returned to the caller and never printed."""
    try:
        done = subprocess.run(["gh", *arguments], input=value, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 127, "", ""
    return done.returncode, done.stdout, done.stderr


def _problem(code: int, stderr: str) -> str:
    return "BLOCKED_PERMISSION" if code != 127 and any(word in stderr.lower() for word in _DENIED) else "FAILED"


def present(repo: str, environment: str, run=gh) -> set[str] | str:
    """The names of the secrets the environment has, or a status if they cannot be listed. Names only:
    GitHub never returns a secret's value."""
    code, stdout, stderr = run(["secret", "list", "--repo", repo, "--env", environment, "--json", "name"])
    if code != 0:
        return _problem(code, stderr)
    try:
        return {row["name"] for row in json.loads(stdout)}
    except (ValueError, TypeError, KeyError):
        return "FAILED"


def bootstrap(names: list[str], *, repo: str, environment: str, replace: tuple[str, ...] = (), check: bool = False,
              prompt: bool = False, environ=None, ask=getpass.getpass, run=gh) -> dict[str, str]:
    """Return one status per name. Sets a secret only when it is missing, or named in ``replace``."""
    environ = os.environ if environ is None else environ
    if not _REPO.match(repo) or not _ENVIRONMENT.match(environment):
        raise ValueError("REPOSITORY_OR_ENVIRONMENT_INVALID")
    unknown = [n for n in (*names, *replace) if n not in ALLOWED]
    if unknown or not names or len(set(names)) != len(names) or not set(replace) <= set(names):
        raise ValueError("NAME_NOT_ALLOWED")
    have = present(repo, environment, run)
    if isinstance(have, str):
        return {name: have for name in names}
    out = {}
    for name in names:
        there = name in have
        if check or (there and name not in replace):
            out[name] = "PRESENT" if there else "MISSING"
            continue
        value = environ.get(name) or ""
        if not value and prompt:
            value = ask(f"{name} (input hidden): ")
        if not value.strip():
            out[name] = "PRESENT" if there else "MISSING"
            continue
        code, _, stderr = run(["secret", "set", name, "--repo", repo, "--env", environment], value)
        value = ""
        out[name] = ("REPLACED" if there else "CREATED") if code == 0 else _problem(code, stderr)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Set named GitHub environment secrets without showing a value.")
    parser.add_argument("names", nargs="+", choices=ALLOWED, metavar="NAME", help="one of: " + ", ".join(ALLOWED))
    parser.add_argument("--repo", required=True, help="OWNER/NAME")
    parser.add_argument("--environment", default="Production")
    parser.add_argument("--replace", action="append", default=[], choices=ALLOWED, metavar="NAME",
                        help="set this one name again even though it is present; repeat for another name")
    parser.add_argument("--check", action="store_true", help="set nothing; report PRESENT or MISSING")
    parser.add_argument("--prompt", action="store_true", help="ask (screen echo off) for a value that is not in the environment")
    args = parser.parse_args(argv)
    try:
        result = bootstrap(args.names, repo=args.repo, environment=args.environment, replace=tuple(args.replace),
                           check=args.check, prompt=args.prompt)
    except ValueError as error:
        print(f"REFUSED {error.args[0]}")
        return 2
    for name in args.names:
        print(f"{name}: {result[name]}")
    return 0 if all(v in ("PRESENT", "CREATED", "REPLACED") for v in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
