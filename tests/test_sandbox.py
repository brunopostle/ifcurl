"""Tests for ifcurl.sandbox — subprocess isolation for ifcopenshell calls."""

from __future__ import annotations

import ctypes
import os
import sys

import pytest

from ifcurl.sandbox import (
    SandboxCrashError,
    SandboxTimeoutError,
    run_sandboxed,
)


def _return_value(x):
    return x


def _raise_value_error(msg):
    raise ValueError(msg)


def _segfault():
    ctypes.string_at(0)  # dereference NULL → SIGSEGV


def _sleep_forever():
    import time

    time.sleep(9999)


class TestRunSandboxed:
    def test_returns_value(self):
        assert run_sandboxed(_return_value, 42) == 42

    def test_propagates_exception(self):
        with pytest.raises(ValueError, match="bad input"):
            run_sandboxed(_raise_value_error, "bad input")

    @pytest.mark.skipif(sys.platform == "win32", reason="SIGSEGV test requires Unix")
    def test_crash_raises_sandbox_crash_error(self):
        with pytest.raises(SandboxCrashError):
            run_sandboxed(_segfault)

    def test_timeout_raises_sandbox_timeout_error(self):
        with pytest.raises(SandboxTimeoutError):
            run_sandboxed(_sleep_forever, timeout=1)
