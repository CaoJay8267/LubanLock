# 资源目录与签名

## resource 约定

DotAgent 默认把所有 Agent 放在：

```text
resource/<kind>/<agent-name>/
```

当前 `kind` 为 `agent`：

```text
resource/agent/research-agent/
├── AGENTS.md
├── .agents/
├── build/
├── dist/
└── reports/
```

- `dotagent init <name>` 默认生成到 `resource/agent/<name>/`；
- `dotagent list` 列出该目录下的 Agent；
- `dotagent import` 未指定 `--out` 时也落到这里；
- 审计/可视化/部署产物都在 Agent 项目内，因此位于 `resource/` 下。

`--resource DIR` 可替换整个根目录。

## 生成物

| 目录/文件 | 内容 | 是否应提交 |
| --- | --- | --- |
| `build/` | 编译产物 | 否 |
| `dist/` | 包与投影 | 否 |
| `reports/` | 审计/可视化 | 否 |
| `.agents/dotagent.lock` | 锁文件（源文件哈希） | 通常提交 |
| `.agents/tests/golden/` | golden 快照 | 建议提交 |

`.gitignore` 已包含 `build/`、`dist/`、`reports/`、`keys/`。

## 签名

### 生成密钥

```bash
dotagent keygen --out ./keys
```

生成 Ed25519：

```text
keys/private.pem
keys/public.pem
```

私钥权限尽力设为 `0600`；请自行妥善保管。

### 签名打包

```bash
dotagent package research-agent \
  --sign-key ./keys/private.pem \
  --verify
```

包内新增 `signature.json`：

```json
{
  "format": "dotagent-signature/1",
  "alg": "ed25519",
  "keyId": "…",
  "signature": "base64…"
}
```

签名覆盖 `manifest.json` 的精确字节；`signature.json` 不进入 manifest 条目。

### 验签

```bash
dotagent verify dist/research-agent-0.1.0.zip \
  --public-key ./keys/public.pem \
  --require-signature
```

- 不带 `--require-signature` 时，未签名包仍通过结构/哈希校验；
- 提供错误公钥、篡改 manifest、缺少签名都会失败；
- 默认（不签名）包保持字节级可复现；同一密钥与内容生成的签名也确定。

### 安全边界

签名证明“内容由持有私钥的人签署过”，不提供证书链、时间戳或身份信任。
生产环境应结合密钥管理、审计与发布流程。

## 锁文件与增量

```bash
dotagent lock research-agent
dotagent compile research-agent --locked
dotagent compile research-agent --incremental
```

- `--locked`：锁与源码不一致时失败；
- `--incremental`：mtime/size 快路径命中锁时跳过完整编译；
- 修改源码后增量会重建；需要强一致时使用 `--locked --check`。
