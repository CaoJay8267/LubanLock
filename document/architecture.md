# DotAgent 架构与决策记录（v0.3.0）

> 本文档回答三个问题：提案是否合理、是否已实现、边界在哪里。行为以
> `dotagent/` 源码与 `tests/` 为准。

## 0. 提案核验（16 项）

### 0.1 事实澄清

- **Agent Skills 标准**（[agentskills.io/specification](https://agentskills.io/specification)）
  只规定技能目录与 `SKILL.md`：`name`、`description`、`license`、`compatibility`、
  `metadata`、`allowed-tools`；`name` 必须与父目录同名。它**不规定** `.agent` 或
  `.agents` 目录名。
- **AGENTS.md**（[agents.md](https://agents.md/)）是独立的开放指令格式，不是 Agent Skills
  的一部分。
- 但 `.agents/skills/<name>/SKILL.md` 已成为多个客户端的实际发现约定（Augment、dbt 等）。
  因此本版采纳 `.agents/` 作为规范目录，同时保留 `.agent/` 兼容，并把 `AGENTS.md` 作为
  Agent 指令的规范入口。

### 0.2 逐项结果

| # | 提案 | 结果 |
| --- | --- | --- |
| 1 | 包根目录分类 | `core/ spec/ build/ project/ runtime/ testkit/ audit/ integrations/ templates/`；根目录仅 `__init__.py`、`__main__.py`、`_version.py`、`cli.py`、`py.typed` |
| 2 | tests 只放 `test_*.py` | 已删除 `tests/__init__.py` 与 `tests/helpers.py`；通用夹具迁至 `dotagent/testkit/fixtures.py` |
| 3 | 遵循 Agent Skills | 技能严格按标准字段与目录规则；单文件技能移除；名称/目录一致性为错误 |
| 4 | `document/guide/` | 已创建中文入门指南 |
| 5 | 示例根目录 `AGENTS.md`、`.agents` | 示例改为 `AGENTS.md` + `.agents/`；`.agent/` 仅兼容 |
| 6 | `resource/<kind>/<agent-name>/.agents/...` | 采用 `resource/agent/<name>/.agents/`；生成物在项目内 `build/ dist/ reports/` |
| 7 | 默认生成到 `resource/agent/` | `dotagent init` 默认路径 |
| 8 | `scripts/main.py` | 交互菜单 + 非交互转发：重置编排、加 Skill、审计、可视化、部署 |
| 9 | 审计/可视化 | `dotagent audit` / `dotagent visualize`，输出到 `reports/` |
| 10 | 子 Agent 在 `.agents/agents/` | 已采用；`subagents/` 为弃用别名 |
| 11 | 文档中文 | README、架构、guide 均为中文（代码标识符保留英文） |
| 12 | MCP 声明与导入 | `.agents/mcp.json`、校验、Claude 投影、Claude 导入 |
| 13 | `dotagent import` | 支持 Claude Code 与 DotAgent 包 |
| 14 | lockfile 与增量 | `.agents/dotagent.lock`、`--locked`、`--incremental` |
| 15 | 更多 JSON Schema 关键字 + `--check` | 新增 `patternProperties`、`propertyNames`、`prefixItems`、`contains`、`dependentRequired`、`dependentSchemas`、`if/then/else`；`dotagent schema --check` |
| 16 | 可选签名 | Ed25519（`cryptography` 可选依赖），默认无签名、可复现 |

## 1. 设计原则

1. **单一事实源**：`AGENTS.md` + `.agents/`；构建、投影、包均可重建。
2. **声明式、无代码执行**：只读 YAML/Markdown/JSON。
3. **确定性**：排序、哈希、固定 ZIP 时间戳；签名默认也确定。
4. **严格而显式**：未知字段报错；未支持的 schema 关键字显式拒绝。
5. **边界清晰**：止步于定义与构建，不实现推理/Loop/Harness。
6. **可验证**：指纹、锁、清单、逐文件哈希、可选签名。
7. **可观测**：静态审计与可视化。
8. **开放标准优先**：Agent Skills、AGENTS.md、MCP、JSON Schema 2020-12 子集。

## 2. 总体架构

```text
AGENTS.md + .agents/
        │
        ▼
 loader ── 发现、front matter、YAML/JSON、MCP、布局兼容
        │
        ▼
 validator ── schema 子集 + 引用/工作流/预算/MCP/工具 schema
        │
        ▼
 builder ── 类型化不可变模型
        │
        ▼
 compiler ── 规范 IR + 资产 + SHA-256 指纹
        │
        ├── projector   json / markdown / claude / 插件
        ├── packager    zip / dir / 签名 / verify
        ├── testkit     built-in suites + checks.yaml
        ├── runtime     提示词、工具、工作流分层、adapter
        ├── audit       静态风险报告
        └── visualize   Mermaid / HTML
```

| 模块 | 职责 |
| --- | --- |
| `core/` | errors、models、utils、graph、layout 路径 |
| `spec/` | loader、builder、validator、JSON Schema 引擎与 12 份 schema |
| `build/` | compiler、packaging、lockfile、signing |
| `project/` | 投影协议、注册表、内置目标 |
| `runtime/` | 只读运行时视图与适配器注册 |
| `testkit/` | 测试套件、checks 解释器、fixtures |
| `audit/` | 审计与可视化 |
| `integrations/` | Claude Code / 包导入 |
| `templates/` | `init` 模板 |
| `cli.py` | 全部子命令 |

## 3. 规范（v0.2 基线，v0.3 扩展见第 5 节）

### 3.1 目录

```text
<project>/
├── AGENTS.md                 # 指令（规范）
├── .agents/                  # 机器可读定义
│   ├── agent.yaml
│   ├── knowledge/
│   ├── skills/<name>/SKILL.md
│   ├── agents/<name>.md
│   ├── tools/ models/ workflows/ tasks/
│   ├── mcp.json
│   ├── tests/
│   └── dotagent.lock
├── build/                    # 生成
├── dist/                     # 生成
└── reports/                  # 生成
```

解析规则：

- 传项目目录、`.agents/` 或 `.agent/` 均可；
- 同时存在 `.agents/` 与 `.agent/` 时优先 `.agents/`；
- `.agent/`、`subagents/`、`instructions.md` 给出弃用警告，仍可读取；
- `AGENTS.md` 优先于 `.agents/instructions.md`。

### 3.2 命名与引用

- 通用实体名：`^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$`（≤64）。
- 技能名：`^[a-z0-9]+(?:-[a-z0-9]+)*$`，且必须等于父目录名。
- 引用使用同名实体字符串；解析范围为当前 Agent。
- 重名 `DTA108`；未知引用 `DTA301`。

### 3.3 实体

#### Agent

```yaml
apiVersion: dotagent/v1alpha1
kind: Agent
metadata: {name, title?, description?, version?, labels?, tags?}
spec:
  instructions: AGENTS.md | instructions.md | {file|text}
  model: default
  defaults: {temperature?, max_steps?, max_output_tokens?}
  limits: {knowledge_chars?, skill_count?, tool_count?, subagent_count?}
  settings: {}
```

预算默认：`knowledge_chars=100000`、`skill_count=32`、`tool_count=64`、
`subagent_count=16`。

#### 技能（Agent Skills）

```yaml
---
name: web-research
description: 研究并引用一手来源。
license: MIT
compatibility: 需要网络访问
metadata: {owner: research}
allowed-tools: web_search web_fetch
---
正文
```

目录必须为 `skills/<name>/SKILL.md`；附件可放在任意子目录，构建时作为资产打包。
`allowed-tools` 按空格拆分为工具引用并校验存在性。

#### 子 Agent

`agents/<name>.md`：

```yaml
---
name: source-checker
description: 核验引用。
model: default
tools: [web_fetch]
skills: [citation-format]
---
指令正文
```

#### 工具

`tools/<name>.yaml`，`metadata.description` 必填，`spec.input_schema` 根类型必须为 object；
支持 `output_schema`、`annotations`、`source`（`builtin|mcp|http|external`）。
`source.kind=mcp` 且 `ref=server/...` 时校验 server 是否在 `mcp.json` 中声明。

#### 模型

`models/<name>.yaml`：`model` 必填；`provider` 为自由字符串；`provider_options` 原样保存，
不假设厂商 schema（显式 TODO）。

#### 工作流

节点种类：`agent | task | subagent | tool | human | join | workflow`；DAG、入口、可达性、
引用与嵌套调用环均校验；v0.2 仍不支持条件表达式/数据映射。

#### 任务

`tasks/<name>.yaml`：提示词、I/O schema、示例、技能/工具/工作流引用；示例输入会用
`input_schema` 校验。

#### MCP

`.agents/mcp.json` 使用标准 `{"mcpServers": {...}}`；校验 JSON、服务对象、`type`、
`url` 形状；审计对 `http://`、缺少传输字段给出提示。

### 3.4 指令来源

- 默认读取项目根 `AGENTS.md`；
- 兼容 `.agents/instructions.md`（弃用警告）；
- `spec.instructions` 支持字符串路径、`{file}`、`{text}`；
- 声明路径必须位于 `.agents/` 内（`AGENTS.md` 特例读取项目根）。

### 3.5 版本

- 当前 `dotagent/v1alpha1`；未知版本 `DTA107`，错误 kind `DTA111`。
- 所有文档 `additionalProperties: false`；未知字段报错。

## 4. 流水线

### 4.1 加载

- 布局解析、manifest、`AGENTS.md`、MCP、实体发现；
- YAML 使用去时间戳的 SafeLoader；拒绝锚点/别名；
- front matter 必须闭合；技能只接受目录形态；
- 加载期诊断不中断，汇总后统一输出。

### 4.2 校验

1. manifest/实体/MCP 的 JSON Schema 校验；
2. 构建通过 schema 的文档；
3. 域校验：引用、重名、技能目录、工作流、工具 schema、任务示例、预算、MCP、注解矛盾。

诊断码：`DTA1xx` 加载、`DTA2xx` schema、`DTA3xx` 语义、`DTA4xx` 预算、
`DTA5xx` 投影、`DTA6xx` 打包/锁、`DTA7xx` 测试、`DTA9xx` 警告/弃用。

### 4.3 编译

`agent.json`：

```json
{
  "apiVersion": "dotagent/v1alpha1",
  "kind": "CompiledAgent",
  "generator": {"name": "dotagent", "version": "0.2.0"},
  "fingerprint": "sha256:...",
  "agent": { "...": "..." },
  "assets": {"AGENTS.md": {"sha256": "...", "size": 0, "mediaType": "text/markdown"}}
}
```

指纹覆盖 `apiVersion + kind + agent + assets`，不含 generator 版本/时间戳。

### 4.4 锁文件与增量

- `dotagent lock` 写入 `.agents/dotagent.lock`：指纹 + 每个源文件（含技能附件）的
  SHA-256/size/mtime；
- `compile --locked`：锁与当前源码不一致则失败；
- `compile --incremental`：stat 快路径，命中锁且输出指纹一致时跳过完整编译；
- `reset` 清理 lock/build/dist/reports。

### 4.5 投影

纯函数：`CompiledAgent → ProjectionResult(files, warnings)`；写盘拒绝绝对路径、
`..`、重复路径。

- `json`：中性 IR + 资产；
- `markdown`：人读文档；
- `claude`：`CLAUDE.md`、`.claude/agents`、`.claude/skills`、`.claude/commands`、
  `.mcp.json`；
- 未知工具/模型映射为 warning，不臆造。

### 4.6 打包与签名

- 结构：`manifest.json`、`agent.json`、`assets/...`、可选 `signature.json`；
- ZIP 可复现；
- Ed25519 签署 `manifest.json` 字节；`signature.json` 不进入 manifest entries；
- `verify --public-key --require-signature` 强制验签；默认无签名可复现。

### 4.7 测试

内置：`workflow`、`limits`、`golden`、`determinism`、`packaging`、`custom`。
`--update` 更新 golden；`tests/` 只保留 `test_*.py`，夹具在包内。

### 4.8 运行时

`AgentRuntime` 提供指令、实体查询、系统提示词、工作流分层、IR 导出；不调用模型/工具。
`RuntimeAdapter` 通过 entry point 接入宿主对象模型，内置 `dict`。

### 4.9 审计与可视化

- 审计：校验诊断 + 密钥启发式、危险工具、明文端点、MCP 缺失、预算、占位模型；
- 可视化：结构图 + 每个工作流图，Mermaid 与 HTML；
- 报告写入项目 `reports/`（在 `resource/` 下时即满足统一归集）。

### 4.10 导入

- `--from claude`：`CLAUDE.md`、`.claude/agents`、`.claude/skills`、`.claude/commands`、
  `.mcp.json` → `AGENTS.md` + `.agents/`；未知工具生成通用声明并 warning；
- `--from package`：从 `agent.json` + 资产重建可编辑工程；
- `--from dsh`：读取旧版 `preset.yml` + `agent.cordis.yml`，或新版 bundle
  `cordis.patch.yml` 中 `@deepseek-ai/dsh-agent-preset` 的声明；persona 前缀为指令，
  `@deepseek-ai/dsh-tool-*` 生成通用工具声明。

## 5. v0.3 增量设计

### 5.1 实体复用与嵌套（可判定）

- **`extends`**：仅限同种类（subagent/tool/model/workflow/task）。原始文档在 schema 校验前
  合并：child 覆盖 base，映射深合并，列表整体替换，名称不继承。继承链上限 16
  （`DTA322`），环报 `DTA321`，缺失 base 报 `DTA301`。
- **`agents` / `uses`**：子 Agent 的嵌套子 Agent，二者合并去重。校验子 Agent 引用存在
  （`DTA301`）、无环（`DTA323`）、深度 ≤8（`DTA324`）。
- 解析发生在原始文档层，因此最终 schema 与 IR 都是“已展开”的，不引入运行期求值。
- 子 Agent 的 `extends` 在正文为空时继承 base 正文；有正文则覆盖。

### 5.2 MCP 工具自动发现

- `dotagent mcp discover` 读取 `.agents/mcp.json`，连接 `stdio` 或 `http` server；
- 握手：`initialize` → `notifications/initialized` → `tools/list`（支持 cursor 分页）；
- 工具 `inputSchema` 经 DotAgent schema 子集 lint 后写入 `.agents/tools/<server>-<tool>.yaml`，
  `source = {kind: mcp, ref: "<server>/<tool>"}`；
- `sse`/`ws` 明确报告 unsupported；不执行工具，只导入 schema；
- stdio 命令不经 shell，带超时、输出大小与工具数量上限；`${VAR}` 做环境变量展开。

### 5.3 内容哈希缓存

- 锁文件记录每个源文件 SHA-256；
- `.agents/.cache/sources.json` 缓存 `(size, mtime_ns, sha256)`；
- `compile --incremental`：stat 命中缓存则复用哈希；mtime 变但内容哈希相同也视为未变；
  只有哈希或锁不一致才重建；
- 缓存目录被 loader 与 lock 扫描忽略，不进入产物。

### 5.4 证书链与密钥轮换

- `keygen --chain`：Ed25519 root CA → signing 证书；`package --sign-key --sign-cert`；
- `signature.json` 携带叶证书与中间链；验证时用 `--ca-cert` 作为 trust anchor；
- 验证：有效期、issuer/subject、BasicConstraints、KeyUsage、Ed25519 签名；
- `keygen --rotate` 在既有 root 下签发新叶证书，旧/新包共享同一 trust anchor；
- 默认仍无签名，未签名包保持字节级可复现；证书链仅支持 Ed25519。

### 5.5 新投影与导入

- `dsh`：输出 DSH bundle（`package.json` + `cordis.patch.yml`），声明
  `@deepseek-ai/dsh-agent-preset`，persona 前缀来自 `AGENTS.md`；工具/技能/MCP 不映射并 warning；
- `openai`：OpenAI Agents SDK `agent.py` 脚手架 + `tools.json`，不生成 handler；
- `openai-chat`：Chat Completions 请求骨架（model/messages/tools）；
- `import --from dsh`：旧版 preset 目录与新版 patch 均可导入；
- `import --from openai`：读取 `openai-chat.json`，或用 `ast` 解析 `agent.py` 中的字面量
  `Agent(...)`（不执行代码）。

### 5.6 可配置审计

`.agents/audit.yaml`（`kind: AuditConfig`）支持：

- `rules.<CODE>.enabled` / `severity`（含 DTA 诊断码）；
- 追加 `secret_patterns`；
- 覆盖 `risky_tool_names`；
- `budget_warning_ratio`；
- `ignore_paths`（glob）。

配置先经 schema 校验，再应用于审计发现；非法 regex 报 `DTA308`。

## 6. v0.3 验证记录

- 单元测试：`python -m unittest discover -s tests -t tests`（119 个用例，全部通过）；
- 静态检查：`ruff check dotagent tests scripts` 通过；
- 示例通过 validate、13 项测试、determinism、package、audit、visualize；
- 证书链：root 签名验证、旋转后新旧包验证、错误 root 拒绝；
- MCP：临时 stdio server 的 `initialize`/`tools/list` 与 schema 导入通过；
- DSH/OpenAI：投影结构、Python 脚手架语法、DSH preset 导入通过；
- 缓存：touch 不变内容仍可复用，内容变化触发重建。

## 7. v0.3 下一步

1. 实体复用扩展到跨 Agent 共享库（带显式 `uses` 导入与版本约束）。
2. MCP 增量发现、缓存与 `http` SSE 完整支持。
3. 证书链吊销/OCSP 与签名时间戳。
4. 更多投影/导入（OpenAI 反向导入、其他 harness），先核对格式。
5. 审计规则按类别分组与自定义规则插件。

## 8. v0.2 基线记录

以下章节是 v0.2 的设计与验证记录，保留用于追溯。

## 9. 关键决策（ADR）

| 编号 | 决策 | 理由 | 代价 |
| --- | --- | --- | --- |
| ADR-1 | `.agents/` 规范、`.agent/` 兼容 | 采用生态约定同时不破坏旧项目 | 两套布局解析 |
| ADR-2 | `AGENTS.md` 作为指令入口 | 开放格式、可被同生态工具读取 | 指令与定义分居两层 |
| ADR-3 | 技能严格标准、仅目录形态 | 可被 Agent Skills 客户端直接使用 | 失去单文件便利 |
| ADR-4 | 自研 JSON Schema 子集 | 零第三方依赖、精确 Pointer、显式拒绝 | 需持续补关键字 |
| ADR-5 | IR 集合按名称键控 | 引用/校验/checks 路径简单 | 不保留数组顺序 |
| ADR-6 | 子 Agent 单文件 | 与 Claude 心智一致，避免递归作用域 | 无独立知识/技能目录 |
| ADR-7 | 无继承/作用域链 | 解析可预测 | 共享需重复声明 |
| ADR-8 | 工作流 DAG、无表达式 | 校验可判定 | 条件/映射由宿主实现 |
| ADR-9 | 确定性产物 | 可复现、可 diff、可校验 | 不含构建时间 |
| ADR-10 | 锁文件 + stat 快路径 | 大型知识库避免全量重编译 | mtime 变化会触发重建 |
| ADR-11 | Ed25519 可选签名 | 非对称、可离线验签、默认可复现 | 需要可选依赖 |
| ADR-12 | 审计为启发式 | 不联网、不执行工具 | 不能替代专业 secret 扫描 |
| ADR-13 | 投影/适配器 entry point | 核心零厂商依赖 | 插件质量由生态负责 |

## 10. 非目标落实

1. **不做 LLM 推理/Agent Loop/Harness/Sandbox/多租户/长期记忆/工具 Handler**：
   `runtime` 只读；无网络栈、模型 SDK、进程/沙箱调用。
2. **不绑定厂商 SDK**：核心依赖仅 PyYAML；签名是可选 `cryptography`。
3. **不臆造厂商格式**：Claude 投影依据官方文档；MCP 使用共享 `mcpServers` 形状；
   无法映射处 warning；模型 `provider_options` 原样保存并标 TODO。

## 11. 扩展机制

```python
from dotagent.project import ProjectedFile, ProjectionResult, Projector

class MyProjector(Projector):
    name = "my-target"
    description = "My host layout"

    def project(self, compiled):
        return ProjectionResult(
            target=self.name,
            files=(ProjectedFile.text("agent.md", "# ..."),),
        )
```

```toml
[project.entry-points."dotagent.projectors"]
my-target = "my_package:MyProjector"

[project.entry-points."dotagent.runtime_adapters"]
my-host = "my_package:MyHostAdapter"
```

## 12. 已知限制 / TODO

- 子 Agent 仅单文件；无嵌套 `.agents`、独立知识/技能。
- 无继承/覆盖；共享实体需同层重复。
- 工作流无条件边/映射/重试/循环。
- 知识按原样注入，无检索/分块/token 精确计数。
- 投影不清理旧文件；重复投影建议空目录。
- 包无证书链/时间戳；签名是内容完整性，不含身份信任。
- JSON Schema 子集不含 `unevaluatedProperties`、`$dynamicRef`、`contentEncoding` 等。
- 审计启发式可能误报/漏报。
- 暂无 DSH/OpenAI 投影；建议先核对官方格式再以插件实现。
- `--incremental` 基于 mtime/size，极端情况下需 `--check` 或全量编译兜底。

## 13. 旧版验证记录（v0.2）

- 单元测试：`python -m unittest discover -s tests -t tests`（v0.2 时 99 个用例）。
- 静态检查：`ruff check dotagent tests scripts` 通过。
- 示例 `examples/research-agent`：validate、13 项测试、determinism、package、audit、
  visualize 全部通过。
- 签名：Ed25519 生成、签名、验签、错误密钥/篡改检测通过。
- 导入：Claude 布局与包往返后均能通过 `validate`。
- 确定性：同一示例两次编译指纹一致；两次打包字节一致。

## 14. 旧版下一步（v0.2）

1. 嵌套子 Agent 与实体复用（`extends`/`uses`），保持可判定。
2. MCP 工具自动发现（连接 server 读取 tool list）与 schema 导入。
3. 增量编译的内容哈希缓存（弱化 mtime 依赖）。
4. 包签名证书链/密钥轮换；默认仍无签名。
5. 更多投影目标与导入器（DSH、OpenAI 等，先核对格式）。
6. 审计规则可配置化（`.agents/audit.yaml`）。
