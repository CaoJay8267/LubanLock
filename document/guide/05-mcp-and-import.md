# MCP 与导入

## 声明 MCP 服务

`.agents/mcp.json` 使用共享的 `mcpServers` 形状：

```json
{
  "mcpServers": {
    "docs": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}
    },
    "local-tools": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "some-mcp-server"],
      "env": {"SOME_TOKEN": "${SOME_TOKEN}"}
    }
  }
}
```

DotAgent 会：

- 校验 JSON 与服务对象形状；
- 检查 `type` 枚举、`url` 形状、`command`/`url` 至少一个；
- 对 `http://` 与缺少传输字段给出审计/校验提示；
- 在 Claude 投影中生成 `.mcp.json`；
- 在导入 Claude 项目时复制 `.mcp.json`。

> 环境变量不会在 DotAgent 中展开或求值；框架只保存声明。

## 自动发现工具

连接 server 读取 `tools/list`，并把 schema 写成 DotAgent 工具声明：

```bash
dotagent mcp discover research-agent --write
dotagent mcp discover research-agent --server docs --write --json
```

- 支持 `stdio`、`http`、旧版 `sse` 与 `ws`；
- HTTP 响应支持 JSON 与 `text/event-stream`（SSE）；旧版 SSE 使用 GET 流 + POST endpoint；
- WebSocket 需要 `dotagent[mcp]`；
- 生成 `.agents/tools/<server>-<tool>.yaml`，`source.ref = "<server>/<tool>"`；
- 不执行发现的工具，只导入 schema；
- 结果缓存在 `.agents/.cache/mcp/`；`--refresh` 强制刷新，`--max-age` 设置 TTL；
- 已在 `mcp.json` 中声明但无法连接时，会给出 warning，不影响其他 server。

## 工具与 MCP 的关联

工具可以声明来源：

```yaml
spec:
  source:
    kind: mcp
    ref: docs/search
```

校验器会检查 `docs` 是否在 `mcp.json` 中声明；未声明时给 warning（`DTA914`），
因为宿主也可能在运行期注入 server。

## 导入 Claude Code

```bash
dotagent import ./legacy-claude --from claude --out resource/agent/legacy
```

映射关系：

| Claude | DotAgent |
| --- | --- |
| `CLAUDE.md` | `AGENTS.md` |
| `.claude/agents/*.md` | `.agents/agents/*.md` |
| `.claude/skills/<name>/SKILL.md` | `.agents/skills/<name>/SKILL.md` |
| `.claude/commands/**/*.md` | `.agents/tasks/*.yaml` |
| `.mcp.json` | `.agents/mcp.json` |
| 引用的工具名（Read/Bash/...） | `.agents/tools/*.yaml` 通用声明 |
| Claude 模型名（sonnet/...） | `.agents/models/*.yaml` |

导入是**有损**的：

- 权限模式、hooks 等 Claude 专属字段不会迁移；
- 未知工具会生成最小 schema，需要人工补全；
- 命令参数 `$ARGUMENTS` 保留为文本；
- 名称会规范化为 DotAgent slug（小写、连字符）。

导入后建议执行：

```bash
dotagent validate legacy
dotagent audit legacy
dotagent test legacy --update
```

## 导入包

```bash
dotagent import dist/research-agent-0.1.0.zip --from package --out resource/agent/restored
```

从 `agent.json` 与资产重建可编辑工程；适合“包 → 源码”的闭环，但格式与指纹可能与原工程不同。

## 导入 DSH / OpenAI

```bash
# DSH：旧版 preset.yml + agent.cordis.yml，或新版 cordis.patch.yml
dotagent import ./my-dsh-preset --from dsh

# OpenAI：openai-chat.json，或生成的 agent.py + tools.json（AST 解析，不执行代码）
dotagent import ./openai --from openai

# Copilot：.github/copilot-instructions.md 与 .github/instructions/*.instructions.md
dotagent import ./repo --from copilot
```

## 自动识别

`--from auto`（默认）会：

- 文件或含 `agent.json` 的目录 → 包；
- 含 `.claude/` 或 `CLAUDE.md` 的目录 → Claude Code。
