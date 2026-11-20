# 扩展与常见问题

## 写一个投影器

投影器是纯函数：输入 `CompiledAgent`，输出文件集合，不直接写盘。

```python
from dotagent.project import ProjectedFile, ProjectionResult, Projector


class MyProjector(Projector):
    name = "my-target"
    description = "My host layout"

    def project(self, compiled):
        files = [
            ProjectedFile.text("agent.md", compiled.to_json_text()),
        ]
        return ProjectionResult(target=self.name, files=tuple(files))
```

注册：

```toml
[project.entry-points."dotagent.projectors"]
my-target = "my_package:MyProjector"
```

安装后 `dotagent project --list` 即可看到。

## 写一个运行时适配器

```python
from dotagent.runtime import RuntimeAdapter


class MyHostAdapter(RuntimeAdapter):
    name = "my-host"
    description = "Convert to my host's object model"

    def adapt(self, compiled):
        return {"agent": compiled.ir["agent"]}
```

```toml
[project.entry-points."dotagent.runtime_adapters"]
my-host = "my_package:MyHostAdapter"
```

## 常见问题

### `.agent/` 与 `.agents/` 有什么区别？

`.agents/` 是 v0.2+ 的规范目录；`.agent/` 是 v0.1 兼容别名，会给出弃用警告。
Agent Skills 标准本身不规定这个目录名，但 `.agents/skills/` 是当前生态的常见发现约定。

### `AGENTS.md` 属于 Agent Skills 吗？

不属于。`AGENTS.md` 是独立的开放指令格式，DotAgent 用它承载 Agent 指令；
技能内容仍然只放在 `SKILL.md`。

### 为什么技能不能是单个 `.md` 文件？

Agent Skills 要求 `skills/<name>/SKILL.md`，且 `name` 必须等于目录名。DotAgent 严格遵守，
以避免生成其他客户端无法加载的技能。

### 一个 Agent 可以有多个模型吗？

可以：在 `models/` 下定义多个，根 Agent 用 `spec.model` 指定默认模型，子 Agent 可覆盖。
模型名是 DotAgent 内部引用，`spec.model` 才是厂商模型标识（原样保存）。

### 如何接 Claude Code？

```bash
dotagent project my-agent --target claude
```

输出 `CLAUDE.md`、`.claude/agents/`、`.claude/skills/`、`.claude/commands/`、`.mcp.json`。
无法映射的模型/工具会 warning，不会伪造。

### 支持 DSH / OpenAI 吗？

核心不绑定任何厂商。可以通过 entry point 实现投影器/适配器；实现前应先核对官方格式。
DotAgent 宁愿缺省，也不臆造格式。

### 如何校验工具 schema 的关键字？

```bash
dotagent schema tool
dotagent schema --check
```

工具 schema 使用 DotAgent 支持的 JSON Schema 2020-12 子集；未支持关键字会报 `DTA308`。

### 如何把密钥排除在仓库外？

- 模型端点只写 `api_key_env`；
- MCP env 使用 `${VAR}` 占位；
- `dotagent audit` 会做启发式检查；
- `.gitignore` 忽略 `keys/`、`reports/`、`dist/`、`build/`。

### 测试命令是什么？

```bash
python -m unittest discover -s tests -t tests
```

`tests/` 只放 `test_*.py`；通用夹具在 `dotagent/testkit/fixtures.py`。

### 如何从旧版迁移？

1. 把 `.agent/` 改名为 `.agents/`；
2. `instructions.md` 改名为项目根 `AGENTS.md`；
3. `subagents/` 改名为 `agents/`；
4. 单文件技能改成 `skills/<name>/SKILL.md`；
5. `allowed_tools: [...]` 改成 `allowed-tools: "a b"`；
6. 运行 `dotagent validate` 并处理诊断。

旧布局仍可读取，但建议尽快迁移。
