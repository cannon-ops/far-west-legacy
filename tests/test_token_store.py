"""tests/test_token_store.py — src/token_store.py.

The point of this module is surviving `gunicorn -w 2`, so the load-bearing test is
TestCrossProcess: a real second Python process writes, and this one reads. Everything
else here is single-process behavior (TTLs, single-use pending records, lock ownership).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src import token_store

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point every test at its own store file. token_store resolves the path per call,
    so setting the env var is enough — nothing is cached at import time."""
    monkeypatch.setenv("FWL_TOKEN_STORE_PATH", str(tmp_path / "store.sqlite3"))
    yield


class TestPending:
    def test_round_trip(self):
        token_store.put_pending("state-abc", {"code_verifier": "v1", "redirect_uri": "http://x/callback"})
        assert token_store.pop_pending("state-abc") == {
            "code_verifier": "v1", "redirect_uri": "http://x/callback",
        }

    def test_single_use(self):
        token_store.put_pending("state-abc", {"code_verifier": "v1"})
        token_store.pop_pending("state-abc")
        assert token_store.pop_pending("state-abc") is None

    def test_unknown_state_is_none(self):
        assert token_store.pop_pending("never-issued") is None

    def test_expired_pending_is_not_returned(self, monkeypatch):
        monkeypatch.setattr(token_store, "PENDING_TTL_SECONDS", -1)
        token_store.put_pending("state-abc", {"code_verifier": "v1"})
        assert token_store.pop_pending("state-abc") is None

    def test_works_without_pkce_verifier(self):
        """If the beta AppKey turns out to reject PKCE, the record just carries no
        verifier. The store is deliberately agnostic to what is in the blob."""
        token_store.put_pending("state-abc", {"redirect_uri": "http://x/callback"})
        assert token_store.pop_pending("state-abc") == {"redirect_uri": "http://x/callback"}


class TestSession:
    def test_round_trip(self):
        token_store.put_session("sid1", {"access_token": "tok", "expires_in": 3600}, "Jane Smith")
        loaded = token_store.get_session("sid1")
        assert loaded["display_name"] == "Jane Smith"
        assert loaded["token"]["access_token"] == "tok"

    def test_unknown_session_is_none(self):
        assert token_store.get_session("nobody") is None

    def test_clear_session(self):
        token_store.put_session("sid1", {"expires_in": 3600}, "Jane")
        token_store.clear_session("sid1")
        assert token_store.get_session("sid1") is None

    def test_idle_expiry(self, monkeypatch):
        monkeypatch.setattr(token_store, "SESSION_IDLE_SECONDS", -1)
        token_store.put_session("sid1", {"expires_in": 3600}, "Jane")
        assert token_store.get_session("sid1") is None

    def test_get_slides_the_idle_window(self, monkeypatch):
        monkeypatch.setattr(token_store, "SESSION_IDLE_SECONDS", 1)
        token_store.put_session("sid1", {"expires_in": 3600}, "Jane")
        time.sleep(0.6)
        assert token_store.get_session("sid1") is not None  # slides to now + 1
        time.sleep(0.6)
        assert token_store.get_session("sid1") is not None  # still alive because of the slide

    def test_peek_does_not_slide_the_idle_window(self, monkeypatch):
        monkeypatch.setattr(token_store, "SESSION_IDLE_SECONDS", 1)
        token_store.put_session("sid1", {"expires_in": 3600}, "Jane")
        time.sleep(0.6)
        assert token_store.peek_session("sid1") is not None
        time.sleep(0.6)
        assert token_store.peek_session("sid1") is None  # idle window ran out despite the peek

    def test_hard_cap_from_token_expires_in_beats_idle_window(self, monkeypatch):
        """A token that dies in 1s must not be held alive for a 20-minute idle window."""
        monkeypatch.setattr(token_store, "SESSION_IDLE_SECONDS", 1200)
        token_store.put_session("sid1", {"expires_in": 1}, "Jane")
        assert token_store.get_session("sid1") is not None
        time.sleep(1.2)
        assert token_store.get_session("sid1") is None

    def test_missing_expires_in_falls_back_to_absolute_cap(self, monkeypatch):
        monkeypatch.setattr(token_store, "SESSION_ABSOLUTE_SECONDS", -1)
        token_store.put_session("sid1", {"access_token": "tok"}, "Jane")
        assert token_store.get_session("sid1") is None

    def test_unparseable_expires_in_does_not_raise(self):
        token_store.put_session("sid1", {"expires_in": "not-a-number"}, "Jane")
        assert token_store.get_session("sid1") is not None


