# DotAgent

**声明式、可移植的 Agent 定义与构建工具链。**

在 `AGENTS.md` + `.agents/` 中定义一次 Agent，然后完成校验、编译、测试、打包、投影、
审计、可视化与部署。DotAgent 不调用模型、不执行工具、不提供 Agent Loop 或 Harness；
真正的运行由宿主负责。

```
AGENTS.md + .agents/ ──▶ 校验 ──▶ 编译（agent.json + 资产 + 指纹）
                                  ├──▶ 投影（json / markdown / claude / 插件）
                                  ├──▶ 打包（可复现 .zip / 目录 + 可选 Ed25519 签名）
                                  ├──▶ 测试（golden、确定性、自定义 checks）
                                  ├──▶ 审计 / 可视化（输出到 reports/）
                                  └──▶ 运行时视图（提示词、工具、工作流分层）
```

## 为什么

Agent 定义不应该被锁死在某一个宿主的目录里。DotAgent 把“定义”与“运行”分开：

- **声明式**：YAML 写结构，Markdown 写内容，不需要写 Python 来定义 Agent。
- **确定性**：排序输出、内容哈希、字节级可复现的包。
- **不执行用户代码**：加载与编译只读取 YAML/Markdown/JSON。
- **可移植**：中性 IR + 投影器；厂商格式只通过有依据的投影器输出。
- **可验证**：指纹、锁文件、包清单、逐文件校验、可选签名。
- **可观测**：静态审计报告 + Mermaid/HTML 可视化。
- **符合开放标准**：技能严格遵循 [Agent Skills](https://agentskills.io/specification)；
  指令使用 [AGENTS.md](https://agents.md/) 开放格式。

## 安装

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # POSIX
pip install -e .

# 可选：包签名
pip install -e ".[signing]"

dotagent --version
```

要求 Python 3.11+ 与 PyYAML；签名需要可选的 `cryptography`。

## 快速开始

```bash
dotagent init research-agent --template full
dotagent validate research-agent
dotagent test research-agent
dotagent compile research-agent
dotagent audit research-agent
dotagent visualize research-agent
dotagent deploy research-agent
```

默认生成到 `resource/agent/<name>/`：

```text
resource/agent/research-agent/
├── AGENTS.md                # Agent 指令（开放格式，可被同生态工具读取）
├── .agents/                 # 机器可读定义
│   ├── agent.yaml
│   ├── knowledge/
│   ├── skills/<name>/SKILL.md
│   ├── agents/<name>.md     # 子 Agent
│   ├── tools/ models/ workflows/ tasks/
│   ├── mcp.json             # MCP 服务声明
│   ├── tests/
│   └── dotagent.lock        # 可选锁文件
├── build/                   # 编译产物（生成）
├── dist/                    # 包与投影（生成）
└── reports/                 # 审计与可视化（生成）
```

`dotagent init --path DIR` 可指定任意目录。旧版 `.agent/` 仍可读取，但会给出弃用警告。

## Agent Skills 规范

技能目录必须是：

```text
.agents/skills/<name>/
├── SKILL.md          # 必须
├── scripts/          # 可选
├── references/       # 可选
└── assets/           # 可选
```

`SKILL.md` 只使用标准 front matter：

```yaml
---
name: web-research                 # 1-64 个 [a-z0-9-]，不能有首尾/连续连字符，必须与目录同名
description: 研究并引用一手来源。   # 必填，≤1024
license: MIT                       # 可选
compatibility: 需要网络访问         # 可选，≤500
metadata:                          # 可选，string→string
  owner: research
allowed-tools: web_search web_fetch # 可选，空格分隔（标准中为实验字段）
---

技能正文（Markdown）。
```

`dotagent validate` 会检查名称与目录一致、描述长度、工具引用等；`dotagent skill add`
会直接生成合规骨架。

> 说明：Agent Skills 标准只规定 `SKILL.md` 的结构，不规定 `.agent` 或 `.agents` 目录名。
> `.agents/` 是当前生态中多个客户端使用的约定；DotAgent 采用它，并保留 `.agent/` 兼容。
> `AGENTS.md` 是独立的开放指令格式，不是 Agent Skills 的一部分。

## 实体

| 实体 | 位置 | 说明 |
| --- | --- | --- |
| Agent | `.agents/agent.yaml` | 名称、版本、描述、模型、预算、设置 |
| 知识 | `.agents/knowledge/**` | Markdown/文本，可选 front matter |
| 技能 | `.agents/skills/<name>/SKILL.md` | 严格 Agent Skills |
| 子 Agent | `.agents/agents/<name>.md` | 轻量单文件，可引用工具/技能/模型 |
| 工具 | `.agents/tools/<name>.yaml` | 声明式 JSON Schema，无 handler |
| 模型 | `.agents/models/<name>.yaml` | 中性字段 + 不透明 `provider_options` |
| 工作流 | `.agents/workflows/<name>.yaml` | 类型化 DAG |
| 任务 | `.agents/tasks/<name>.yaml` | 提示词、I/O schema、示例 |
| MCP | `.agents/mcp.json` | 标准 `mcpServers` 映射 |

完整字段见 [架构文档](document/architecture.md)。

### 工具声明

```yaml
apiVersion: dotagent/v1alpha1
kind: Tool
metadata:
  name: web_search
  description: 搜索公开网页。
spec:
  input_schema:
    type: object
    additionalProperties: false
    required: [query]
    properties:
      query: {type: string, minLength: 1}
  annotations:
    read_only: true
    open_world: true
```

工具只是声明；执行由宿主或 MCP 提供。

### MCP 声明

`.agents/mcp.json` 使用标准形状：

```json
{
  "mcpServers": {
    "docs": {
      "type": "http",
      "url": "https://example.com/mcp"
    }
  }
}
```

`dotagent validate` 会检查 JSON、传输字段、`http://` 安全提示；Claude 投影会生成 `.mcp.json`。

## CLI

| 命令 | 作用 |
| --- | --- |
| `dotagent init [NAME] [--path DIR] [--template minimal\|full]` | 创建 Agent（默认 `resource/agent/<name>`） |
| `dotagent list` | 列出 `resource/agent` 下的 Agent |
| `dotagent validate [PATH] [--strict] [--json]` | Schema + 语义校验 |
| `dotagent compile [PATH] [-o DIR] [--check\|--locked\|--incremental]` | 编译、校验产物、锁校验、增量 |
| `dotagent lock [PATH]` | 写入 `.agents/dotagent.lock` |
| `dotagent project [PATH] --target NAME [-o DIR]` | 投影（`--list` 查看目标） |
| `dotagent package [PATH] [--format zip\|dir] [--sign-key FILE] [--verify]` | 可复现打包、可选签名 |
| `dotagent verify PACKAGE [--public-key FILE] [--require-signature]` | 校验包与签名 |
| `dotagent keygen [--out DIR]` | 生成 Ed25519 密钥对 |
| `dotagent test [PATH] [--suite NAME] [-k TEXT] [--update]` | 声明式测试 |
| `dotagent audit [PATH] [--fail-on error\|warning\|never]` | 静态审计，写 `reports/audit/` |
| `dotagent visualize [PATH]` | Mermaid/HTML，写 `reports/visual/` |
| `dotagent import SOURCE [--from claude\|package] [--out DIR]` | 从 Claude Code 或包反向导入 |
| `dotagent skill add NAME --agent PATH` | 添加合规技能 |
| `dotagent reset [PATH] --yes [--build]` | 清理生成物并可选重建 |
| `dotagent deploy [PATH] [--target NAME] [--sign-key FILE]` | 打包 + 投影到 `dist/` |
| `dotagent inspect [PATH] [--json]` | 摘要或完整 IR |
| `dotagent schema [NAME] [--check]` | 打印/自检内置 JSON Schema |

全局 `--resource DIR` 可替换 `resource/` 根目录（默认 `resource`）。路径参数既可以是
项目路径，也可以是 `resource/agent/` 下的 Agent 名称。

退出码：`0` 成功，`1` 校验/测试/校验失败，`2` 用法错误，`3` 内部错误。

## 编译产物与锁文件

```text
<project>/build/
├── agent.json      # 规范 IR：agent + assets 元数据 + 指纹
└── assets/         # AGENTS.md、知识、技能、任务提示词
```

- `agent.json` 的 SHA-256 指纹覆盖定义与所有资产哈希，跨机器稳定。
- `dotagent lock` 生成 `.agents/dotagent.lock`，记录每个源文件的 SHA-256。
- `dotagent compile --locked` 在锁与源码不一致时拒绝构建。
- `dotagent compile --incremental` 先做 stat 快路径，命中锁时直接复用现有构建。

## 打包与签名

```text
manifest.json
agent.json
assets/...
signature.json   # 仅签名时存在
```

- 默认无签名；ZIP 条目排序、固定时间戳与压缩参数，字节级可复现。
- `--sign-key private.pem` 对 `manifest.json` 精确字节做 Ed25519 签名。
- `verify --public-key public.pem --require-signature` 强制验签。
- 签名文件不进入 `manifest.entries`，避免自引用；校验器单独处理。

## 审计与可视化

`dotagent audit` 组合校验诊断与静态启发式：明文密钥、危险工具注解、明文 HTTP 端点、
MCP 传输缺失、预算接近上限、占位模型等。输出：

```text
<project>/reports/audit/audit.json
<project>/reports/audit/audit.md
```

`dotagent visualize` 输出：

```text
<project>/reports/visual/agent.mmd
<project>/reports/visual/workflows/<name>.mmd
<project>/reports/visual/index.html
```

Mermaid 图可在 GitHub/VS Code 直接查看；HTML 版本通过 CDN 加载 Mermaid，离线时可看 `.mmd`。

## 导入（闭环）

```bash
# 从 Claude Code 项目导入：CLAUDE.md、.claude/agents、.claude/skills、.claude/commands、.mcp.json
dotagent import ./legacy-claude --from claude --out resource/agent/legacy

# 从 DotAgent 包导入（可编辑工程）
dotagent import dist/research-agent-0.1.0.zip --from package --out resource/agent/restored
```

导入是刻意有损的：无法映射的字段会以 warning 报告，未知 Claude 工具会生成通用声明，
需要人工补全 schema。

## Python API

```python
import dotagent

agent = dotagent.load_agent("resource/agent/research-agent")
compiled = dotagent.compile_agent("resource/agent/research-agent")
result = dotagent.project_agent(compiled, "claude")
runtime = dotagent.AgentRuntime(compiled)

print(compiled.fingerprint)
print(runtime.system_prompt())
print(runtime.workflow_plan("quick-research").layers)

report, files = dotagent.audit_agent("resource/agent/research-agent")
```

插件通过 entry point 注册：

```toml
[project.entry-points."dotagent.projectors"]
my-target = "my_package:MyProjector"

[project.entry-points."dotagent.runtime_adapters"]
my-host = "my_package:MyHostAdapter"
```

## 测试

```bash
python -m unittest discover -s tests -t tests
```

`tests/` 只包含 `test_*.py`；测试所需的通用夹具放在
`dotagent/testkit/fixtures.py`，供下游项目复用。

`dotagent test` 的内置套件：`workflow`、`limits`、`golden`、`determinism`、
`packaging`、`custom`（`tests/checks.yaml`）。`--update` 只更新 golden 快照。

## 项目结构

```text
dotagent/
├── __init__.py          # 公共 API
├── __main__.py
├── cli.py               # 命令行
├── core/                # 错误、模型、工具、路径、图算法
├── spec/                # 加载、构建、校验、JSON Schema
├── build/               # 编译、打包、锁文件、签名
├── project/             # 投影器与注册表
├── runtime/             # 只读运行时视图与适配器
├── testkit/             # 测试套件、checks、fixtures
├── audit/               # 审计与可视化
├── integrations/        # Claude / 包导入
└── templates/           # init 模板
```

## 安全边界

- 不执行用户代码；只读取声明式文件。
- 路径必须位于项目内；ZIP 条目拒绝绝对路径与 `..`。
- 拒绝 YAML 锚点/别名，避免别名展开导致产物膨胀。
- 源文件 8 MiB、资产 64 MiB、包 256 MiB 上限。
- 审计中的“密钥”判断是启发式，不能替代真正的 secret 扫描。

## 非目标

LLM 推理、Agent Loop、Harness、Sandbox、多租户、长期记忆、工具 Handler、厂商 SDK 绑定。
DotAgent 只负责“定义与构建”。

## 文档

- [新手入门](document/guide/README.md)
- [架构、规范与决策记录](document/architecture.md)
- [示例 Agent](examples/research-agent)

## License

MIT
