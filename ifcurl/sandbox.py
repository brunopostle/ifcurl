# IFC URL — resolve and render ifc:// URLs
# Copyright (C) 2026 Bruno Postle <bruno@postle.net>
#
# This file is part of IFC URL.
#
# IFC URL is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IFC URL is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IFC URL.  If not, see <http://www.gnu.org/licenses/>.

"""Subprocess sandbox for ifcopenshell operations.

ifcopenshell uses C++ bindings that can segfault on malformed or adversarially
crafted IFC data.  Running untrusted IFC through the main service process
exposes it to DoS (segfault kills the worker) and potential RCE (memory
corruption).

``run_sandboxed`` executes a callable in a short-lived child process.  If the
child exits with a fatal signal (SIGSEGV, SIGABRT, …) the parent raises
``SandboxCrashError`` instead of dying.  If the child exceeds the configured
timeout, the parent raises ``SandboxTimeoutError`` and kills the child.

Resource limits
---------------
Set ``IFCURL_SANDBOX_MEMORY_MB`` to cap the child's virtual address space (in
MiB).  Set ``IFCURL_SANDBOX_CPU_SECS`` to cap CPU time (in seconds).  Both
default to 0 (no limit) so that out-of-the-box behaviour is unchanged; set
them in production to bound worst-case resource usage.

Timeout
-------
``IFCURL_SANDBOX_TIMEOUT`` (default 120 s) applies per-call.  The timeout
should be longer than the longest expected render time so that slow-but-valid
models aren't rejected.
"""

from __future__ import annotations

import multiprocessing
import os

try:
    import resource as _resource
    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False  # Windows

_SANDBOX_TIMEOUT: int = int(os.environ.get("IFCURL_SANDBOX_TIMEOUT", "120"))
_SANDBOX_MEMORY_MB: int = int(os.environ.get("IFCURL_SANDBOX_MEMORY_MB", "0"))
_SANDBOX_CPU_SECS: int = int(os.environ.get("IFCURL_SANDBOX_CPU_SECS", "0"))


class SandboxError(RuntimeError):
    pass


class SandboxCrashError(SandboxError):
    """Child process was killed by a signal (segfault, abort, …)."""


class SandboxTimeoutError(SandboxError):
    """Child process exceeded the allowed wall-clock time."""


def _worker(conn: multiprocessing.connection.Connection, fn, args: tuple, kwargs: dict) -> None:
    """Entry point for the sandboxed child process."""
    if _HAS_RESOURCE:
        if _SANDBOX_MEMORY_MB > 0:
            limit = _SANDBOX_MEMORY_MB * 1024 * 1024
            _resource.setrlimit(_resource.RLIMIT_AS, (limit, limit))
        if _SANDBOX_CPU_SECS > 0:
            _resource.setrlimit(_resource.RLIMIT_CPU, (_SANDBOX_CPU_SECS, _SANDBOX_CPU_SECS))

    try:
        result = fn(*args, **kwargs)
        conn.send(("ok", result))
    except BaseException as exc:
        try:
            conn.send(("err", exc))
        except Exception:
            pass  # if the exception is not picklable, the parent sees EOFError
    finally:
        conn.close()


def run_sandboxed(fn, *args, timeout: int = _SANDBOX_TIMEOUT, **kwargs):
    """Run ``fn(*args, **kwargs)`` in a child process.

    :returns: The return value of *fn* on success.
    :raises SandboxCrashError: If the child exits with a signal.
    :raises SandboxTimeoutError: If the child exceeds *timeout* seconds.
    :raises: Any exception raised by *fn* inside the child (re-raised as-is).
    """
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    proc = multiprocessing.Process(
        target=_worker,
        args=(child_conn, fn, args, kwargs),
        daemon=True,
    )
    proc.start()
    child_conn.close()

    got_data = parent_conn.poll(timeout)
    if not got_data:
        parent_conn.close()
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join()
        raise SandboxTimeoutError(f"Subprocess timed out after {timeout}s")

    try:
        status, value = parent_conn.recv()
    except EOFError:
        parent_conn.close()
        proc.join()
        sig = -proc.exitcode if (proc.exitcode is not None and proc.exitcode < 0) else proc.exitcode
        raise SandboxCrashError(f"Subprocess terminated without sending result (exit {sig})")

    parent_conn.close()
    proc.join()

    if proc.exitcode is not None and proc.exitcode < 0:
        raise SandboxCrashError(f"Subprocess killed by signal {-proc.exitcode}")

    if status == "err":
        raise value
    return value
