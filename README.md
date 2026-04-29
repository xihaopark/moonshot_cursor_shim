# Moonshot ↔ Cursor 本地 Shim

在任意机器上克隆即可用：

```bash
git clone https://github.com/xihaopark/moonshot_cursor_shim.git
cd moonshot_cursor_shim
```

在 **Cursor**（OpenAI 兼容）与 **Moonshot [`https://api.moonshot.cn/v1`](https://api.moonshot.cn)** 之间做一个透明代理，用于：

- 在 **非** `thinking` 已存在时，为 `POST …/chat/completions` 的请求体补上官方要求的 `thinking` 字段（等价于 OpenAI SDK 的 `extra_body`）。
- **原样转发**流式响应与普通 JSON，**不修改** `reasoning_content`（满足「补齐 / 保留」需求时，上游仍可返回 reasoning 字段；本 shim 不做截断）。
- **避免路径重复**：Cursor 发往 `BASE/v1/chat/completions` 时，upstream 仍为 `…/v1/chat/completions`（不会出现 `/v1/v1/`）。

## 使用方式

### 1. 虚拟环境与启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 默认：关闭深度思考（与多数 coding 场景一致）
MOONSHOT_THINKING=disabled .venv/bin/python shim.py
```

可选环境变量：

| 变量 | 含义 | 默认 |
|------|------|------|
| `MOONSHOT_BASE` | 上游根 URL（含 `/v1`） | `https://api.moonshot.cn/v1` |
| `MOONSHOT_THINKING` | `disabled` 或 `enabled`（仅在不带 `thinking` 时补上） | `disabled` |
| `SHIM_BIND` | 监听地址 | `127.0.0.1:8765` |

### 2. Cursor 配置

1. **OpenAI API** 一处：填入你的 **Moonshot API Key**（`sk-...`）。
2. **Override Base URL** 设为：`http://127.0.0.1:8765/v1`（与上面 `SHIM_BIND` 一致）。
3. 自定义模型名仍使用 Kimi 文档中的 ID（如 `kimi-k2.6`）。

### 3. 自检

```bash
curl -s http://127.0.0.1:8765/health
```

应返回 `upstream`、`thinking_default` 等 JSON。

## 说明

- 与具体业务仓库解耦；在任意 Cursor 项目里只要把 Base URL 指到本地 shim 即可。
- 若需 **固定开启** 深度思考，可设 `MOONSHOT_THINKING=enabled`；若请求里已带 `thinking`，shim **不会覆盖**（`setdefault`）。