class TestJobLock:
    def test_acquire_then_second_owner_blocked(self):
        assert token_store.acquire_job_lock("job1", "tab-a") is True
        assert token_store.acquire_job_lock("job1", "tab-b") is False

    def test_same_owner_reacquire_refreshes(self):
        assert token_store.acquire_job_lock("job1", "tab-a") is True
        assert token_store.acquire_job_lock("job1", "tab-a") is True

    def test_release_frees_the_lock(self):
        token_store.acquire_job_lock("job1", "tab-a")
        token_store.release_job_lock("job1", "tab-a")
        assert token_store.acquire_job_lock("job1", "tab-b") is True

    def test_release_by_non_owner_is_a_no_op(self):
        token_store.acquire_job_lock("job1", "tab-a")
        token_store.release_job_lock("job1", "tab-b")
        assert token_store.acquire_job_lock("job1", "tab-b") is False

    def test_expired_lock_is_reclaimable(self, monkeypatch):
        """A worker killed mid-upload must not wedge the job forever."""
        monkeypatch.setattr(token_store, "JOBLOCK_TTL_SECONDS", -1)
        token_store.acquire_job_lock("job1", "dead-worker")
        monkeypatch.setattr(token_store, "JOBLOCK_TTL_SECONDS", 300)
        assert token_store.acquire_job_lock("job1", "tab-b") is True

    def test_owner_lookup(self):
        assert token_store.job_lock_owner("job1") is None
        token_store.acquire_job_lock("job1", "tab-a")
        assert token_store.job_lock_owner("job1") == "tab-a"

    def test_locks_are_per_job(self):
        assert token_store.acquire_job_lock("job1", "tab-a") is True
        assert token_store.acquire_job_lock("job2", "tab-b") is True


class TestSweep:
    def test_sweep_removes_expired_rows_only(self, monkeypatch):
        token_store.put_session("live", {"expires_in": 3600}, "Jane")
        monkeypatch.setattr(token_store, "PENDING_TTL_SECONDS", -1)
        token_store.put_pending("dead", {"code_verifier": "v"})
        token_store.sweep()
        assert token_store.get_session("live") is not None
        assert token_store.pop_pending("dead") is None

    def test_reset_clears_everything(self):
        token_store.put_session("sid1", {"expires_in": 3600}, "Jane")
        token_store.acquire_job_lock("job1", "tab-a")
        token_store.reset()
        assert token_store.get_session("sid1") is None
        assert token_store.job_lock_owner("job1") is None


# ---------------------------------------------------------------------------
# The reason this module exists: two OS processes, one store.
# ---------------------------------------------------------------------------

_WRITER = """
import sys
from src import token_store
token_store.put_pending(sys.argv[1], {"code_verifier": "verifier-from-worker-a"})
token_store.put_session(sys.argv[2], {"access_token": "tok-a", "expires_in": 3600}, "Worker A User")
token_store.acquire_job_lock(sys.argv[3], "worker-a")
"""


def _run_writer(store_path: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "FWL_TOKEN_STORE_PATH": str(store_path), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-c", _WRITER, *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )


class TestCrossProcess:
    """Proves the fix for the actual production bug: under `gunicorn -w 2`, /auth/login
    and /callback can land on different worker processes. The old module-level dicts lost
    the PKCE code_verifier between them; this store does not."""

    def test_second_process_sees_pending_session_and_lock(self, tmp_path):
        store_path = tmp_path / "store.sqlite3"
        os.environ["FWL_TOKEN_STORE_PATH"] = str(store_path)

        result = _run_writer(store_path, ["state-x", "sid-x", "job-x"])
        assert result.returncode == 0, f"writer process failed:\n{result.stdout}\n{result.stderr}"

        # This process is "worker B" — it never saw any of the writes above in memory.
        assert token_store.pop_pending("state-x") == {"code_verifier": "verifier-from-worker-a"}

        loaded = token_store.get_session("sid-x")
        assert loaded is not None
        assert loaded["display_name"] == "Worker A User"
        assert loaded["token"]["access_token"] == "tok-a"

        # A job another worker is running is locked against this one.
        assert token_store.acquire_job_lock("job-x", "worker-b") is False

    def test_write_from_this_process_is_visible_to_another(self, tmp_path):
        store_path = tmp_path / "store.sqlite3"
        os.environ["FWL_TOKEN_STORE_PATH"] = str(store_path)

        token_store.acquire_job_lock("job-y", "worker-b")
        # worker-a tries to take the same lock and must be refused.
        result = _run_writer(store_path, ["state-y", "sid-y", "job-y"])
        assert result.returncode == 0, f"writer process failed:\n{result.stdout}\n{result.stderr}"
        assert token_store.job_lock_owner("job-y") == "worker-b"
