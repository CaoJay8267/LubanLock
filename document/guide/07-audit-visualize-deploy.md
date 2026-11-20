# 审计、可视化、部署

## 审计

```bash
dotagent audit research-agent
```

审计不联网、不执行工具，结果写入：

```text
<project>/reports/audit/audit.json
<project>/reports/audit/audit.md
```

### 检查项

- 校验错误/警告（`DTA*`）；
- 明文密钥启发式（`AUD001`）：私钥头、`sk-`、`ghp_`、AKIA 等；
- 危险工具（`AUD002`）：`open_world`/`destructive` 且非只读；
- 命令类工具名（`AUD003`）：shell/exec/bash 等；
- 未声明 annotations 的工具（`AUD004`）；
- 明文 `http://` 模型端点（`AUD005`）；
- 占位模型（`AUD006`）；
- 知识预算使用超过 80%（`AUD007`）。

### 失败阈值

```bash
dotagent audit research-agent --fail-on error     # 默认：有 error 才失败
dotagent audit research-agent --fail-on warning   # warning 也失败
dotagent audit research-agent --fail-on never     # 总是返回 0
```

> 审计是启发式，不能替代专业 secret 扫描或权限评审。

## 可视化

```bash
dotagent visualize research-agent
```

输出：

```text
<project>/reports/visual/
├── agent.mmd                    # 结构图
├── workflows/<name>.mmd         # 每个工作流
└── index.html                   # 汇总页面
```

- `.mmd` 是 Mermaid 文本，GitHub/VS Code 可直接渲染；
- `index.html` 通过 CDN 加载 Mermaid；离线时看 `.mmd`；
- 结构图包含知识、技能、子 Agent、工具、模型、任务、工作流、MCP。

## 部署

```bash
dotagent deploy research-agent
```

等价于：

1. 编译；
2. 打包到 `dist/<name>-<version>.zip` 并校验；
3. 投影到 `dist/claude/` 与 `dist/markdown/`（可用 `--target` 覆盖）。

带签名：

```bash
dotagent deploy research-agent --sign-key ./keys/private.pem
```

## 用户脚本（scripts/main.py）

无参数进入中文交互菜单：

```bash
python scripts/main.py
```

菜单：

1. 重置 Agent 编排（清理生成物并重建）；
2. 添加 Skill；
3. 审计；
4. 可视化；
5. 部署；
0. 退出。

也支持非交互转发：

```bash
python scripts/main.py audit research-agent
python scripts/main.py deploy research-agent
```

脚本会在需要时把仓库根加入 `sys.path`，因此既可直接运行，也可在安装后使用。
