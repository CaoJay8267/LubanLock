# 快速开始

## 1. 安装

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # POSIX
pip install -e .
lubanlock --version
```

需要 Python 3.11+。仅运行签名功能时需要额外的 `cryptography`：

```bash
pip install -e ".[signing]"
```

## 2. 创建 Agent

```bash
lubanlock init research-agent
```

默认生成到 `resource/agent/research-agent/`。也可以指定路径：

```bash
lubanlock init research-agent --path ./my-agents/research-agent
```

模板：

- `minimal`（默认）：`AGENTS.md`、`agent.yaml`、模型占位；
- `full`：包含知识、技能、子 Agent、工具、模型、工作流、任务、MCP 与测试检查。

## 3. 目录速览

```text
resource/agent/research-agent/
├── AGENTS.md
└── .agents/
    ├── agent.yaml
    └── models/default.yaml
```

## 4. 校验

```bash
lubanlock validate research-agent
```

输出示例：

```text
research-agent  .../resource/agent/research-agent
entities: 1 skills, 1 tools, 1 models
no problems found
```

有错误时会给出稳定代码与 JSON Pointer，例如：

```text
[error] DTA301 .agents/agent.yaml /spec/model: unknown model 'missing'
```

## 5. 测试

```bash
lubanlock test research-agent
```

内置套件：工作流、预算、golden、确定性、打包、自定义 checks。首次运行 golden 会跳过；
确认输出后执行：

```bash
lubanlock test research-agent --update
```

## 6. 编译

```bash
lubanlock compile research-agent
```

产物在 `build/`：

```text
build/
├── agent.json
└── assets/
```

`--check` 用于 CI 检查产物是否过期：

```bash
lubanlock compile research-agent --check
```

## 7. 审计与可视化

```bash
lubanlock audit research-agent
lubanlock visualize research-agent
```

报告在 `reports/audit/`，图在 `reports/visual/`。

## 8. 下一步

- 写第一个技能：[编写技能](03-skills.md)
- 了解目录约定：[项目布局](02-layout.md)
- 接入 Claude Code：[CLI 参考](06-cli.md)
