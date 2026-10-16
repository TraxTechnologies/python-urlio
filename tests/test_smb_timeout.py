"""Tests for SMB stall timeouts (hung listing / dead connections)."""
from __future__ import absolute_import

import time

import pytest
from smb.base import SMBTimeout as PySMBTimeout

from urlio.smb_timeout import SMBTimeoutError, wait_while_busy
from urlio.smb_ext import iter_listPath, listPath
from urlio.path import SMBPath


class _StalledConn(object):
    """Connection that never completes an in-flight SMB operation."""

    def __init__(self):
        self.is_busy = True
        self.sock = object()
        self.is_using_smb2 = True
        self.has_authenticated = True
        self.poll_count = 0

    def _pollForNetBIOSPacket(self, timeout):
        self.poll_count += 1
        raise PySMBTimeout()


class _CompletingConn(_StalledConn):
    def _pollForNetBIOSPacket(self, timeout):
        self.is_busy = False


def test_wait_while_busy_raises_on_stall():
    conn = _StalledConn()
    start = time.time()
    with pytest.raises(SMBTimeoutError):
        wait_while_busy(conn, idle_timeout=1)
    elapsed = time.time() - start
    assert elapsed < 5
    assert conn.poll_count >= 1


def test_wait_while_busy_completes_when_not_busy():
    conn = _CompletingConn()
    wait_while_busy(conn, idle_timeout=1)


def test_iter_list_path_stall_bounded(monkeypatch):
    conn = _StalledConn()

    def noop_smb2(*_args, **_kwargs):
        return None

    monkeypatch.setattr('urlio.smb_ext._listPath_SMB2', noop_smb2)

    start = time.time()
    with pytest.raises(SMBTimeoutError):
        list(iter_listPath(conn, 'share', 'dir', timeout=1))
    assert time.time() - start < 5


def test_list_path_stall_bounded(monkeypatch):
    conn = _StalledConn()
    monkeypatch.setattr('urlio.smb_ext._listPath_SMB2', lambda *_a, **_k: None)

    start = time.time()
    with pytest.raises(SMBTimeoutError):
        listPath(conn, 'share', 'dir', timeout=1)
    assert time.time() - start < 5


def test_smbpath_ls_honors_timeout(monkeypatch):
    captured = {}

    def fake_iter_list_path(conn, share, path, **kwargs):
        captured.update(kwargs)
        return iter([])

    monkeypatch.setattr('urlio.path.iter_listPath', fake_iter_list_path)

    def mock_find_dfs_share(path):
        return ('server', 'share', 'domain', 'relpath')

    p = SMBPath(
        r'\\server\share\relpath',
        timeout=37,
        find_dfs_share=mock_find_dfs_share,
        user='u',
        password='p',
    )
    p._conn = object()
    monkeypatch.setattr(
        SMBPath, 'get_connection', lambda self: self._conn
    )

    list(p.ls())
    assert captured['timeout'] == 37


def test_smbpath_invalidates_connection_on_timeout(monkeypatch):
    def mock_find_dfs_share(path):
        return ('server', 'share', 'domain', 'relpath')

    p = SMBPath(
        r'\\server\share\relpath',
        timeout=1,
        find_dfs_share=mock_find_dfs_share,
        user='u',
        password='p',
    )
    p._conn = _StalledConn()
    monkeypatch.setattr(
        SMBPath, 'get_connection', lambda self: self._conn
    )
    monkeypatch.setattr('urlio.smb_ext._listPath_SMB2', lambda *_a, **_k: None)

    with pytest.raises(SMBTimeoutError):
        list(p.ls())
    assert p._conn is None
