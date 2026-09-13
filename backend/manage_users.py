"""Explicit first-admin bootstrap. Never prints or embeds a password."""
import argparse
import os
from pathlib import Path

if __package__:
    from .control_store import ControlError, ControlStore
else:
    from control_store import ControlError, ControlStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tạo quản trị viên đầu tiên cho dashboard nội bộ.")
    parser.add_argument("command", choices=["bootstrap-admin"])
    parser.add_argument("--username", default="admin")
    parser.add_argument("--display-name", default="Quản trị hệ thống")
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent.parent / ".dashboard-access.txt"))
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    # Reserve the destination before creating an account. An existing access
    # file is never silently overwritten and a password is never sent to stdout.
    descriptor = None
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        result = ControlStore(args.state_path).bootstrap_admin(args.username, args.display_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write("Dashboard internal account\n")
            handle.write(f"Username: {result['user']['username']}\n")
            handle.write(f"Temporary password: {result['temporary_password']}\n")
            handle.write("Change this password at first login. Keep this file private.\n")
        print("Created first administrator. Credentials saved to the requested private access file.")
        return 0
    except FileExistsError:
        print("Access file already exists; choose another private --output path.")
        return 1
    except ControlError as exc:
        print(exc.message)
        return 1
    finally:
        if descriptor is not None:
            os.close(descriptor)
            output.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
