"""Preflight must not promise the models will run when they provably will not.

On 2026-09-01 preflight reported `models: ok` on a machine where BGE-M3 could not
complete a forward pass: the weights are memory-mapped, so LOADING succeeds and the
process dies later, when the forward touches a page that cannot be backed. On
Windows that is an access violation (exit -1073741819) — a hard process kill with
no Python exception, no traceback, and no degraded-mode JSON. Under uvicorn it
takes the worker down mid-request.

Nothing downstream can catch it, which is why it has to be caught before the model
is asked to run.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from coursegen import config
from coursegen.app import preflight


class TestMemoryHeadroomCheck:
    def test_fails_when_headroom_is_below_the_requirement(self) -> None:
        with patch.object(preflight, "_free_virtual_memory_gb", return_value=0.5):
            with pytest.raises(RuntimeError) as exc:
                preflight._check_memory_headroom()
        msg = str(exc.value)
        assert "0.50 GB" in msg
        # The message must name the cause the user can act on. A capped page file
        # was the actual cause; "out of memory" alone would send them to close
        # browser tabs instead of fixing the setting.
        assert "page file" in msg.lower()

    def test_passes_with_ample_headroom(self) -> None:
        with patch.object(preflight, "_free_virtual_memory_gb", return_value=32.0):
            preflight._check_memory_headroom()

    def test_unknown_memory_is_not_a_failure(self) -> None:
        """A machine whose memory cannot be read is not a machine that is out of it.

        Failing closed on an unknown would block startup on every platform the
        helper does not cover.
        """
        with patch.object(preflight, "_free_virtual_memory_gb", return_value=None):
            preflight._check_memory_headroom()

    def test_boundary_is_not_off_by_one(self) -> None:
        with patch.object(preflight, "_free_virtual_memory_gb",
                          return_value=config.MIN_FREE_VIRTUAL_MEMORY_GB):
            preflight._check_memory_headroom()  # exactly at the floor passes


class TestPreflightSurface:
    def test_memory_is_reported_as_its_own_key(self) -> None:
        """Distinct from `models`. "The files are here" and "this machine can run
        them" are different questions, and only the first was ever asked."""
        status = preflight.preflight_status()
        assert "memory" in status
        assert "models" in status

    def test_status_never_raises(self) -> None:
        with patch.object(preflight, "_free_virtual_memory_gb", return_value=0.1):
            status = preflight.preflight_status()
        assert status["memory"] != "ok"
        assert isinstance(status["memory"], str)
