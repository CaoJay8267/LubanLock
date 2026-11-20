# 编写技能（Agent Skills）

Luban Lock 的技能严格遵循 [Agent Skills 规范](https://agentskills.io/specification)。

## 目录

```text
.agents/skills/<name>/
├── SKILL.md          # 必须
├── scripts/          # 可选：可执行脚本
├── references/       # 可选：参考资料
├── assets/           # 可选：模板/资源
└── ...               # 任意附件
```

构建时整个目录会作为资产打包；技能正文可以引用同目录附件。

## SKILL.md

```markdown
---
name: web-research
description: 规划并执行网络调研，优先使用一手来源。当问题需要外部证据时使用。
license: MIT
compatibility: 需要网络访问
metadata:
  owner: research
allowed-tools: web_search web_fetch
---

# Web research

1. 把问题拆成 2-5 个检索式。
2. 用 `web_search` 搜索，再用 `web_fetch` 打开结果。
3. 记录 URL 与支撑句；交叉验证关键结论。
```

字段约束：

| 字段 | 必需 | 约束 |
| --- | --- | --- |
| `name` | 是 | 1-64，`[a-z0-9-]`，无首尾/连续连字符，必须与目录同名 |
| `description` | 是 | ≤1024，非空 |
| `license` | 否 | 许可证名或文件引用 |
| `compatibility` | 否 | ≤500 |
| `metadata` | 否 | string→string 映射 |
| `allowed-tools` | 否 | 空格分隔的工具名 |

`lubanlock validate` 会检查名称与目录一致、描述长度、`allowed-tools` 引用是否存在。

## 快速添加

```bash
lubanlock skill add citation-format --agent research-agent \
  --description "把结论渲染成带编号引用的格式。"
```

生成：

```text
.agents/skills/citation-format/SKILL.md
```

## 使用建议

- 描述写“什么时候用”，而不是“这个技能是什么”；
- 正文保持短小，把长参考资料放到 `references/`，按需加载；
- 一个技能只做一件事；
- 不要依赖宿主私有字段；扩展信息放进 `metadata`。

## 在任务/子 Agent 中引用

```yaml
# tasks/summarize.yaml
spec:
  skills: [citation-format]
```

```markdown
<!-- .agents/agents/checker.md -->
---
name: checker
description: 核验引用。
skills: [citation-format]
---
```
