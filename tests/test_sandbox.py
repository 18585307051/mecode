"""Sandbox 路径边界校验的单元测试。

覆盖 task.md T6 的 6 个验证场景。
"""

import os
import sys
from pathlib import Path

import pytest

from mewcode.tools.errors import PathOutOfSandboxError
from mewcode.tools.sandbox import Sandbox


def test_合法相对路径(tmp_path: Path) -> None:
    """相对路径应被解析到 cwd 下。"""
    sandbox = Sandbox(cwd=tmp_path)
    p = sandbox.resolve("a.py")
    assert p == (tmp_path / "a.py").resolve()


def test_合法绝对路径(tmp_path: Path) -> None:
    """绝对路径只要在 cwd 子树内就合法。"""
    sandbox = Sandbox(cwd=tmp_path)
    target = tmp_path / "x.txt"
    p = sandbox.resolve(str(target))
    assert p == target.resolve()


def test_合法子目录(tmp_path: Path) -> None:
    """多级子目录路径合法。"""
    sandbox = Sandbox(cwd=tmp_path)
    p = sandbox.resolve("sub/deep/a.py")
    assert (tmp_path / "sub" / "deep" / "a.py").resolve() == p


def test_合法_dot_dot_后仍在沙盒内(tmp_path: Path) -> None:
    """src/../a.py 解析后仍在 cwd 内，应合法。"""
    sandbox = Sandbox(cwd=tmp_path)
    p = sandbox.resolve("src/../a.py")
    assert p == (tmp_path / "a.py").resolve()


def test_越界_父目录(tmp_path: Path) -> None:
    """`..` 上溯到 cwd 外应抛 PathOutOfSandboxError。"""
    sandbox = Sandbox(cwd=tmp_path)
    with pytest.raises(PathOutOfSandboxError) as exc_info:
        sandbox.resolve("../outside.txt")
    assert "../outside.txt" in str(exc_info.value)
    assert "工作目录" in str(exc_info.value)


def test_越界_绝对路径(tmp_path: Path) -> None:
    """指向 cwd 外的绝对路径应抛错。"""
    sandbox = Sandbox(cwd=tmp_path)
    if sys.platform == "win32":
        outside = "C:/Windows/System32/drivers/etc/hosts"
    else:
        outside = "/etc/passwd"
    with pytest.raises(PathOutOfSandboxError):
        sandbox.resolve(outside)


def test_错误信息包含路径(tmp_path: Path) -> None:
    """错误信息应同时含原始 raw_path 与 cwd 字符串。"""
    sandbox = Sandbox(cwd=tmp_path)
    raw = "../../bad"
    with pytest.raises(PathOutOfSandboxError) as exc_info:
        sandbox.resolve(raw)
    msg = str(exc_info.value)
    assert raw in msg
    assert str(tmp_path) in msg
