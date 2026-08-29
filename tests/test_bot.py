import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def test_auth_blocks_when_whitelist_empty(monkeypatch):
    import satanas.config as cfg
    import satanas.auth as auth

    monkeypatch.setattr(cfg, "ALLOWED_USER_IDS", set())
    class FakeUser:
        id = 111
    class FakeUpdate:
        effective_user = FakeUser()
    assert auth.is_allowed(FakeUpdate()) is False


def test_auth_blocks_unknown_user(monkeypatch):
    import satanas.config as cfg
    import satanas.auth as auth

    monkeypatch.setattr(cfg, "ALLOWED_USER_IDS", {111})

    class FakeUser:
        id = 222

    class FakeUpdate:
        effective_user = FakeUser()

    assert auth.is_allowed(FakeUpdate()) is False


def test_state_reset_clears_password():
    import satanas.state as state

    st = state.get(99)
    st.phase = "sync"
    state.reset(99)
    assert state.get(99).phase == "idle"
    assert state.get(99).waiting_captcha is False
