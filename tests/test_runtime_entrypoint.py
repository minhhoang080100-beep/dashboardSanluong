"""Simulate container privilege/mount setup without changing the host filesystem or UID."""
from pathlib import PurePosixPath
import stat
import sys
from types import SimpleNamespace

import pytest

from backend import runtime_entrypoint


class Executed(Exception):
    pass


@pytest.fixture
def runtime(monkeypatch):
    events = []
    config = {
        "uid": 0, "writable": True,
        "existing": {"/data", "/app/.data", "/data/control.sqlite3", "/data/control.sqlite3-wal"},
        "symlinks": set(), "resolved": {}, "links": {}, "nonfiles": set(),
    }

    class FakePath:
        def __init__(self, value):
            self.value = PurePosixPath(str(value))

        def __str__(self):
            return str(self.value)

        def __eq__(self, other):
            return str(self) == str(other)

        def __truediv__(self, part):
            return FakePath(self.value / part)

        @property
        def parent(self):
            return FakePath(self.value.parent)

        @property
        def name(self):
            return self.value.name

        def is_absolute(self):
            return self.value.is_absolute()

        def is_symlink(self):
            return str(self) in config["symlinks"]

        def resolve(self):
            return FakePath(config["resolved"].get(str(self), str(self)))

        def exists(self):
            return str(self) in config["existing"]

        def is_dir(self):
            return self.exists() and str(self) in {"/data", "/app/.data"}

        def mkdir(self, **kwargs):
            events.append(("mkdir", str(self), kwargs))
            config["existing"].add(str(self))

        def stat(self):
            mode = stat.S_IFDIR if str(self) in config["nonfiles"] else stat.S_IFREG
            return SimpleNamespace(st_mode=mode, st_nlink=config["links"].get(str(self), 1))

    def setuid(uid):
        events.append(("setuid", uid))
        config["uid"] = uid

    def access(path, mode):
        events.append(("access", str(path), config["uid"]))
        return config["writable"]

    def execute(command, args):
        events.append(("exec", command, args, config["uid"]))
        raise Executed()

    env = {"DASHBOARD_STATE_PATH": "/data/control.sqlite3"}
    fake_os = SimpleNamespace(
        name="posix", environ=env, W_OK=2,
        getuid=lambda: config["uid"],
        chown=lambda path, uid, gid: events.append(("chown", str(path), uid, gid)),
        chmod=lambda path, mode: events.append(("chmod", str(path), mode)),
        setgroups=lambda groups: events.append(("setgroups", groups)),
        setgid=lambda gid: events.append(("setgid", gid)), setuid=setuid,
        access=access, umask=lambda mode: events.append(("umask", mode)), execvp=execute,
    )
    monkeypatch.setattr(runtime_entrypoint, "os", fake_os)
    monkeypatch.setattr(runtime_entrypoint, "Path", FakePath)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=10001, pw_gid=10001)))
    monkeypatch.setattr(sys, "argv", ["runtime_entrypoint.py", "uvicorn", "main:app"])
    return env, config, events


@pytest.mark.parametrize("mount", [None, "/other", "/data/subdir"])
def test_railway_missing_or_wrong_mount_refused_before_mkdir(runtime, mount):
    env, _, events = runtime
    env["RAILWAY_PROJECT_ID"] = "synthetic-project"
    if mount is not None:
        env["RAILWAY_VOLUME_MOUNT_PATH"] = mount
    with pytest.raises(SystemExit):
        runtime_entrypoint.main()
    assert not any(event[0] in {"mkdir", "chown", "setuid", "exec"} for event in events)


def test_railway_valid_mount_drops_privileges_before_exec(runtime):
    env, _, events = runtime
    env.update(RAILWAY_PROJECT_ID="synthetic-project", RAILWAY_VOLUME_MOUNT_PATH="/data")
    with pytest.raises(Executed):
        runtime_entrypoint.main()
    steps = [event[0] for event in events]
    assert steps.index("setgroups") < steps.index("setgid") < steps.index("setuid") < steps.index("exec")
    assert events[-1] == ("exec", "uvicorn", ["uvicorn", "main:app"], 10001)
    assert ("access", "/data", 10001) in events
    assert ("umask", 0o077) in events
    owned = [event[1] for event in events if event[0] == "chown"]
    assert owned == ["/data", "/data/control.sqlite3", "/data/control.sqlite3-wal"]


@pytest.mark.parametrize("path", ["control.sqlite3", "/tmp/control.sqlite3", "/data/another.sqlite3", "/data/sub/control.sqlite3", "/app/.data/../control.sqlite3"])
def test_unsafe_state_paths_never_change_permissions(runtime, path):
    env, _, events = runtime
    env["DASHBOARD_STATE_PATH"] = path
    with pytest.raises(SystemExit):
        runtime_entrypoint.main()
    assert events == []


@pytest.mark.parametrize("path", ["/data", "/data/control.sqlite3", "/data/control.sqlite3-wal"])
def test_symlink_state_paths_are_refused_without_executing(runtime, path):
    _, config, events = runtime
    config["symlinks"].add(path)
    with pytest.raises(SystemExit):
        runtime_entrypoint.main()
    assert not any(event[0] == "exec" for event in events)
    assert not any(event[0] == "chown" and event[1] == path for event in events)


@pytest.mark.parametrize("kind", ["multiple_links", "not_regular_file"])
def test_state_files_cannot_be_hardlinks_or_special_files(runtime, kind):
    _, config, events = runtime
    if kind == "multiple_links":
        config["links"]["/data/control.sqlite3"] = 2
    else:
        config["nonfiles"].add("/data/control.sqlite3")
    with pytest.raises(SystemExit):
        runtime_entrypoint.main()
    assert not any(event[0] == "exec" for event in events)
    assert not any(event[0] == "chown" and event[1] == "/data/control.sqlite3" for event in events)


def test_nonroot_keeps_identity_and_requires_writable_directory(runtime):
    _, config, events = runtime
    config["uid"] = 10001
    with pytest.raises(Executed):
        runtime_entrypoint.main()
    assert not any(event[0] in {"chown", "chmod", "setuid", "setgid", "setgroups"} for event in events)
    assert events[-1][-1] == 10001
    events.clear()
    config["writable"] = False
    with pytest.raises(SystemExit):
        runtime_entrypoint.main()
    assert not any(event[0] == "exec" for event in events)


def test_local_container_does_not_require_railway_environment(runtime):
    env, _, events = runtime
    env["DASHBOARD_STATE_PATH"] = "/app/.data/control.sqlite3"
    with pytest.raises(Executed):
        runtime_entrypoint.main()
    assert events[-1][-1] == 10001
