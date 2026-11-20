# Luban Lock（鲁班锁）

**声明式、可移植的 Agent 定义与构建工具链。**

> 鲁班锁不靠钉子或胶水：构件靠结构互相咬合，拆开仍是独立零件。
> Luban Lock 用同样的方式组合 Agent：指令、实体、技能、MCP 与工具各自独立，
> 通过结构组合成一个可校验、可编译、可移植的整体。

在 `AGENTS.md` + `.agents/` 中定义一次 Agent，然后完成校验、编译、测试、打包、投影、
审计、可视化与部署。Luban Lock 不调用模型、不执行工具、不提供 Agent Loop 或 Harness；
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

Agent 定义不应该被锁死在某一个宿主的目录里。Luban Lock 把“定义”与“运行”分开：

- **声明式**：YAML 写结构，Markdown 写内容，不需要写 Python 来定义 Agent。
- **确定性**：排序输出、内容哈希、字节级可复现的包。
- **不执行用户代码**：加载与编译只读取 YAML/Markdown/JSON。
- **可移植**：中性 IR + 投影器；厂商格式只通过有依据的投影器输出。
- **可验证**：指纹、锁文件、包清单、逐文件校验、可选签名。
- **可观测**：静态审计报告 + Mermaid/HTML 可视化。
- **符合开放标准**：技能严格遵循 [Agent Skills](https://agentskills.io/specification)；
  指令使用 [AGENTS.md](https://agents.md/) 开放格式。

## v1.0 改名

- 因 PyPI `dotagent` 已被占用，项目更名为 **Luban Lock（鲁班锁）**
- 发行名、导入名与 CLI 统一为 `lubanlock`
- 格式标识改为 `lubanlock/v1alpha1`、`lubanlock.lock`、`LUBANLOCK_*`

## v0.8 新增

- DSH 子 Agent 生成独立 preset（根 preset + 子 Agent preset）
- 审计规则 `when`/`unless` 条件与 Mermaid 依赖图
- PKI 缓存加密（AES-GCM，密钥 `LUBANLOCK_CACHE_KEY` 或本地 key 文件）

## v0.7 新增

- RFC 3161 请求支持显式 `--timestamp-proxy`，不再依赖环境变量
- PKI 缓存目录/文件权限收紧（0700/0600，尽力而为）
- Copilot 路径级 `*.instructions.md` 投影与导入

## v0.6 新增

- 真实 RFC 3161 token 校验与本地 CRL 分发点集成测试
- OCSP/CRL 代理、超时与离线缓存（`--offline`、`--proxy`）
- 共享库二进制资产与包导入往返
- 审计规则依赖图（`depends_on`）
- GitHub Copilot 投影与导入

## v0.5 新增

- 共享库现在导入工具、模型、技能与知识，并把外部文件纳入锁
- MCP 增加旧版 SSE 与 WebSocket 传输
- 证书校验增加 CRL 分发点自动获取、OCSP 与 RFC 3161 时间戳
- DSH 映射技能、MCP 管理器和子 Agent 插件
- OpenAI 复杂 schema 使用 `FunctionTool`，子 Agent 生成 `handoffs`
- 审计规则增加分类与参数 schema

## v0.4 新增（可选能力）

- 跨 Agent 共享库：`spec.uses` 显式导入另一工程的工具/模型，支持 SemVer 约束
- MCP 发现缓存（`--refresh`/`--max-age`）与 SSE 响应解析
- 证书吊销列表（CRL）校验：`lubanlock verify --crl`
- DSH 投影自动映射已知工具插件
- OpenAI 投影为简单 schema 生成 `tools.py` handler 脚手架
- 审计规则可插件化（`lubanlock.audit_rules` entry point）

## v0.3 新增

- `extends` 实体继承与 `agents` / `uses` 嵌套子 Agent（有环检测与深度上限）
- MCP 工具自动发现（stdio/http）与 schema 导入
- 内容哈希缓存：mtime 变化但内容未变时不会重建
- 证书链签名与密钥轮换（Ed25519 / X.509，默认仍无签名）
- 新投影目标：`dsh`、`openai`、`openai-chat`；DSH/OpenAI 反向导入
- `.agents/audit.yaml` 可配置审计规则

## 安装

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # POSIX
pip install lubanlock          # 发布后可用
# 或从源码开发：
pip install -e .

# 可选：包签名
pip install -e ".[signing]"

lubanlock --version
```

要求 Python 3.11+ 与 PyYAML；签名需要可选的 `cryptography`。

## 发布与安装

从源码构建 wheel 并安装：

```bash
python -m pip wheel . --no-deps -w dist
python -m pip install dist/lubanlock-1.0.0-py3-none-any.whl
lubanlock --version
```

开发环境：

```bash
python -m pip install -e ".[signing,dev]"
ruff check lubanlock tests scripts
mypy lubanlock
python -m unittest discover -s tests -t tests
coverage run -m unittest discover -s tests -t tests && coverage report
```

版本策略：SemVer；`.agents/` 规范版本（`lubanlock/v1alpha1`）与包版本独立演进。
变更记录见 [CHANGELOG.md](CHANGELOG.md)。CI 覆盖 Python 3.11–3.14 的
Ubuntu/Windows 矩阵、覆盖率门禁、类型检查与安装后冒烟测试。

## 快速开始

```bash
lubanlock init research-agent --template full
lubanlock validate research-agent
lubanlock test research-agent
lubanlock compile research-agent
lubanlock audit research-agent
lubanlock visualize research-agent
lubanlock deploy research-agent
```

默认生成到 `resource/agent/<name>/`：

```text
resource/agent/research-agent/
├── AGENTS.md                # Agent 指令（开放格式，可被同生态工具读取）
├── .agents/                 # 机器可读定义
│   ├── agent.yaml
│   ├── knowledge/
│   ├── skills/<name>/SKILL.md
│   ├── agents/<name>.md     # 子 Agent（可嵌套）
│   ├── tools/ models/ workflows/ tasks/
│   ├── mcp.json             # MCP 服务声明
│   ├── audit.yaml           # 审计规则（可选）
│   ├── tests/
│   ├── .cache/              # 增量构建缓存（生成）
│   └── lubanlock.lock        # 可选锁文件
├── build/                   # 编译产物（生成）
├── dist/                    # 包与投影（生成）
└── reports/                 # 审计与可视化（生成）
```

`lubanlock init --path DIR` 可指定任意目录。旧版 `.agent/` 仍可读取，但会给出弃用警告。

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

`lubanlock validate` 会检查名称与目录一致、描述长度、工具引用等；`lubanlock skill add`
会直接生成合规骨架。

> 说明：Agent Skills 标准只规定 `SKILL.md` 的结构，不规定 `.agent` 或 `.agents` 目录名。
> `.agents/` 是当前生态中多个客户端使用的约定；Luban Lock 采用它，并保留 `.agent/` 兼容。
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

### 复用与嵌套

结构化实体（子 Agent、工具、模型、工作流、任务）支持 `extends`：

```yaml
# tools/echo-upper.yaml
apiVersion: lubanlock/v1alpha1
kind: Tool
extends: echo          # 同种类实体，继承 spec/metadata（名称除外）
metadata:
  name: echo-upper
  description: 返回大写文本。
```

子 Agent 用 `agents` 或 `uses` 组合子 Agent：

```yaml
---
name: reviewer
description: 审阅草稿。
uses: [helper]        # 等价于 agents: [helper]
extends: base-reviewer # 也可继承另一个子 Agent 的提示词与配置
---
```

解析规则：同种类、单层作用域、有环即报错（`DTA321`/`DTA323`），嵌套深度上限 8
（`DTA324`），继承链上限 16（`DTA322`）。全部静态可判定。

### 跨 Agent 共享库

`spec.uses` 显式导入另一个 Luban Lock 工程的 **工具与模型**：

```yaml
spec:
  uses:
    - path: ../shared-tools
      version: ">=0.2.0,<0.3.0"
```

- 路径相对当前工程，版本读取被引用工程的 `metadata.version`；
- 版本约束支持 `>= <= > < == != ~= ^` 与逗号组合；
- 只导入 `tools`、`models`、`skills` 与 `knowledge`；
- 技能/知识资产会以 `libraries/<name>/...` 命名空间进入编译产物；
- 本地或库之间出现重名报 `DTA333`，不静默覆盖；
- 含共享库的工程禁用增量快速路径，`--locked` 仍通过指纹发现变化；
- 锁文件会记录外部库文件哈希（`libraries/<name>/...`）。

### MCP

`.agents/mcp.json` 使用标准 `mcpServers` 形状。连接 server 自动发现工具：

```bash
lubanlock mcp discover research-agent --write --json
```

- 支持 `stdio`、`http`、旧版 `sse` 与 `ws`；
- HTTP 响应支持 JSON 与 `text/event-stream`（SSE）；
- WebSocket 需要额外安装 `lubanlock[mcp]`；
- `tools/list` 的 `inputSchema` 写成 `.agents/tools/<server>-<tool>.yaml`；
- 不执行发现的工具，只做 schema 导入；
- 结果缓存到 `.agents/.cache/mcp/`，`--refresh` 强制刷新，`--max-age` 控制 TTL；
- HTTP 响应支持 JSON 与 `text/event-stream`（SSE）；
- 命令不经过 shell，带超时与输出大小限制。

### 工具声明

```yaml
apiVersion: lubanlock/v1alpha1
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

`lubanlock validate` 会检查 JSON、传输字段、`http://` 安全提示；Claude 投影会生成 `.mcp.json`。

## CLI

| 命令 | 作用 |
| --- | --- |
| `lubanlock init [NAME] [--path DIR] [--template minimal\|full]` | 创建 Agent（默认 `resource/agent/<name>`） |
| `lubanlock list` | 列出 `resource/agent` 下的 Agent |
| `lubanlock validate [PATH] [--strict] [--json]` | Schema + 语义校验 |
| `lubanlock compile [PATH] [-o DIR] [--check\|--locked\|--incremental]` | 编译、校验产物、锁校验、增量 |
| `lubanlock lock [PATH]` | 写入 `.agents/lubanlock.lock`（内容哈希） |
| `lubanlock project [PATH] --target NAME [-o DIR]` | 投影（`--list` 查看目标） |
| `lubanlock package [PATH] [--format zip\|dir] [--sign-key FILE] [--sign-cert CERT] [--timestamp-url URL] [--verify]` | 可复现打包、签名、时间戳 |
| `lubanlock verify PACKAGE [--public-key FILE] [--ca-cert FILE] [--crl FILE] [--fetch-crl] [--ocsp] [--timestamp-cert FILE]` | 校验包、链、CRL/OCSP/时间戳 |
| `lubanlock keygen [--out DIR] [--chain\|--rotate --root-key K --root-cert C]` | 生成密钥、证书链或轮换证书 |
| `lubanlock test [PATH] [--suite NAME] [-k TEXT] [--update]` | 声明式测试 |
| `lubanlock audit [PATH] [--fail-on error\|warning\|never]` | 静态审计，读 `.agents/audit.yaml` |
| `lubanlock visualize [PATH]` | Mermaid/HTML，写 `reports/visual/` |
| `lubanlock mcp discover [PATH] [--server NAME] [--write] [--refresh] [--max-age SEC]` | 连接 MCP server 导入工具 schema，带缓存 |
| `lubanlock import SOURCE [--from claude\|package\|dsh\|openai\|copilot] [--out DIR]` | 从 Claude Code、DSH、OpenAI、Copilot 或包反向导入 |
| `lubanlock skill add NAME --agent PATH` | 添加合规技能 |
| `lubanlock reset [PATH] --yes [--build]` | 清理生成物并可选重建 |
| `lubanlock deploy [PATH] [--target NAME] [--sign-key FILE]` | 打包 + 投影到 `dist/` |
| `lubanlock inspect [PATH] [--json]` | 摘要或完整 IR |
| `lubanlock schema [NAME] [--check]` | 打印/自检内置 JSON Schema |

全局 `--resource DIR` 可替换 `resource/` 根目录（默认 `resource`）。路径参数既可以是
项目路径，也可以是 `resource/agent/` 下的 Agent 名称。

退出码：`0` 成功，`1` 校验/测试/校验失败，`2` 用法错误，`3` 内部错误。

## 投影目标

| 目标 | 输出 |
| --- | --- |
| `json` | 中性 `agent.json` + `assets/` |
| `markdown` | 人类可读文档包 |
| `claude` | `CLAUDE.md`、`.claude/agents`、`.claude/skills`、`.claude/commands`、`.mcp.json` |
| `dsh` | DeepSeek Harness 预设 bundle（`package.json` + `cordis.patch.yml`）；persona + 已知工具插件 |
| `openai` | OpenAI Agents SDK 脚手架（`agent.py` + `tools.py` + `tools.json`） |
| `openai-chat` | Chat Completions 请求骨架（model + messages + tools） |
| `copilot` | GitHub Copilot `.github/copilot-instructions.md` + `.github/instructions/*.instructions.md` |

DSH 会映射已知工具名、技能文件系统、MCP manager 与子 Agent；每个直接子 Agent 会生成
独立 preset，根 preset 仍负责 persona。OpenAI 为简单 schema 生成 `@function_tool`，
复杂 schema 生成 `FunctionTool`，直接子 Agent 变成 `handoffs`。

## 编译产物与锁文件

```text
<project>/build/
├── agent.json      # 规范 IR：agent + assets 元数据 + 指纹
└── assets/         # AGENTS.md、知识、技能、任务提示词
```

- `agent.json` 的 SHA-256 指纹覆盖定义与所有资产哈希，跨机器稳定。
- `lubanlock lock` 生成 `.agents/lubanlock.lock`，记录每个源文件的 SHA-256。
- `lubanlock compile --locked` 在锁与源码不一致时拒绝构建。
- `lubanlock compile --incremental` 先查 `.agents/.cache/sources.json`：mtime 未变直接复用缓存，
  mtime 变了才重新哈希；内容哈希一致就跳过编译。**touch 文件不再导致重建**。

## 打包与签名

```text
manifest.json
agent.json
assets/...
signature.json   # 仅签名时存在
```

- 默认无签名；ZIP 条目排序、固定时间戳与压缩参数，字节级可复现。
- `--sign-key private.pem` 对 `manifest.json` 精确字节做 Ed25519 签名。
- 证书模式：`--sign-key signing-key.pem --sign-cert signing.pem`，签名内携带叶证书与中间链；
  `verify --ca-cert root-ca.pem --require-signature` 验证证书链与签名。
- `verify --crl revoked.crl` 检查吊销列表（PEM/DER）；
- `verify --fetch-crl` 从证书 CRL 分发点自动获取；
- `verify --ocsp [--ocsp-url URL]` 检查 OCSP；
- `verify --offline` 只读 PKI 缓存，`--proxy URL` 为 CRL/OCSP 指定代理；
- `package --timestamp-url URL [--timestamp-proxy URL]` 获取 RFC 3161 时间戳，`verify --timestamp-cert` 验证。
- `keygen --chain` 生成 root CA + 叶证书；`keygen --rotate` 在同一个 root 下签发新叶证书，
  实现密钥轮换（旧包和新包都可用同一 trust anchor 验证）。
- 默认仍无签名，未签名包保持可复现；证书链仅支持 Ed25519。

## 审计与可视化

`lubanlock audit` 组合校验诊断与静态启发式：明文密钥、危险工具注解、明文 HTTP 端点、
MCP 传输缺失、预算接近上限、占位模型等。规则可在 `.agents/audit.yaml` 配置：

```yaml
apiVersion: lubanlock/v1alpha1
kind: AuditConfig
rules:
  AUD001: {enabled: false}
  AUD006: {severity: error}
  AUD900: {depends_on: [AUD006]}
secret_patterns: ['TOKEN-[0-9]+']
risky_tool_names: [bash, shell]
budget_warning_ratio: 0.8
ignore_paths: ['knowledge/private/*']
```

输出：

```text
<project>/reports/audit/audit.json
<project>/reports/audit/audit.md
```

第三方规则通过 `lubanlock.audit_rules` entry point 注册，实现
`lubanlock.audit.AuditRule`（`code` + `run`，可选 `parameter_schema`），即可被同一份
`audit.yaml` 开关、调级、分类与传参。

`lubanlock visualize` 输出：

```text
<project>/reports/visual/agent.mmd
<project>/reports/visual/workflows/<name>.mmd
<project>/reports/visual/index.html
```

Mermaid 图可在 GitHub/VS Code 直接查看；HTML 版本通过 CDN 加载 Mermaid，离线时可看 `.mmd`。

## 导入（闭环）

```bash
# 从 Claude Code 项目导入：CLAUDE.md、.claude/agents、.claude/skills、.claude/commands、.mcp.json
lubanlock import ./legacy-claude --from claude --out resource/agent/legacy

# 从 Luban Lock 包导入（可编辑工程）
lubanlock import dist/research-agent-0.1.0.zip --from package --out resource/agent/restored
```

导入是刻意有损的：无法映射的字段会以 warning 报告，未知 Claude 工具会生成通用声明，
需要人工补全 schema。

## Python API

```python
import lubanlock

agent = lubanlock.load_agent("resource/agent/research-agent")
compiled = lubanlock.compile_agent("resource/agent/research-agent")
result = lubanlock.project_agent(compiled, "claude")
runtime = lubanlock.AgentRuntime(compiled)

print(compiled.fingerprint)
print(runtime.system_prompt())
print(runtime.workflow_plan("quick-research").layers)

report, files = lubanlock.audit_agent("resource/agent/research-agent")
```

插件通过 entry point 注册：

```toml
[project.entry-points."lubanlock.projectors"]
my-target = "my_package:MyProjector"

[project.entry-points."lubanlock.runtime_adapters"]
my-host = "my_package:MyHostAdapter"
```

## 测试

```bash
python -m unittest discover -s tests -t tests
```

`tests/` 只包含 `test_*.py`；测试所需的通用夹具放在
`lubanlock/testkit/fixtures.py`，供下游项目复用。

`lubanlock test` 的内置套件：`workflow`、`limits`、`golden`、`determinism`、
`packaging`、`custom`（`tests/checks.yaml`）。`--update` 只更新 golden 快照。

## 项目结构

```text
lubanlock/
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
Luban Lock 只负责“定义与构建”。

## 文档

- [新手入门](document/guide/README.md)
- [架构、规范与决策记录](document/architecture.md)
- [示例 Agent](examples/research-agent)

## License

MIT
