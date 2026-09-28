"""Operator-only machine account CLI. Passwords are read from a private prompt."""
import argparse
import getpass
import hmac
import sys
import warnings

from .auth import ALLOWED_COMPANIES, RESOURCE_KEYS, MachineAuthError, MachineStore


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally includes rejected arguments, which could be a secret.
        self.exit(2, "Invalid arguments. Use --help; passwords must be entered at the private prompt.\n")


def _new_password():
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        password = getpass.getpass("Machine password: ")
        confirmation = getpass.getpass("Confirm machine password: ")
    try:
        equal = hmac.compare_digest(password.encode("utf-8"), confirmation.encode("utf-8"))
    except UnicodeError:
        equal = False
    if not equal:
        raise MachineAuthError(422, "MACHINE_PASSWORD_MISMATCH", "Passwords do not match.")
    return password


def main(argv=None):
    parser = _Parser(description="Manage separate corporate API machine credentials.")
    parser.add_argument("command", choices=("create", "update", "disable"))
    parser.add_argument("--username", required=True)
    parser.add_argument("--state-path")
    parser.add_argument("--company", action="append", choices=sorted(ALLOWED_COMPANIES), help="Repeat for each permitted company.")
    parser.add_argument("--resource", action="append", choices=sorted(RESOURCE_KEYS), help="Repeat for each permitted dataset; no all-resources default.")
    parser.add_argument("--enable", action="store_true", help="Re-enable an existing machine during a password update.")
    args = parser.parse_args(argv)
    if args.command == "create" and (not args.company or not args.resource or args.enable):
        parser.error("Creation requires explicit company and resource grants.")
    if args.command == "disable" and (args.company or args.resource or args.enable):
        parser.error("Disable accepts only the username and optional state path.")
    try:
        password = _new_password() if args.command in {"create", "update"} else None
        store = MachineStore(args.state_path)
        if args.command == "create":
            store.create_client(args.username, password, company_ids=args.company, resources=args.resource)
        elif args.command == "update":
            store.update_client(args.username, password=password, company_ids=args.company, resources=args.resource,
                                enabled=True if args.enable else None)
        else:
            store.disable_client(args.username)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("A private password prompt is required; no credential change was made.", file=sys.stderr)
        return 1
    except MachineAuthError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print("Machine account operation completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
