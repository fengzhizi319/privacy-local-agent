# 测试控制台后端（Python）API 参考

本文档描述控制台 Python 后端（`console/backend`）对外提供的全部 REST 接口、请求/响应数据模型与环境变量配置。

- **默认基址**：`http://127.0.0.1:8080`
- **数据格式**：除静态资源外，全部为 `application/json`
- **统一错误结构**：`{"detail": "...", "status": <int>}`

## 1. 接口总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 检查后端自身与下游 agent 的连通性 |
| GET | `/api/samples` | 返回所有端点的示例数据（按功能分类） |
| POST | `/api/proxy` | 通用代理：把一个请求转发到 agent |
| POST | `/api/batch` | 批量代理：顺序转发一组请求并汇总结果 |
| GET | `/assets/*` | 静态资源（前端构建产物） |
| GET | `/{full_path}` | SPA 回退：未命中路径返回 `index.html` |

---

## 2. GET /api/health

检查后端自身与下游 agent 的连通性。

**响应字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `backend` | string | 后端自身状态，恒为 `"ok"` |
| `agent` | object \| string | agent `/health` 返回内容；不可达时为 `"unreachable"` |
| `agent_url` | string | 配置的 agent REST 地址 |
| `latency_ms` | float | 探测 agent 的往返耗时（仅可达时） |
| `error` | string | 错误描述（仅不可达时） |

**注意**：agent 不可达时仍返回 **HTTP 200**（而非 5xx），以便前端读取 `agent == "unreachable"` 并展示友好提示。

**示例**：

```bash
curl http://127.0.0.1:8080/api/health
```

```json
{
  "backend": "ok",
  "agent": { "status": "ok" },
  "agent_url": "http://127.0.0.1:8079",
  "latency_ms": 3.21
}
```

---

## 3. GET /api/samples

返回所有可测试端点的示例数据。前端启动时调用该接口渲染侧边栏与总览。

**响应结构**：`{"samples": [EndpointSample, ...]}`

**`EndpointSample` 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `method` | string | HTTP 方法（GET / POST） |
| `path` | string | 端点路径（如 `/v1/privacy/mask`） |
| `label` | string | UI 中显示的简短名称 |
| `category` | string | 功能分类（Masking / DP / ...） |
| `description` | string | 中文功能描述 |
| `body` | object \| null | 默认 JSON 请求体 |
| `contentType` | string \| null | 二进制载荷的 Content-Type |
| `rawPayloadB64` | string \| null | 二进制载荷的 base64 编码 |
| `backend` | string | 可用性标识：`rest`（仅 Python）/ `both` |

---

## 4. POST /api/proxy

通用代理：把一个请求转发到 `privacy-local-agent` REST 服务。

**请求体（`ProxyRequest`）**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `method` | string | 是 | 目标 HTTP 方法 |
| `path` | string | 是 | 目标路径（如 `/v1/privacy/mask`） |
| `body` | object \| null | 否 | JSON 请求体（与 `raw_payload_b64` 二选一） |
| `raw_payload_b64` | string \| null | 否 | 二进制载荷的 base64 编码 |
| `content_type` | string \| null | 否 | 二进制载荷的 Content-Type |

**响应体（`ProxyResponse`）**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | int | 转发后的逻辑状态码（成功为 200） |
| `duration_ms` | float | 本次转发耗时（毫秒） |
| `data` | any | agent 返回的原始数据 |

**示例**：

```bash
curl -X POST http://127.0.0.1:8080/api/proxy \
  -H 'Content-Type: application/json' \
  -d '{
    "method": "POST",
    "path": "/v1/privacy/mask",
    "body": { "value": "13800138000", "field": "phone" }
  }'
```

```json
{
  "status": 200,
  "duration_ms": 2.35,
  "data": { "masked": "138****8000" }
}
```

**错误**：

| 状态码 | 场景 |
|---|---|
| 400 | `raw_payload_b64` 解码失败 |
| 422 | 请求体校验失败 |
| 502 | agent 不可达 / 超时 |
| 其他 | 透传 agent 返回的状态码 |

---

## 5. POST /api/batch

批量代理：顺序转发一组请求并汇总成功 / 失败统计。单个请求失败不中断整个批次。

**请求体（`BatchRequest`）**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `requests` | `BatchRequestItem[]` | 子请求列表（顺序执行） |

**`BatchRequestItem` 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `method` | string | HTTP 方法（默认 POST） |
| `path` | string | 目标路径 |
| `body` | object \| null | JSON 请求体（批量不支持二进制载荷） |

**响应体（`BatchResponse`）**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | int | 子请求总数 |
| `passed` | int | 状态码在 2xx 区间的数量 |
| `failed` | int | 其余数量（`total == passed + failed`） |
| `results` | `BatchResultItem[]` | 逐条结果 |

**`BatchResultItem` 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `method` | string | HTTP 方法 |
| `path` | string | 目标路径 |
| `status` | int | 该请求的状态码 |
| `duration_ms` | float | 该请求耗时（毫秒） |
| `data` | any \| null | 成功时的 agent 返回数据 |
| `error` | string \| null | 失败时的错误描述 |

**示例**：

```bash
curl -X POST http://127.0.0.1:8080/api/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "requests": [
      { "method": "GET",  "path": "/health" },
      { "method": "POST", "path": "/v1/privacy/mask", "body": { "value": "a@b.com", "field": "email" } }
    ]
  }'
```

---

## 6. 静态资源与 SPA 回退

| 路径 | 行为 |
|---|---|
| `/assets/*` | 返回 `web/dist/assets/` 下带哈希的 JS/CSS（强缓存友好） |
| `/{full_path}` | 未命中路径一律返回 `index.html`（前端路由接管）；`index.html` 不存在时返回 404 |

静态目录由 `PRIVACY_CONSOLE_STATIC_DIR` 指定，默认 `../web/dist`（相对 `backend/` 工作目录）。目录不存在时应用仍可提供 API。

---

## 7. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_AGENT_URL` | `http://127.0.0.1:8079` | 下游 agent 的 REST 基地址 |
| `PRIVACY_AGENT_API_KEY` | — | 可选认证 API Key（agent 开启 auth 时必填） |
| `PRIVACY_CONSOLE_HOST` | `127.0.0.1` | 控制台后端监听地址 |
| `PRIVACY_CONSOLE_PORT` | `8080` | 控制台后端监听端口 |
| `PRIVACY_CONSOLE_STATIC_DIR` | `../web/dist` | 前端构建产物目录 |

配置通过 `pydantic-settings` 加载，支持 `.env` 文件；所有项均有默认值，本地开发零配置即可运行。

## 8. 与 Go 后端的差异

控制台另有 Go gRPC 代理后端（`console/backend-go`，默认 `8081`），接口契约（`/api/health` / `/api/samples` / `/api/proxy` / `/api/batch`）与本文档保持一致，差异在于：

- Go 后端把请求转换为 **gRPC** 调用转发给 agent；
- 部分 REST 专属端点（如 `/livez`、`/v1/privacy/dp/arrow_ipc`）仅在 Python 后端可用，示例数据中以 `backend` 字段（`rest` / `both`）标识。
