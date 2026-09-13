from pathlib import Path
import sqlite3

from backend.manage_users import main


def test_bootstrap_credentials_only_written_to_private_file(tmp_path, capsys):
    state = tmp_path / "control.sqlite3"
    output = tmp_path / ".dashboard-access.txt"
    assert main(["bootstrap-admin", "--state-path", str(state), "--output", str(output)]) == 0
    contents = output.read_text(encoding="utf-8")
    temporary = next(line.removeprefix("Temporary password: ") for line in contents.splitlines() if line.startswith("Temporary password: "))
    assert len(temporary) >= 24
    assert temporary not in capsys.readouterr().out
    with sqlite3.connect(state) as db:
        row = db.execute("SELECT username,password_hash,must_change_password FROM users").fetchone()
    assert row[0] == "admin" and row[2] == 1
    assert temporary not in row[1]
    previous = output.read_bytes()
    assert main(["bootstrap-admin", "--state-path", str(state), "--output", str(output)]) == 1
    assert output.read_bytes() == previous


def test_bootstrap_refuses_existing_users_and_removes_reserved_empty_file(tmp_path, capsys):
    state = tmp_path / "control.sqlite3"
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    assert main(["bootstrap-admin", "--state-path", str(state), "--output", str(first)]) == 0
    assert main(["bootstrap-admin", "--state-path", str(state), "--output", str(second)]) == 1
    assert first.exists() and not second.exists()
    assert "Temporary password:" not in capsys.readouterr().out
