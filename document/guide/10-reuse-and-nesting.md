# 实体复用与嵌套子 Agent

v0.3 提供两种**静态可判定**的复用机制。

## extends：继承同种类实体

支持 `subagent`、`tool`、`model`、`workflow`、`task`。

```yaml
# .agents/tools/echo-upper.yaml
apiVersion: dotagent/v1alpha1
kind: Tool
extends: echo
metadata:
  name: echo-upper
  description: 返回大写文本。
```

规则：

- 只能继承同一种类的实体；
- 深合并：child 覆盖 base，列表整体替换；
- 名称不继承，child 必须声明自己的名称；
- 子 Agent 正文为空时继承 base 正文；
- 继承链最多 16 层；环报 `DTA321`，缺失 base 报 `DTA301`，过深报 `DTA322`。

```markdown
<!-- .agents/agents/base.md -->
---
name: base
description: 基础审阅者。
tools: [echo]
---
你是一个审阅者。
```

```markdown
<!-- .agents/agents/child.md -->
---
name: child
description: 更严格的审阅者。
extends: base
---
你是一个更严格的审阅者。
```

`child` 会继承 `tools: [echo]` 与 `description`（被覆盖），正文用自己的。

## agents / uses：嵌套子 Agent

```markdown
<!-- .agents/agents/reviewer.md -->
---
name: reviewer
description: 审阅草稿。
uses: [helper]        # 等价于 agents: [helper]
---
调用 helper 检查结构，再给出意见。
```

- `agents` 与 `uses` 合并去重；
- 引用必须存在（`DTA301`）；
- 有环报 `DTA323`；
- 深度上限 8，超限报 `DTA324`。

运行时可以查看层级：

```python
runtime = dotagent.AgentRuntime.from_path("resource/agent/demo")
print(runtime.subagent_tree("reviewer"))
```

## 边界

- 只在当前 Agent 内复用，不跨 Agent/仓库；
- 继承在加载后、schema 校验前展开，因此 IR 中看到的是已合并结果；
- 投影到 Claude Code 时，嵌套子 Agent 无法表达，会给出 warning；
- DSH/OpenAI 投影也不会自动展开嵌套关系。
