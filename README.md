# MewCode

终端 AI 编程助手。**第一阶段：纯对话 REPL**。

支持 Anthropic 协议（含 extended thinking）和 OpenAI 协议两种后端，
通过项目级 YAML 配置文件管理多个命名供应商。

## 安装

```bash
pip install -e .
```

需要 Python 3.10 及以上。

## 配置

复制 `mewcode.yaml.example` 为 `mewcode.yaml`，填入你的 API key：

```yaml
default: deepseek-anthropic

providers:
  deepseek-anthropic:
    protocol: anthropic
    model: deepseek-v4-pro[1m]
    base_url: https://api.deepseek.com/anthropic
    api_key: sk-your-key-here
```

`mewcode.yaml` 已被 `.gitignore` 屏蔽，不会被提交。

## 启动

在含 `mewcode.yaml` 的目录下：

```bash
mewcode
```

## 内置命令

- `/help` — 列出所有命令
- `/exit` 或 `/quit` — 退出
- `/clear` — 清空当前会话历史
- `/think on|off` — 开关 extended thinking（仅 Anthropic 协议）
- `/providers` — 列出已配置的供应商
- `/provider <name>` — 切换到指定供应商（清空历史）

## 退出码

- `0` — 正常退出
- `1` — 配置错误
- `2` — 未预期异常
