"""Single-instance tests: the lock, socket commands, and a safe client."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import instance as instance_module
from pastex.instance import SingleInstance, send_command


def test_only_one_instance_claims_the_slot(tmp_path):
    first = SingleInstance(lambda command: None, directory=tmp_path)
    second = SingleInstance(lambda command: None, directory=tmp_path)

    assert first.claim() is True
    assert second.claim() is False

    first.stop()
    assert second.claim() is True
    second.stop()


def test_command_round_trip(tmp_path):
    seen = []
    server = SingleInstance(seen.append, directory=tmp_path)
    assert server.claim() is True
    assert server.serve() is True
    try:
        reply = send_command("show", directory=tmp_path)
    finally:
        server.stop()

    assert reply == {"ok": True}
    assert seen == ["show"]


def test_every_command_is_forwarded(tmp_path):
    seen = []
    server = SingleInstance(seen.append, directory=tmp_path)
    server.claim()
    server.serve()
    try:
        for command in ("show", "pick", "quit"):
            assert send_command(command, directory=tmp_path) == {"ok": True}
    finally:
        server.stop()

    assert seen == ["show", "pick", "quit"]


def test_send_command_without_a_server_returns_none(tmp_path):
    assert send_command("show", directory=tmp_path) is None


def test_stop_removes_the_socket(tmp_path):
    server = SingleInstance(lambda command: None, directory=tmp_path)
    server.claim()
    server.serve()
    assert server.socket_path.exists()

    server.stop()

    assert not server.socket_path.exists()
    assert send_command("show", directory=tmp_path) is None


def test_invalid_payload_is_rejected(tmp_path):
    server = SingleInstance(lambda command: None, directory=tmp_path)

    assert server.process(b"not json") == {"ok": False, "error": "invalid request"}
    assert server.process(b'{"command": "delete-all"}') == {
        "ok": False,
        "error": "unknown command: delete-all",
    }


def test_callback_error_is_reported_not_raised(tmp_path):
    def boom(command):
        raise RuntimeError("failed")

    server = SingleInstance(boom, directory=tmp_path)

    assert server.process(b'{"command": "show"}') == {"ok": False, "error": "failed"}


def test_send_command_validates_the_command():
    try:
        send_command("rm -rf")
    except ValueError as exc:
        assert "unknown command" in str(exc)
    else:  # pragma: no cover - the guard must exist
        raise AssertionError("send_command must reject unknown commands")


def test_runtime_dir_honours_xdg_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert instance_module.runtime_dir() == tmp_path / "pastex"
