# CLI 参考

全局选项：

```text
dotagent [--resource DIR] <command> ...
```

`--resource` 默认 `resource`。所有带 `PATH` 的参数既可以传项目路径，也可以传
`resource/agent/` 下的 Agent 名称。

退出码：`0` 成功、`1` 校验/测试/校验失败、`2` 用法错误、`3` 内部错误。

## init

```bash
dotagent init my-agent
dotagent init my-agent --template full
dotagent init my-agent --path ./agents/my-agent
```

默认创建到 `resource/agent/<name>/`。已存在且非空时需要 `--force`。

## list

```bash
dotagent list
dotagent --resource ./custom-resource list
```

## validate

```bash
dotagent validate my-agent
dotagent validate my-agent --strict --json
```

`--strict` 把 warning 也视为失败。JSON 输出含 `ok/errors/warnings/diagnostics`。

## compile

```bash
dotagent compile my-agent
dotagent compile my-agent -o ./build
dotagent compile my-agent --check
dotagent compile my-agent --locked
dotagent compile my-agent --incremental
```

- 默认输出 `<project>/build/`；
- `--check`：产物是否过期；
- `--locked`：锁文件必须与源码一致；
- `--incremental`：stat 快路径，命中锁时直接复用。

## lock

```bash
dotagent lock my-agent
```

写入 `.agents/dotagent.lock`（指纹 + 每个源文件哈希/大小/mtime）。

## project

```bash
dotagent project --list
dotagent project my-agent --target claude
dotagent project my-agent --target markdown -o ./dist/docs
```

默认输出 `<project>/dist/<target>/`。

## mcp discover

```bash
dotagent mcp discover my-agent
dotagent mcp discover my-agent --server docs --write
dotagent mcp discover my-agent --server docs --timeout 5 --json
```

连接 MCP server，读取 `tools/list`；`--write` 把 schema 写到 `.agents/tools/`。
仅支持 `stdio` 与 `http`。

## package / verify / keygen

```bash
dotagent keygen --out ./keys                     # 原始 Ed25519 密钥
dotagent keygen --out ./keys --chain             # root CA + 叶证书
dotagent keygen --out ./keys2 --rotate \
  --root-key ./keys/root-key.pem --root-cert ./keys/root-ca.pem

dotagent package my-agent --sign-key ./keys/signing-key.pem \
  --sign-cert ./keys/signing.pem --verify
dotagent verify ./dist/research-agent-0.1.0.zip \
  --ca-cert ./keys/root-ca.pem --require-signature
```

默认无签名，未签名包仍可复现。

## test

```bash
dotagent test my-agent
dotagent test my-agent --suite workflow --suite custom
dotagent test my-agent -k limits
dotagent test my-agent --update
dotagent test my-agent --json
```

`--update` 只更新 `tests/golden/agent.json`。

## audit

```bash
dotagent audit my-agent
dotagent audit my-agent --fail-on warning
dotagent audit my-agent --out ./reports/audit
```

## visualize

```bash
dotagent visualize my-agent
dotagent visualize my-agent --out ./reports/visual
```

## import

```bash
dotagent import ./claude-project --from claude
dotagent import ./dsh-preset --from dsh
dotagent import ./openai --from openai
dotagent import ./dist/agent.zip --from package --out resource/agent/restored
```

## skill

```bash
dotagent skill add new-skill --agent my-agent --description "何时使用这个技能"
```

## reset

```bash
dotagent reset my-agent --yes
dotagent reset my-agent --yes --build
```

删除 `build/`、`dist/`、`reports/`、`.agents/dotagent.lock`；`--build` 后重新编译。

## deploy

```bash
dotagent deploy my-agent
dotagent deploy my-agent --target claude --target markdown --sign-key ./keys/private.pem
```

生成 `dist/<name>-<version>.zip` 与 `dist/<target>/`。

## inspect

```bash
dotagent inspect my-agent
dotagent inspect my-agent --json
```

## schema

```bash
dotagent schema            # 列出内置 schema
dotagent schema agent      # 打印 agent schema
dotagent schema --check    # 自检所有 schema
```
