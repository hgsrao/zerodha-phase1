from pathlib import Path

import pytest

from local_review_runner import (
    BACKENDS,
    ReviewRunnerError,
    call_ollama,
    parse_args,
    parse_file_request,
    validate_and_read_files,
)


def test_reads_file_inside_project(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text("safe", encoding="utf-8")
    result = validate_and_read_files(tmp_path, ["README.md"])
    assert result[0].content == "safe"
    assert result[0].relative_path == "README.md"


def test_allows_powershell_launcher(tmp_path: Path) -> None:
    source = tmp_path / "start_engine.ps1"
    source.write_text("Write-Host 'safe'", encoding="utf-8")
    result = validate_and_read_files(tmp_path, ["start_engine.ps1"])
    assert result[0].content == "Write-Host 'safe'"


def test_blocks_parent_traversal(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(ReviewRunnerError, match="outside the project root"):
        validate_and_read_files(project, ["../outside.md"])


def test_blocks_sensitive_file(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret", encoding="utf-8")
    with pytest.raises(ReviewRunnerError, match="Sensitive file is blocked"):
        validate_and_read_files(tmp_path, [".env"])


def test_blocks_more_than_three_files(tmp_path: Path) -> None:
    names = []
    for index in range(4):
        name = f"file{index}.md"
        (tmp_path / name).write_text("safe", encoding="utf-8")
        names.append(name)
    with pytest.raises(ReviewRunnerError, match="No more than 3"):
        validate_and_read_files(tmp_path, names)


def test_blocks_duplicate_file(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text("safe", encoding="utf-8")
    with pytest.raises(ReviewRunnerError, match="Duplicate file"):
        validate_and_read_files(tmp_path, ["README.md", "README.md"])


def test_reads_only_requested_line_range(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    result = validate_and_read_files(tmp_path, ["module.py:2-3"])
    assert result[0].content == "two\nthree\n"
    assert result[0].relative_path == "module.py#L2-L3"


def test_allows_distinct_ranges_from_same_file(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    result = validate_and_read_files(tmp_path, ["module.py:1-2", "module.py:3-4"])
    assert [item.content for item in result] == ["one\ntwo\n", "three\nfour\n"]


def test_rejects_reversed_or_oversized_range() -> None:
    with pytest.raises(ReviewRunnerError, match="Invalid line range"):
        parse_file_request("module.py:9-2")
    with pytest.raises(ReviewRunnerError, match="exceeds 400 lines"):
        parse_file_request("module.py:1-401")


def test_rejects_degenerate_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": "00000000000000000000", "done": True}

    class FakeSession:
        trust_env = True

        def post(self, *args, **kwargs):
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("local_review_runner.requests.Session", FakeSession)
    with pytest.raises(ReviewRunnerError, match="degenerate"):
        call_ollama("test")


def test_backend_profiles_are_explicit_and_local() -> None:
    assert BACKENDS["desktop"].model == "qwen3-coder:30b"
    assert BACKENDS["desktop"].base_url == "http://127.0.0.1:11434"
    assert BACKENDS["razer"].model == "qwen3-coder:30b"
    assert BACKENDS["razer"].base_url == "http://192.168.0.17:11434"


def test_backend_argument_defaults_to_desktop_and_accepts_razer() -> None:
    base = ["--files", "README.md", "--task", "review"]
    assert parse_args(base).backend == "desktop"
    assert parse_args([*base, "--backend", "razer"]).backend == "razer"


def test_call_ollama_uses_selected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": "valid response", "done": True}

    class FakeSession:
        trust_env = True

        def post(self, url, **kwargs):
            observed["url"] = url
            observed["payload"] = kwargs["json"]
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("local_review_runner.requests.Session", FakeSession)
    call_ollama("test", BACKENDS["razer"])
    assert observed["url"] == "http://192.168.0.17:11434/api/generate"
    assert observed["payload"]["model"] == "qwen3-coder:30b"
    assert observed["payload"]["keep_alive"] == "1h"
