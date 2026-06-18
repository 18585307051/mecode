"""TOCTOU 防御单测（spec AC3）。"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mewcode.tools.errors import (
    PathOutOfSandboxError,
    PathRaceConditionError,
)
from mewcode.tools.sandbox import Sandbox


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


# ---------- safe_open 正常路径 ----------


def test_safe_open_读取正常文件(sandbox: Sandbox, tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")

    with sandbox.safe_open("a.txt", "r") as fp:
        assert fp.read() == "hello"


def test_safe_open_写入(sandbox: Sandbox, tmp_path: Path) -> None:
    with sandbox.safe_open("b.txt", "w") as fp:
        fp.write("world")
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "world"


def test_safe_open_binary模式(sandbox: Sandbox, tmp_path: Path) -> None:
    f = tmp_path / "c.bin"
    f.write_bytes(b"\x00\x01\x02")

    with sandbox.safe_open("c.bin", "rb") as fp:
        assert fp.read() == b"\x00\x01\x02"


# ---------- 沙箱越界 ----------


def test_safe_open_越界路径抛错(sandbox: Sandbox) -> None:
    with pytest.raises(PathOutOfSandboxError):
        with sandbox.safe_open("../outside.txt", "r"):
            pass


# ---------- TOCTOU 检测 ----------


def test_safe_open_inode不一致_抛PathRaceConditionError(
    sandbox: Sandbox, tmp_path: Path
) -> None:
    """spec AC3：mock fstat 与 lstat 返回不同 inode → safe_open 抛错。"""
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")

    real_fstat = os.fstat
    real_lstat = os.lstat

    def fake_fstat(fd):
        # 返回构造的 stat：inode 假装是 999
        s = real_fstat(fd)

        class Fake:
            st_ino = 999
            st_dev = s.st_dev

        return Fake()

    def fake_lstat(path):
        s = real_lstat(path)

        class Fake:
            st_ino = 1234  # 与上面 999 不同 → 触发 race
            st_dev = s.st_dev

        return Fake()

    with patch("os.fstat", side_effect=fake_fstat), \
         patch("os.lstat", side_effect=fake_lstat):
        with pytest.raises(PathRaceConditionError):
            with sandbox.safe_open("a.txt", "r"):
                pass


def test_safe_open_dev不一致_抛PathRaceConditionError(
    sandbox: Sandbox, tmp_path: Path
) -> None:
    """dev 不一致也算竞态。"""
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")

    real_fstat = os.fstat

    def fake_fstat(fd):
        s = real_fstat(fd)

        class Fake:
            st_ino = s.st_ino
            st_dev = 99999  # 假装跨设备

        return Fake()

    with patch("os.fstat", side_effect=fake_fstat):
        with pytest.raises(PathRaceConditionError):
            with sandbox.safe_open("a.txt", "r"):
                pass


def test_safe_open_OSError时不抛race(
    sandbox: Sandbox, tmp_path: Path
) -> None:
    """fstat / lstat 抛 OSError 时（如 Windows 某些场景）不当作 race，
    宽容处理。"""
    f = tmp_path / "a.txt"
    f.write_text("ok", encoding="utf-8")

    with patch("os.fstat", side_effect=OSError("not supported")):
        # 不应抛 PathRaceConditionError，而是正常读取
        with sandbox.safe_open("a.txt", "r") as fp:
            assert fp.read() == "ok"
