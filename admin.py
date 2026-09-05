"""Local moderation commands. Run on the server host, never through HTTP."""
import argparse
import getpass
import json
import sys

from msa_server import moderation


def main(argv=None):
    parser = argparse.ArgumentParser(description="Administración local de baneos MSA")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("ban", "unban", "status", "list"):
        command = commands.add_parser(action)
        if action != "list":
            command.add_argument("player", help="ID público exacto msa-...")
        if action in {"ban", "unban"}:
            command.add_argument("--reason", required=True)
            command.add_argument("--operator", default=getpass.getuser())
    args = parser.parse_args(argv)
    try:
        result = moderation.administer(args.action, getattr(args, "player", None),
                                       getattr(args, "reason", ""),
                                       getattr(args, "operator", ""))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"No se aplicó la operación: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
