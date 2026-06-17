"""mewcode.config.loader 的单元测试。

覆盖 6 个场景（task.md T4 验证）：
- 合法配置成功加载
- 文件不存在
- YAML 格式错误
- 缺失 default 字段
- protocol 取值非法
- default 指向不存在的供应商
"""

from pathlib import Path

import pytest

from mewcode.config import (
    AppConfig,
    ConfigFieldError,
    ConfigFileNotFound,
    ConfigFormatError,
    load,
)

# ---------- 辅助函数 ----------


def _write(tmp_path: Path, content: str) -> Path:
    """把内容写入临时 mewcode.yaml 并返回路径。"""
    p = tmp_path / "mewcode.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------- 测试用例 ----------


def test_load_合法配置成功(tmp_path: Path) -> None:
    """合法配置可以被解析为 AppConfig，字段正确填充。"""
    p = _write(
        tmp_path,
        """
default: alice
providers:
  alice:
    protocol: anthropic
    model: claude-x
    base_url: https://example.com
    api_key: sk-aaa
  bob:
    protocol: openai
    model: gpt-x
    base_url: https://example2.com
    api_key: sk-bbb
""",
    )
    cfg = load(p)
    assert isinstance(cfg, AppConfig)
    assert cfg.default == "alice"
    assert set(cfg.providers.keys()) == {"alice", "bob"}
    alice = cfg.providers["alice"]
    assert alice.name == "alice"
    assert alice.protocol == "anthropic"
    assert alice.model == "claude-x"
    assert alice.base_url == "https://example.com"
    assert alice.api_key == "sk-aaa"


def test_文件不存在(tmp_path: Path) -> None:
    """不存在的路径应抛 ConfigFileNotFound。"""
    with pytest.raises(ConfigFileNotFound) as exc_info:
        load(tmp_path / "nonexistent.yaml")
    assert "未找到配置文件" in str(exc_info.value)


def test_yaml格式错误(tmp_path: Path) -> None:
    """无法解析的 YAML 应抛 ConfigFormatError。"""
    p = _write(tmp_path, "[[[ broken yaml :::")
    with pytest.raises(ConfigFormatError):
        load(p)


def test_顶层不是字典(tmp_path: Path) -> None:
    """顶层是列表等非字典结构应抛 ConfigFormatError。"""
    p = _write(tmp_path, "- a\n- b\n")
    with pytest.raises(ConfigFormatError):
        load(p)


def test_缺失default字段(tmp_path: Path) -> None:
    """缺 default 字段应抛 ConfigFieldError，错误信息含 default。"""
    p = _write(
        tmp_path,
        """
providers:
  alice:
    protocol: anthropic
    model: m
    base_url: u
    api_key: k
""",
    )
    with pytest.raises(ConfigFieldError) as exc_info:
        load(p)
    assert "default" in str(exc_info.value)


def test_缺失providers字段(tmp_path: Path) -> None:
    """缺 providers 字段应抛 ConfigFieldError。"""
    p = _write(tmp_path, "default: alice\n")
    with pytest.raises(ConfigFieldError) as exc_info:
        load(p)
    assert "providers" in str(exc_info.value)


def test_protocol非法(tmp_path: Path) -> None:
    """protocol 取值不在白名单中应抛 ConfigFieldError。"""
    p = _write(
        tmp_path,
        """
default: alice
providers:
  alice:
    protocol: gemini
    model: m
    base_url: u
    api_key: k
""",
    )
    with pytest.raises(ConfigFieldError) as exc_info:
        load(p)
    assert "protocol" in str(exc_info.value)


def test_default指向不存在的供应商(tmp_path: Path) -> None:
    """default 指向 providers 中没有的 name 应抛 ConfigFieldError。"""
    p = _write(
        tmp_path,
        """
default: charlie
providers:
  alice:
    protocol: anthropic
    model: m
    base_url: u
    api_key: k
""",
    )
    with pytest.raises(ConfigFieldError) as exc_info:
        load(p)
    msg = str(exc_info.value)
    assert "default" in msg
    assert "charlie" in msg


def test_provider缺字段(tmp_path: Path) -> None:
    """provider 条目缺核心字段应抛 ConfigFieldError，错误信息含字段名。"""
    p = _write(
        tmp_path,
        """
default: alice
providers:
  alice:
    protocol: anthropic
    model: m
    base_url: u
""",
    )
    with pytest.raises(ConfigFieldError) as exc_info:
        load(p)
    assert "api_key" in str(exc_info.value)
