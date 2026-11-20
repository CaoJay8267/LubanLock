# DotAgent 新手入门

本目录面向第一次接触 DotAgent 的开发者。建议按顺序阅读：

1. [快速开始](01-quickstart.md) — 安装、初始化、校验、编译、测试
2. [项目布局](02-layout.md) — `AGENTS.md`、`.agents/`、`resource/` 的约定
3. [编写技能](03-skills.md) — Agent Skills 标准与 `dotagent skill add`
4. [工具、模型、工作流、任务](04-tools-models-workflows.md)
5. [MCP 与导入](05-mcp-and-import.md)
6. [CLI 参考](06-cli.md)
7. [审计、可视化、部署](07-audit-visualize-deploy.md)
8. [资源目录与签名](08-resources-and-signing.md)
9. [扩展与常见问题](09-extending-faq.md)
10. [实体复用与嵌套子 Agent](10-reuse-and-nesting.md)

## 30 秒版本

```bash
pip install -e .
dotagent init my-agent
dotagent validate my-agent
dotagent test my-agent
dotagent compile my-agent
dotagent audit my-agent
dotagent visualize my-agent
```

生成位置：`resource/agent/my-agent/`。

## 核心概念

- **Agent**：一个项目，包含 `AGENTS.md`（指令）与 `.agents/`（机器可读定义）。
- **实体**：知识、技能、子 Agent、工具、模型、工作流、任务。
- **构建**：把定义编译成确定性的 `agent.json` + 资产 + 指纹。
- **投影**：把编译结果转成其他工具的目录布局。
- **运行时适配**：给宿主提供只读视图、提示词与工作流分层；DotAgent 自身不运行 Agent。

继续阅读：[快速开始](01-quickstart.md)。
