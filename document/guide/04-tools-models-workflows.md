# 工具、模型、工作流、任务

## 工具（Tool）

工具是声明，不是实现。Luban Lock 只保存名称、描述、JSON Schema 与注解。

```yaml
# .agents/tools/web_search.yaml
apiVersion: lubanlock/v1alpha1
kind: Tool
metadata:
  name: web_search
  description: 搜索公开网页并返回候选结果。
spec:
  input_schema:
    type: object
    additionalProperties: false
    required: [query]
    properties:
      query: {type: string, minLength: 1, maxLength: 400}
      limit: {type: integer, minimum: 1, maximum: 20}
  output_schema:
    type: object
    required: [results]
    properties:
      results:
        type: array
        items:
          type: object
          required: [title, url]
          properties:
            title: {type: string}
            url: {type: string}
  annotations:
    read_only: true
    idempotent: true
    open_world: true
  source:
    kind: builtin
    ref: web_search
```

- `input_schema` 根类型必须是 `object`；
- 支持的关键字见 `lubanlock schema tool`；
- 不支持的关键字会显式报错，避免“看起来生效、实际没校验”；
- `source.kind=mcp` 且 `ref=server/...` 时，会检查 `mcp.json` 是否声明该 server。

> 工具描述是模型选择工具的主要依据，务必写清楚“做什么/何时用”。

## 模型（Model）

```yaml
# .agents/models/default.yaml
apiVersion: lubanlock/v1alpha1
kind: Model
metadata:
  name: default
  description: 通用工具调用模型。
spec:
  model: your-model-id
  provider: openai-compatible
  context_window: 128000
  max_output_tokens: 8192
  capabilities: [tool_calling, reasoning]
  endpoint:
    base_url: https://api.example.com/v1
    api_key_env: PROVIDER_KEY
  parameters:
    temperature: 0.2
  provider_options: {}
```

- `provider` 是自由字符串，框架不解释；
- `endpoint.api_key_env` 只保存环境变量名，不保存密钥；
- `provider_options` 原样透传，不假设任何厂商 schema（标 TODO）。

## 工作流（Workflow）

```yaml
apiVersion: lubanlock/v1alpha1
kind: Workflow
metadata:
  name: quick-research
  description: 规划、检索、核验、总结。
spec:
  entry: plan
  nodes:
    - {id: plan, kind: task, ref: plan-research, outputs: [question]}
    - {id: search, kind: tool, ref: web_search}
    - {id: gather, kind: join}
    - {id: review, kind: human}
  edges:
    - {from: plan, to: search}
    - {from: search, to: gather}
    - {from: gather, to: review}
```

节点种类：

| kind | 是否需 ref | 含义 |
| --- | --- | --- |
| `agent` | 否（可选根 Agent 名） | 当前 Agent |
| `task` | 是 | 任务 |
| `subagent` | 是 | 子 Agent |
| `tool` | 是 | 工具 |
| `workflow` | 是 | 嵌套工作流 |
| `human` | 否 | 人工节点 |
| `join` | 否 | 汇聚，至少一条入边 |

校验：DAG 无环、入口存在、边端点存在、ref 可解析、嵌套调用无环、不可达节点 warning。

v0.3 不支持条件表达式、数据映射、重试与循环；这些属于宿主执行语义。

## 任务（Task）

```yaml
apiVersion: lubanlock/v1alpha1
kind: Task
metadata:
  name: summarize
  description: 把核验后的发现写成带引用的答案。
spec:
  prompt:
    text: |
      用“发现 / 证据 / 未解问题”三节回答；引用使用编号格式。
  input_schema:
    type: object
    additionalProperties: false
    required: [question, findings]
    properties:
      question: {type: string, minLength: 1}
      findings: {type: array, items: {type: string}}
  output_schema:
    type: object
    required: [answer]
    properties:
      answer: {type: string}
  skills: [citation-format]
  tools: [save_note]
  examples:
    - input: {question: 格式稳定吗, findings: ["规范标记为稳定 [1]。"]}
      output: {answer: "发现：... 证据：... 未解问题：..."}
```

- 示例输入会用 `input_schema` 校验；
- 既无 prompt 又无 workflow 时 warning；
- 任务的 prompt 文件必须位于 `.agents/` 内。

## 子 Agent

```markdown
---
name: source-checker
description: 核验引用与冲突。
model: default
tools: [web_fetch]
skills: [citation-format]
---

逐条打开引用 URL，标注 supported / unclear / contradicted。
```

子 Agent 是单文件，不能拥有独立知识/技能目录；需要共享内容时提升到根 Agent。
嵌套与继承（`agents`/`uses`/`extends`）见 [实体复用与嵌套子 Agent](10-reuse-and-nesting.md)。
