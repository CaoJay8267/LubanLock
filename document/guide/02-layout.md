# 项目布局

## 规范布局

```text
<project>/
├── AGENTS.md                 # 指令（规范）
├── .agents/                  # 机器可读定义
│   ├── agent.yaml            # 必需
│   ├── knowledge/
│   ├── skills/<name>/SKILL.md
│   ├── agents/<name>.md
│   ├── tools/ models/ workflows/ tasks/
│   ├── mcp.json
│   ├── tests/
│   └── dotagent.lock
├── build/                    # 编译产物（生成）
├── dist/                     # 包与投影（生成）
└── reports/                  # 审计与可视化（生成）
```

解析入口可以是项目目录、`.agents/` 或旧的 `.agent/`。

## 兼容规则

| 旧写法 | 新写法 | 行为 |
| --- | --- | --- |
| `.agent/` | `.agents/` | 读取并给 `DTA910` 警告 |
| `.agents/subagents/` | `.agents/agents/` | 读取并给 `DTA910` 警告 |
| `.agents/instructions.md` | `AGENTS.md` | 读取并给 `DTA911` 警告 |
| `skills/<name>.md` | `skills/<name>/SKILL.md` | 不再支持（Agent Skills 要求目录） |

同时存在新旧目录时优先新布局；同时存在 `AGENTS.md` 与 `instructions.md` 时使用前者。

## 身份与预算

```yaml
# .agents/agent.yaml
apiVersion: dotagent/v1alpha1
kind: Agent
metadata:
  name: research-agent
  title: Research Agent
  description: 负责研究并给出有引用的结论。
  version: 0.1.0
  tags: [research]
spec:
  model: default
  defaults:
    temperature: 0.2
    max_steps: 12
  limits:
    knowledge_chars: 20000
    skill_count: 8
    tool_count: 8
    subagent_count: 4
  settings:
    audience: developers
```

预算默认值：`knowledge_chars=100000`、`skill_count=32`、`tool_count=64`、
`subagent_count=16`。超限在 `validate` 中是 warning，在 `test` 中是失败。

## 命名与引用

- 实体名：小写字母开头，允许单个 `-` 或 `_` 分隔，≤64；
- 技能名：只允许小写字母/数字/单连字符，且等于父目录名；
- 引用写实体名，例如 `tools: [web_search]`；
- 引用只在当前 Agent 内解析；子 Agent 引用根 Agent 的工具/技能/模型。

## 路径安全

声明的文件路径必须位于 `.agents/` 内（`AGENTS.md` 特例在项目根）：

```yaml
spec:
  instructions: {file: prompts/planner.md}
```

`../`、绝对路径、盘符会被拒绝（`DTA109`）。

## 资源目录

按约定，Agent 默认位于：

```text
resource/agent/<name>/
```

`resource/` 是 DotAgent 框架统一管理生成物的位置；其他 `kind` 预留给未来的
模板、数据集等资源。详见 [资源目录与签名](08-resources-and-signing.md)。
