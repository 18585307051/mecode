"""MCP 配置加载单测（spec AC1-AC4）。"""

from pathlib import Path

import pytest
import yaml

from mewcode.mcp.config import (
    ServerConfig,
    _load_layer,
    _parse_server,
    expand_vars,
    load_all,
)


def test_expand_vars_成功(monkeypatch) -> None:
    monkeypatch.setenv("TEST_VAR", "hello")
    assert expand_vars("${TEST_VAR}/world") == ("hello/world", [])


def test_expand_vars_缺失() -> None:
    expanded, missing = expand_vars("Bearer ${MISSING_TOKEN}")
    assert expanded == "Bearer ${MISSING_TOKEN}"
    assert missing == ["MISSING_TOKEN"]


def test_expand_vars_多个变量(monkeypatch) -> None:
    monkeypatch.setenv("A", "1")
    monkeypatch.setenv("B", "2")
    assert expand_vars("${A}-${B}") == ("1-2", [])


def test_parse_server_合法stdio(monkeypatch) -> None:
    monkeypatch.setenv("DEBUG_MODE", "1")
    cfg = _parse_server(
        "fs",
        {
            "type": "stdio",
            "command": "python",
            "args": ["server.py"],
            "env": {"DEBUG": "${DEBUG_MODE}"},
            "timeout": 10,
        },
    )
    assert isinstance(cfg, ServerConfig)
    assert cfg.name == "fs"
    assert cfg.type == "stdio"
    assert cfg.command == "python"
    assert cfg.args == ["server.py"]
    assert cfg.env == {"DEBUG": "1"}
    assert cfg.timeout == 10.0


def test_parse_server_合法http(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "abc")
    cfg = _parse_server(
        "github",
        {
            "type": "http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer ${TOKEN}"},
        },
    )
    assert cfg is not None
    assert cfg.type == "http"
    assert cfg.url == "https://example.com/mcp"
    assert cfg.headers["Authorization"] == "Bearer abc"
    assert cfg.timeout == 60.0


def test_parse_server_缺type(capsys) -> None:
    assert _parse_server("bad", {}) is None
    assert "type" in capsys.readouterr().out


def test_parse_server_缺command(capsys) -> None:
    assert _parse_server("bad", {"type": "stdio"}) is None
    assert "command" in capsys.readouterr().out


def test_parse_server_缺url(capsys) -> None:
    assert _parse_server("bad", {"type": "http"}) is None
    assert "url" in capsys.readouterr().out


def test_parse_server_变量缺失跳过(capsys) -> None:
    cfg = _parse_server(
        "github",
        {
            "type": "http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer ${NO_SUCH_VAR}"},
        },
    )
    assert cfg is None
    out = capsys.readouterr().out
    assert "NO_SUCH_VAR" in out
    assert "已跳过" in out


def test_load_layer_文件不存在(tmp_path: Path) -> None:
    assert _load_layer(tmp_path / "missing.yaml") == {}


def test_load_layer_yaml错误(tmp_path: Path, capsys) -> None:
    p = tmp_path / "mcp_servers.yaml"
    p.write_text("servers: [", encoding="utf-8")
    assert _load_layer(p) == {}
    assert "解析失败" in capsys.readouterr().out


def test_load_layer_合法(tmp_path: Path) -> None:
    p = tmp_path / "mcp_servers.yaml"
    p.write_text(
        yaml.safe_dump({"servers": {"fs": {"type": "stdio", "command": "python"}}}),
        encoding="utf-8",
    )
    data = _load_layer(p)
    assert "fs" in data


@pytest.fixture
def layered_cwd(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".mewcode").mkdir()
    (tmp_path / ".mewcode").mkdir()
    return tmp_path


def test_load_all_用户级(layered_cwd: Path) -> None:
    user = Path.home() / ".mewcode" / "mcp_servers.yaml"
    user.write_text(
        yaml.safe_dump({"servers": {"fs": {"type": "stdio", "command": "python"}}}),
        encoding="utf-8",
    )
    cfgs = load_all(layered_cwd)
    assert set(cfgs) == {"fs"}
    assert cfgs["fs"].command == "python"


def test_load_all_项目覆盖用户(layered_cwd: Path) -> None:
    user = Path.home() / ".mewcode" / "mcp_servers.yaml"
    project = layered_cwd / ".mewcode" / "mcp_servers.yaml"
    user.write_text(
        yaml.safe_dump({"servers": {"fs": {"type": "stdio", "command": "python"}}}),
        encoding="utf-8",
    )
    project.write_text(
        yaml.safe_dump({"servers": {"fs": {"type": "stdio", "command": "node"}}}),
        encoding="utf-8",
    )
    cfgs = load_all(layered_cwd)
    assert cfgs["fs"].command == "node"


def test_load_all_不同名取并集(layered_cwd: Path) -> None:
    user = Path.home() / ".mewcode" / "mcp_servers.yaml"
    project = layered_cwd / ".mewcode" / "mcp_servers.yaml"
    user.write_text(
        yaml.safe_dump({"servers": {"user": {"type": "stdio", "command": "python"}}}),
        encoding="utf-8",
    )
    project.write_text(
        yaml.safe_dump({"servers": {"project": {"type": "http", "url": "https://x"}}}),
        encoding="utf-8",
    )
    cfgs = load_all(layered_cwd)
    assert set(cfgs) == {"user", "project"}
