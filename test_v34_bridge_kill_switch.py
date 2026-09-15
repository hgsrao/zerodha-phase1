"""Tests for v34_bridge_kill_switch.py."""

from v34_bridge_kill_switch import is_kill_switch_active


class TestIsKillSwitchActive:
    def test_missing_file_is_inactive(self, tmp_path):
        assert is_kill_switch_active(tmp_path / "KILL_SWITCH") is False

    def test_existing_empty_file_is_active(self, tmp_path):
        path = tmp_path / "KILL_SWITCH"
        path.write_text("", encoding="utf-8")
        assert is_kill_switch_active(path) is True

    def test_existing_file_with_content_is_active_content_ignored(self, tmp_path):
        path = tmp_path / "KILL_SWITCH"
        path.write_text("halted by ops on 2026-08-15 - overnight risk review", encoding="utf-8")
        assert is_kill_switch_active(path) is True

    def test_removing_the_file_deactivates_it(self, tmp_path):
        path = tmp_path / "KILL_SWITCH"
        path.write_text("x", encoding="utf-8")
        assert is_kill_switch_active(path) is True
        path.unlink()
        assert is_kill_switch_active(path) is False
