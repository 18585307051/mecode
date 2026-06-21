"""规则解析与匹配单测（spec AC5 / AC6）。"""

from mewcode.permissions.rules import (
    Rule,
    extract_match_target,
    format_rule_for_display,
    parse_rule,
)


# ---------- parse_rule ----------


def test_parse_rule_合法() -> None:
    r = parse_rule("Bash(git *)")
    assert r is not None
    assert r.tool == "run"
    assert r.pattern == "git *"
    assert r.raw == "Bash(git *)"


def test_parse_rule_所有工具映射() -> None:
    cases = [
        ("Bash(*)", "run"),
        ("Run(*)", "run"),
        ("Read(*)", "read"),
        ("Write(*)", "write"),
        ("Edit(*)", "edit"),
        ("Glob(*)", "glob"),
        ("Search(*)", "search"),
    ]
    for raw, expected_tool in cases:
        r = parse_rule(raw)
        assert r is not None and r.tool == expected_tool, f"失败：{raw}"


def test_parse_rule_大小写不敏感() -> None:
    assert parse_rule("bash(*)") is not None
    assert parse_rule("BASH(*)") is not None
    assert parse_rule("BaSh(*)") is not None


def test_parse_rule_非法格式() -> None:
    assert parse_rule("invalid") is None
    assert parse_rule("Bash") is None
    assert parse_rule("Bash()") is None  # 空模式
    assert parse_rule("(*)") is None  # 缺工具名
    assert parse_rule("UnknownTool(*)") is None  # 工具未知
    assert parse_rule("") is None


def test_parse_rule_pattern含特殊字符() -> None:
    r = parse_rule("Read(src/**/*.py)")
    assert r is not None
    assert r.pattern == "src/**/*.py"

    r2 = parse_rule("Bash(npm install --save-dev *)")
    assert r2 is not None
    assert r2.pattern == "npm install --save-dev *"


# ---------- Rule.matches ----------


def test_matches_前缀语义_命中() -> None:
    r = parse_rule("Bash(git *)")
    assert r is not None
    assert r.matches("run", "git status")
    assert r.matches("run", "git push origin main")
    assert r.matches("run", "git commit -m 'hi'")


def test_matches_前缀语义_未命中() -> None:
    r = parse_rule("Bash(git *)")
    assert r is not None
    # 不以 git 开头
    assert not r.matches("run", "cd /tmp && git status")
    assert not r.matches("run", "echo git status")


def test_matches_工具名不一致() -> None:
    r = parse_rule("Bash(git *)")
    assert r is not None
    assert not r.matches("read", "git status")  # 工具不对


def test_matches_精确匹配() -> None:
    r = parse_rule("Bash(git status)")
    assert r is not None
    assert r.matches("run", "git status")
    assert not r.matches("run", "git status -s")  # 多了字符


def test_matches_glob_path() -> None:
    """注意 fnmatch 的 `*` 含 `/`（不像 shell），spec D3 已说明此简化。"""
    # 用 src/* 而非 src/**/*——fnmatch 中 * 已含 /
    r = parse_rule("Read(src/*.py)")
    assert r is not None
    assert r.matches("read", "src/a.py")
    assert r.matches("read", "src/sub/b.py")  # * 含 / 在 fnmatch 中
    assert not r.matches("read", "tests/a.py")


# ---------- extract_match_target ----------


def test_extract_target_run() -> None:
    assert extract_match_target("run", {"command": "ls -la"}) == "ls -la"


def test_extract_target_read() -> None:
    assert extract_match_target("read", {"path": "a.py"}) == "a.py"


def test_extract_target_glob() -> None:
    assert extract_match_target("glob", {"pattern": "**/*.py"}) == "**/*.py"


def test_extract_target_unknown() -> None:
    assert extract_match_target("unknown", {}) is None


def test_extract_target_缺字段() -> None:
    assert extract_match_target("run", {}) == ""  # 默认空字符串


# ---------- format_rule_for_display ----------


def test_format_rule_run() -> None:
    assert format_rule_for_display("run", "git status") == "Bash(git status)"


def test_format_rule_read() -> None:
    assert format_rule_for_display("read", "a.py") == "Read(a.py)"


def test_mcp_rule_bucket() -> None:
    """第六阶段：Mcp(...) 规则匹配所有 mcp__ 前缀工具。"""
    r = parse_rule("Mcp(mcp__fs__*)")
    assert r is not None
    assert r.tool == "mcp"
    assert r.matches("mcp__fs__read_file", "mcp__fs__read_file")
    assert not r.matches("mcp__github__create_issue", "mcp__github__create_issue")
    assert not r.matches("run", "mcp__fs__read_file")


def test_extract_target_mcp() -> None:
    assert extract_match_target("mcp__fs__read_file", {}) == "mcp__fs__read_file"


def test_format_rule_mcp() -> None:
    assert (
        format_rule_for_display("mcp__fs__read_file", "mcp__fs__read_file")
        == "Mcp(mcp__fs__read_file)"
    )
