"""命令注册表与分发（第十阶段扩展）。

数据结构：
- Command         —— 命令定义（名称、别名、描述、handler、type、usage、arg_hint、hidden）
- CommandType     —— LOCAL / STATEFUL / PROMPT 三类执行模式
- CommandContext  —— 传给 handler 的上下文
- CommandResult   —— handler 返回值（含 should_exit / prompt_text）
- CommandRegistrationError —— 注册期撞名 / 自反 alias / 非法 type 抛出

工作流：
1. 启动期 builtin.register_builtins() 把所有内置命令写入 COMMANDS。
2. 注册时立即校验：同名 / alias 撞名 / 自反 / 非法 type → CommandRegistrationError。
3. REPL 主循环对每行输入调 dispatch(line, ctx)。
4. dispatch：
   - 不以 / 开头 → 返回 None（落对话分支）
   - 命令存在 → 执行 handler → 返回 CommandResult
   - 命令未知 / 纯 / → 调 renderer.print_unknown_command → 返回 CommandResult()
5. 命令名大小写不敏感；COMMANDS 内部以小写 key 索引。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.chat import Session
    from mewcode.config import AppConfig
    from mewcode.render import Renderer


# ---------- 类型常量 ----------


class CommandType:
    """命令执行模式（spec 第十阶段 F1）。

    LOCAL    —— 纯本地查询；handler 不修改 session 历史，不向 LLM 发请求。
    STATEFUL —— 影响运行时状态；handler 修改 session/policy/provider/mode 等。
    PROMPT   —— 提示词命令；handler 构造文本，REPL 当作用户输入注入 run_turn。
    """

    LOCAL = "local"
    STATEFUL = "stateful"
    PROMPT = "prompt"


_VALID_TYPES = frozenset({CommandType.LOCAL, CommandType.STATEFUL, CommandType.PROMPT})


# ---------- 异常 ----------


class CommandRegistrationError(RuntimeError):
    """命令注册期检测到撞名 / 自反 alias / 非法 type。

    由 main 在调用 register_builtins() 时捕获，打印单行红字后退出码 1。
    不属于运行时错误，不应携带 traceback 给最终用户。
    """


# ---------- 数据结构 ----------


@dataclass(frozen=True)
class Command:
    """单个命令的定义。

    第十阶段在第七阶段的 name/aliases/description/handler 之上新增四字段：
    type 必填，usage / arg_hint / hidden 有默认值。
    """

    name: str                                # 不含 / 前缀
    aliases: tuple[str, ...]                 # 别名列表（不含 / 前缀）
    description: str                         # /help 中展示
    handler: Callable[["CommandContext"], Awaitable["CommandResult"]]
    type: str                                # CommandType.* 之一
    usage: str = ""                          # 一行用法示例
    arg_hint: str = ""                       # 参数形式提示
    hidden: bool = False                     # /help 是否隐藏


@dataclass
class CommandContext:
    """命令执行时收到的上下文。"""

    session: "Session"
    app_config: "AppConfig"
    args: list[str] = field(default_factory=list)
    renderer: "Renderer" = field(default=None)  # type: ignore[assignment]
    # 第五阶段：权限策略实例（可选）。/permission 命令族用。
    policy: object = field(default=None)
    # 第七阶段：项目指令加载器（可选）。/instructions 命令用。
    instructions: object = field(default=None)
    # 第七阶段：reload / 记忆刷新时重建 system_prompt 的 callable。
    rebuild_system_prompt: object = field(default=None)
    # 第八阶段：上下文压缩器（可选）。/compact 命令用。
    compactor: object = field(default=None)
    # 第九阶段：会话存档器（可选）。/session 命令族用。
    archive: object = field(default=None)
    # 第九阶段：长期记忆管理器（可选）。/memory 命令族 + /status 用。
    memory_manager: object = field(default=None)


@dataclass(frozen=True)
class CommandResult:
    """命令执行结果。

    Attributes:
        should_exit: True 时通知 REPL 退出。
        prompt_text: PROMPT 类命令构造的"伪用户输入"；REPL 收到后送入
            run_turn。两者互斥；普通 LOCAL/STATEFUL 命令两者都为默认。
    """

    should_exit: bool = False
    prompt_text: str | None = None


# ---------- 全局表 ----------


COMMANDS: dict[str, Command] = {}


# ---------- 注册 ----------


def register(cmd: Command) -> None:
    """注册一条命令；任何冲突立即 raise CommandRegistrationError。

    校验顺序：
    1. type 合法。
    2. name 非空且不含空格。
    3. 自反检查（name 不在自己的 aliases 中）。
    4. name / 各 alias 在 COMMANDS 中已被其它 Command 占用 → 报错。
    5. 同对象重复注册视为幂等 noop（register_builtins 可被多处调用而不抛错）。
    """
    if cmd.type not in _VALID_TYPES:
        raise CommandRegistrationError(
            f"命令 /{cmd.name} 的 type={cmd.type!r} 非法，"
            f"必须是 {sorted(_VALID_TYPES)} 之一"
        )

    name = cmd.name.strip()
    if not name:
        raise CommandRegistrationError("命令 name 不能为空")
    if any(ch.isspace() for ch in name):
        raise CommandRegistrationError(f"命令 name 不能含空格：{name!r}")

    name_lc = name.lower()

    # 自反 alias 检查（spec AC3）
    aliases_lc = tuple(a.lower() for a in cmd.aliases)
    if name_lc in aliases_lc:
        raise CommandRegistrationError(
            f"命令 /{name} 的 aliases 包含自身 name；"
            "不允许自反引用"
        )
    # alias 内部去重检查
    if len(set(aliases_lc)) != len(aliases_lc):
        raise CommandRegistrationError(
            f"命令 /{name} 的 aliases 内部存在重复项：{cmd.aliases}"
        )

    # 收集所有 key（name + aliases）依次校验占用
    keys = [name_lc, *aliases_lc]
    for key in keys:
        existing = COMMANDS.get(key)
        if existing is None:
            continue
        # 幂等：同对象 / 等值（dataclass 字段全等）→ noop。
        # handler 是模块级函数 → 同次进程内 register_builtins() 多次调用，
        # 新构造的 Command 与旧的字段全等，frozen dataclass 默认 __eq__ 返回 True。
        if existing is cmd or existing == cmd:
            continue
        owner = existing.name
        raise CommandRegistrationError(
            f"命令注册冲突：{key!r} 已被 /{owner} 占用"
        )

    # 通过校验：写入全局表
    for key in keys:
        COMMANDS[key] = cmd


def unregister_all() -> None:
    """测试辅助：清空全局表。生产代码不应调用。"""
    COMMANDS.clear()


# ---------- 查询 ----------


def commands_by_type() -> dict[str, list[Command]]:
    """按 type 分桶返回**可见**命令；每桶按 name 升序。

    返回字典固定顺序：LOCAL → STATEFUL → PROMPT。空桶仍以空 list 出现，
    便于渲染层稳定处理。
    """
    seen: set[int] = set()
    grouped: dict[str, list[Command]] = {
        CommandType.LOCAL: [],
        CommandType.STATEFUL: [],
        CommandType.PROMPT: [],
    }
    for cmd in COMMANDS.values():
        if id(cmd) in seen:
            continue
        seen.add(id(cmd))
        if cmd.hidden:
            continue
        grouped.setdefault(cmd.type, []).append(cmd)
    for bucket in grouped.values():
        bucket.sort(key=lambda c: c.name)
    return grouped


def visible_command_names() -> list[str]:
    """返回所有 hidden=False 的命令 name，去重升序。

    供 Tab 补全候选与未知命令引导列表用。**别名不出现**。
    """
    seen: set[str] = set()
    out: list[str] = []
    for cmd in COMMANDS.values():
        if cmd.hidden:
            continue
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        out.append(cmd.name)
    out.sort()
    return out


# ---------- 分发 ----------


async def dispatch(line: str, ctx: CommandContext) -> CommandResult | None:
    """把一行输入按命令分发。

    Returns:
        None              —— 不是命令（行不以 / 开头），调用方应当对话处理。
        CommandResult     —— 命令已执行（含未知命令的情形）；按 should_exit
            / prompt_text 决定 REPL 后续行为。
    """
    if not line.startswith("/"):
        return None

    parts = line[1:].split()
    available = visible_command_names()

    if not parts:
        # 仅输入 "/" → 视作未知命令
        ctx.renderer.print_unknown_command("", available)
        return CommandResult()

    name, *args = parts
    name_lc = name.lower()
    cmd = COMMANDS.get(name_lc)
    if cmd is None:
        ctx.renderer.print_unknown_command(name, available)
        return CommandResult()

    ctx.args = args
    return await cmd.handler(ctx)
