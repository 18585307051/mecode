"""MCP Server 配置加载（spec F1 / F2 / F3）。

两层文件：
  ~/.mewcode/mcp_servers.yaml      (用户级)
  <cwd>/.mewcode/mcp_servers.yaml  (项目级)

合并：项目级 server 完整覆盖用户级同名 server；不同名取并集。

${VAR} 展开仅从 os.environ 读；缺失变量 → 跳过整个 Server（spec D4）。
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass
class ServerConfig:
    """单个 Server 的归一化配置。"""

    name: str
    type: Literal["stdio", "http"]
    # stdio
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    # http
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # 通用
    timeout: float = 60.0


_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def expand_vars(value: str) -> tuple[str, list[str]]:
    """展开 ${VAR}。

    Returns:
        (expanded, missing_vars)
        - expanded: 展开后字符串（缺失变量保留 ${VAR}）
        - missing_vars: 未定义的变量名列表（空 = 全部成功）
    """
    missing: list[str] = []

    def _sub(m: re.Match) -> str:
        var = m.group(1)
        val = os.environ.get(var)
        if val is None:
            missing.append(var)
            return m.group(0)
        return val

    return _VAR_RE.sub(_sub, value), missing


def _expand_dict(d: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """对 dict 的所有值做 ${VAR} 展开。"""
    out: dict[str, str] = {}
    all_missing: list[str] = []
    for k, v in d.items():
        expanded, missing = expand_vars(str(v))
        out[k] = expanded
        all_missing.extend(missing)
    return out, all_missing


def _parse_server(name: str, raw: dict) -> ServerConfig | None:
    """解析单个 server dict → ServerConfig。

    缺失必填字段或 ${VAR} 缺失 → 打印 warning 返回 None（跳过）。
    """
    if not isinstance(raw, dict):
        print(f"⚠️ MCP Server {name!r} 配置不是 dict（已跳过）")
        return None

    server_type = raw.get("type")
    if server_type not in ("stdio", "http"):
        print(f"⚠️ MCP Server {name!r} 缺少 type 字段或类型非法（已跳过）")
        return None

    try:
        timeout = float(raw.get("timeout", 60.0))
    except (TypeError, ValueError):
        timeout = 60.0

    if server_type == "stdio":
        command = raw.get("command")
        if not command:
            print(f"⚠️ MCP Server {name!r}（stdio）缺少 command（已跳过）")
            return None
        args = list(raw.get("args") or [])
        env_raw = raw.get("env") or {}
        if not isinstance(env_raw, dict):
            print(f"⚠️ MCP Server {name!r} 的 env 不是 dict（已跳过）")
            return None
        env, missing = _expand_dict(env_raw)
        if missing:
            print(
                f"⚠️ MCP Server {name!r} 配置含未定义环境变量 {missing}（已跳过）"
            )
            return None
        return ServerConfig(
            name=name,
            type="stdio",
            command=str(command),
            args=[str(a) for a in args],
            env=env,
            cwd=raw.get("cwd"),
            timeout=timeout,
        )

    # http
    url = raw.get("url")
    if not url:
        print(f"⚠️ MCP Server {name!r}（http）缺少 url（已跳过）")
        return None
    headers_raw = raw.get("headers") or {}
    if not isinstance(headers_raw, dict):
        print(f"⚠️ MCP Server {name!r} 的 headers 不是 dict（已跳过）")
        return None
    headers, missing = _expand_dict(headers_raw)
    if missing:
        print(
            f"⚠️ MCP Server {name!r} 配置含未定义环境变量 {missing}（已跳过）"
        )
        return None
    return ServerConfig(
        name=name,
        type="http",
        url=str(url),
        headers=headers,
        timeout=timeout,
    )


def _load_layer(path: Path) -> dict[str, dict]:
    """加载单个 YAML 文件，返回 servers dict（不做归一化解析）。"""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        print(f"⚠️ MCP 配置文件 {path} 解析失败：{e}")
        return {}
    if not isinstance(data, dict):
        return {}
    servers = data.get("servers") or {}
    if not isinstance(servers, dict):
        return {}
    return servers


def load_all(cwd: Path) -> dict[str, ServerConfig]:
    """加载两层配置并合并（spec F2）。

    合并规则：项目级 server 完整覆盖用户级同名 server；不同名取并集。
    """
    user_path = Path.home() / ".mewcode" / "mcp_servers.yaml"
    project_path = cwd / ".mewcode" / "mcp_servers.yaml"

    merged_raw: dict[str, dict] = {}
    merged_raw.update(_load_layer(user_path))
    merged_raw.update(_load_layer(project_path))  # 项目级覆盖

    out: dict[str, ServerConfig] = {}
    for name, raw in merged_raw.items():
        cfg = _parse_server(str(name), raw)
        if cfg is not None:
            out[cfg.name] = cfg
    return out
