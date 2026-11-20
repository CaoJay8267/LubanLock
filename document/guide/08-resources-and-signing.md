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

### 原始 Ed25519 密钥

```bash
dotagent keygen --out ./keys
dotagent package research-agent --sign-key ./keys/private.pem
dotagent verify dist/research-agent-0.1.0.zip \
  --public-key ./keys/public.pem --require-signature
```

### 证书链

```bash
dotagent keygen --out ./keys --chain
# keys/root-key.pem, root-ca.pem, signing-key.pem, signing.pem

dotagent package research-agent \
  --sign-key ./keys/signing-key.pem \
  --sign-cert ./keys/signing.pem

dotagent verify dist/research-agent-0.1.0.zip \
  --ca-cert ./keys/root-ca.pem --crl ./keys/revoked.crl --require-signature
```

- `signature.json` 携带叶证书与中间链；
- 校验会检查有效期、issuer/subject、CA 约束、KeyUsage 和 Ed25519 签名；
- `--crl` 接受 PEM/DER，校验 CRL 签名、有效期和叶证书序列号；
- OCSP 与可信时间戳尚未实现；
- 证书链仅支持 Ed25519。

### 密钥轮换

```bash
dotagent keygen --out ./keys-v2 --rotate \
  --root-key ./keys/root-key.pem \
  --root-cert ./keys/root-ca.pem
```

新叶证书仍由同一个 root 签发，因此旧包和新包都能用同一个
`root-ca.pem` 验证；`keyId` 用于区分签名者。

### 生成物

包内 `signature.json` 不进入 `manifest.entries`，避免自引用；默认无签名包保持字节级可复现。

## 锁文件与增量

```bash
dotagent lock research-agent
dotagent compile research-agent --locked
dotagent compile research-agent --incremental
```

- `--locked`：锁与源码不一致时失败；
- `--incremental`：mtime/size 快路径命中锁时跳过完整编译；
- 修改源码后增量会重建；需要强一致时使用 `--locked --check`。
