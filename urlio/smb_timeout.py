"""
SMB synchronous poll helpers and timeout errors for urlio.

``timeout`` on listing and other SMBPath operations is a *stall* limit: the
maximum number of seconds without SMB traffic while an operation is in flight.
Slow but active directory listings (large folders, slow filers) may run much
longer than ``timeout`` as long as the server keeps responding within that
window.  When the stall limit is exceeded, :class:`SMBTimeoutError` is raised.
"""
from __future__ import absolute_import

import time

try:
    from smb.base import SMBTimeout as _PySMBTimeout
except ImportError:
    _PySMBTimeout = None

# Upper bound on a single _pollForNetBIOSPacket call (seconds).
_POLL_CHUNK = 30.0


class SMBTimeoutError(Exception):
    """SMB I/O exceeded the configured stall timeout without server progress."""


def _remaining_idle(idle_limit, last_progress_at):
    return idle_limit - (time.time() - last_progress_at)


def wait_while_busy(conn, idle_timeout, progress_marker=None):
    """
    Poll until ``conn.is_busy`` is false or the stall timeout expires.

    :param conn: pysmb SMBConnection
    :param idle_timeout: max seconds without SMB progress before raising
    :param progress_marker: optional zero-arg callable; when its return value
        changes, the idle timer is reset (e.g. bytes uploaded counter).
    """
    if idle_timeout <= 0:
        raise SMBTimeoutError(
            'SMB operation stalled: idle timeout must be positive'
        )

    last_progress_at = time.time()
    last_marker = progress_marker() if progress_marker else None

    while conn.is_busy:
        remaining = _remaining_idle(idle_timeout, last_progress_at)
        if remaining <= 0:
            raise SMBTimeoutError(
                'SMB operation stalled for {:.0f}s without progress'.format(
                    idle_timeout
                )
            )

        poll_timeout = min(_POLL_CHUNK, remaining)
        try:
            conn._pollForNetBIOSPacket(poll_timeout)
            last_progress_at = time.time()
        except Exception as exc:
            if _PySMBTimeout is not None and isinstance(exc, _PySMBTimeout):
                remaining = _remaining_idle(idle_timeout, last_progress_at)
                if remaining <= 0:
                    raise SMBTimeoutError(
                        'SMB operation stalled for {:.0f}s without progress'.format(
                            idle_timeout
                        )
                    )
                continue
            raise

        if progress_marker is not None:
            marker = progress_marker()
            if marker != last_marker:
                last_marker = marker
                last_progress_at = time.time()


def map_pysmb_timeout(exc):
    """Convert pysmb SMBTimeout to urlio SMBTimeoutError where appropriate."""
    if _PySMBTimeout is not None and isinstance(exc, _PySMBTimeout):
        return SMBTimeoutError(
            'SMB operation timed out waiting for a response from the server'
        )
    return exc
