# CLI 参考

全局选项：

```text
lubanlock [--resource DIR] <command> ...
```

`--resource` 默认 `resource`。所有带 `PATH` 的参数既可以传项目路径，也可以传
`resource/agent/` 下的 Agent 名称。

退出码：`0` 成功、`1` 校验/测试/校验失败、`2` 用法错误、`3` 内部错误。

## init

```bash
lubanlock init my-agent
lubanlock init my-agent --template full
lubanlock init my-agent --path ./agents/my-agent
```

默认创建到 `resource/agent/<name>/`。已存在且非空时需要 `--force`。

## list

```bash
lubanlock list
lubanlock --resource ./custom-resource list
```

## validate

```bash
lubanlock validate my-agent
lubanlock validate my-agent --strict --json
```

`--strict` 把 warning 也视为失败。JSON 输出含 `ok/errors/warnings/diagnostics`。

## compile

```bash
lubanlock compile my-agent
lubanlock compile my-agent -o ./build
lubanlock compile my-agent --check
lubanlock compile my-agent --locked
lubanlock compile my-agent --incremental
```

- 默认输出 `<project>/build/`；
- `--check`：产物是否过期；
- `--locked`：锁文件必须与源码一致；
- `--incremental`：stat 快路径，命中锁时直接复用。

## lock

```bash
lubanlock lock my-agent
```

写入 `.agents/lubanlock.lock`（指纹 + 每个源文件哈希/大小/mtime）。

## project

```bash
lubanlock project --list
lubanlock project my-agent --target claude
lubanlock project my-agent --target markdown -o ./dist/docs
```

默认输出 `<project>/dist/<target>/`。

## mcp discover

```bash
lubanlock mcp discover my-agent
lubanlock mcp discover my-agent --server docs --write
lubanlock mcp discover my-agent --server docs --timeout 5 --json
lubanlock mcp discover my-agent --refresh --max-age 3600
```

连接 MCP server，读取 `tools/list`；`--write` 把 schema 写到 `.agents/tools/`。
结果默认缓存 24 小时，`--refresh` 忽略缓存。仅支持 `stdio` 与 `http`（含 SSE 响应）。

## package / verify / keygen

```bash
lubanlock keygen --out ./keys                     # 原始 Ed25519 密钥
lubanlock keygen --out ./keys --chain             # root CA + 叶证书
lubanlock keygen --out ./keys2 --rotate \
  --root-key ./keys/root-key.pem --root-cert ./keys/root-ca.pem

lubanlock package my-agent --sign-key ./keys/signing-key.pem \
  --sign-cert ./keys/signing.pem \
  --timestamp-url https://tsa.example/tsr --timestamp-proxy http://127.0.0.1:8080 --verify
lubanlock verify ./dist/research-agent-0.1.0.zip \
  --ca-cert ./keys/root-ca.pem --fetch-crl --ocsp \
  --offline --proxy http://127.0.0.1:8080 \
  --timestamp-cert ./keys/tsa.pem --require-signature
```

默认无签名，未签名包仍可复现。

## test

```bash
lubanlock test my-agent
lubanlock test my-agent --suite workflow --suite custom
lubanlock test my-agent -k limits
lubanlock test my-agent --update
lubanlock test my-agent --json
```

`--update` 只更新 `tests/golden/agent.json`。

## audit

```bash
lubanlock audit my-agent
lubanlock audit my-agent --fail-on warning
lubanlock audit my-agent --out ./reports/audit
```

## visualize

```bash
lubanlock visualize my-agent
lubanlock visualize my-agent --out ./reports/visual
```

## import

```bash
lubanlock import ./claude-project --from claude
lubanlock import ./dsh-preset --from dsh
lubanlock import ./openai --from openai
lubanlock import ./copilot-project --from copilot
lubanlock import ./dist/agent.zip --from package --out resource/agent/restored
```

## skill

```bash
lubanlock skill add new-skill --agent my-agent --description "何时使用这个技能"
```

## reset

```bash
lubanlock reset my-agent --yes
lubanlock reset my-agent --yes --build
```

删除 `build/`、`dist/`、`reports/`、`.agents/lubanlock.lock`；`--build` 后重新编译。

## deploy

```bash
lubanlock deploy my-agent
lubanlock deploy my-agent --target claude --target markdown --sign-key ./keys/private.pem
```

生成 `dist/<name>-<version>.zip` 与 `dist/<target>/`。

## inspect

```bash
lubanlock inspect my-agent
lubanlock inspect my-agent --json
```

## schema

```bash
lubanlock schema            # 列出内置 schema
lubanlock schema agent      # 打印 agent schema
lubanlock schema --check    # 自检所有 schema
```
