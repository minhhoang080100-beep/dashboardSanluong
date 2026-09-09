"""Compatibility entry point; use python -m backend.inspect_database --help."""
if __package__:
    from .inspect_database import main
else:
    from inspect_database import main

if __name__ == "__main__":
    raise SystemExit(main(default_mode="methods"))
