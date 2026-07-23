# Python REST 代理后端 HTTP API 文档

本后端为前端测试控制台提供统一的 HTTP/JSON 接口，所有 agent 操作都通过 `/api/proxy` 转发到 `privacy-local-agent` 的 REST 服务。

默认监听地址：`http://127.0.0.1:8080`

所有代理响应均携带 `via` / `protocol` 字段，标识处理请求的后端（`python-rest`）与其和 agent 的通信协议（`REST`），供前端验证后端切换是否生效。

---

## 1. GET /api/health

检查后端自身与下游 agent 的连通性。

### 请求

```bash
curl -s http://127.0.0.1:8080/api/health | jq
```

### 成功响应（HTTP 200，agent 可达）

```json
{
  "backend": "ok",
  "agent": { "status": "ok", "namespace": "default" },
  "agent_url": "http://127.0.0.1:8079",
  "latency_ms": 3.2,
  "via": "python-rest",
  "protocol": "REST"
}
```

### agent 不可达响应（仍为 HTTP 200）

```json
{
  "backend": "ok",
  "agent": "unreachable",
  "agent_url": "http://127.0.0.1:8079",
  "error": "Unable to reach privacy agent: ...",
  "via": "python-rest",
  "protocol": "REST"
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `backend` | string | 后端自身状态，恒为 `ok`（能响应即存活） |
| `agent` | object / string | agent 的 `/health` 返回内容；不可达时为 `"unreachable"` |
| `agent_url` | string | 配置的 agent REST 地址，便于排查连错目标 |
| `latency_ms` | number | 探测 agent 的往返耗时（毫秒） |
| `error` | string | agent 不可达时的错误信息 |
| `via` / `protocol` | string | 后端身份标识（`python-rest` / `REST`） |

> 设计约定：agent 不可达时仍返回 **HTTP 200**（而非 5xx），以便前端读取 `agent == "unreachable"` 并展示友好提示，而不是直接报错。

---

## 2. GET /api/samples

返回所有 agent 端点的示例数据（按功能分类），供前端加载到测试控制台。

### 请求

```bash
curl -s http://127.0.0.1:8080/api/samples | jq
```

### 响应示例

```json
{
  "samples": [
    {
      "method": "GET",
      "path": "/health",
      "label": "Health",
      "category": "Health",
      "description": "服务健康检查",
      "body": null,
      "contentType": null,
      "rawPayloadB64": null,
      "backend": "rest"
    },
    {
      "method": "POST",
      "path": "/v1/privacy/mask",
      "label": "Mask",
      "category": "Masking",
      "description": "单字段脱敏",
      "body": { "field_name": "email", "value": "alice@example.com", "context": "" },
      "contentType": null,
      "rawPayloadB64": null,
      "backend": "both"
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `method` | string | 前端使用的 HTTP 方法 |
| `path` | string | 转发到 `/api/proxy` 时使用的 path |
| `label` | string | 前端展示名称 |
| `category` | string | 分类（Health、Masking、DP、K-Anonymity 等） |
| `description` | string | 中文功能描述 |
| `body` | object | 转发时的 JSON 请求体（可为 `null`） |
| `contentType` | string | 二进制载荷的 Content-Type（仅 Arrow IPC 等场景） |
| `rawPayloadB64` | string | 二进制载荷的 base64 编码（仅 Arrow IPC 等场景） |
| `backend` | string | 可用性标识：`rest` 仅 Python 后端，`both` 两个后端都支持 |

---

## 3. POST /api/proxy

统一代理入口，把请求转发到 privacy-local-agent REST 服务。

### 请求体

```json
{
  "method": "POST",
  "path": "/v1/privacy/mask",
  "body": { "field_name": "email", "value": "alice@example.com" }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `method` | string | 是 | 目标 HTTP 方法（如 `GET` / `POST`） |
| `path` | string | 是 | 目标 agent 路径（如 `/v1/privacy/mask`） |
| `body` | object | 否 | JSON 请求体（与 `raw_payload_b64` 二选一） |
| `raw_payload_b64` | string | 否 | 二进制载荷的 base64 编码（如 Arrow IPC） |
| `content_type` | string | 否 | 二进制载荷的 Content-Type，配合 `raw_payload_b64` 使用 |

### 成功响应（HTTP 200）

```json
{
  "status": 200,
  "duration_ms": 12.3,
  "data": { "result": "a***@example.com" },
  "via": "python-rest",
  "protocol": "REST"
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | number | 逻辑状态码，成功固定为 200 |
| `duration_ms` | number | 转发耗时（毫秒） |
| `data` | any | agent 返回数据（JSON / Arrow 解析结果 / base64） |
| `via` / `protocol` | string | 后端身份标识 |

### 错误响应

- **base64 解码失败（HTTP 400）**：`{"detail": "Invalid base64 payload: ..."}`
- **上游 agent 不可达（HTTP 502）**：`{"detail": "Unable to reach privacy agent: ...", "status": 502}`
- **agent 返回非 2xx**：透传原状态码与 `detail`（如 422 参数错误）

---

## 4. POST /api/batch

批量代理：顺序逐个转发一组请求并汇总统计，单个失败不中断整批。

### 请求体

```json
{
  "requests": [
    { "method": "POST", "path": "/v1/privacy/mask", "body": { "field_name": "email", "value": "a@example.com" } },
    { "method": "POST", "path": "/v1/privacy/dp/count", "body": { "values": [1, 2, 3], "params": { "epsilon": 0.1 } } }
  ]
}
```

### 成功响应（HTTP 200）

```json
{
  "total": 2,
  "passed": 2,
  "failed": 0,
  "results": [
    { "method": "POST", "path": "/v1/privacy/mask", "status": 200, "duration_ms": 8.1, "data": { "result": "a***@example.com" }, "error": null },
    { "method": "POST", "path": "/v1/privacy/dp/count", "status": 200, "duration_ms": 5.4, "data": { "result": 3.2 }, "error": null }
  ],
  "via": "python-rest",
  "protocol": "REST"
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | number | 子请求总数，恒为 `passed + failed` |
| `passed` | number | 状态码在 2xx 区间的子请求数 |
| `failed` | number | 其余子请求数 |
| `results[].status` | number | 该子请求的状态码（失败时为 agent 返回码或 500） |
| `results[].data` / `results[].error` | any / string | 成功时填 data，失败时填 error |

---

## 5. POST /api/upload

数据文件隐私处理：接收上传文件并转发到 agent 的 `process_file` 端点。

### 请求（multipart/form-data）

| 表单字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | file | 是 | CSV 或 JSON 数据文件 |
| `operation` | string | 是 | 操作类型：`mask_dataframe` / `k_anonymize` / `classify_table` |
| `params` | string | 否 | 操作参数的 JSON 字符串，默认 `{}` |

### cURL 示例

```bash
curl -s -X POST http://127.0.0.1:8080/api/upload \
  -F "file=@data.csv;type=text/csv" \
  -F "operation=mask_dataframe" \
  -F 'params={"columns": ["email", "phone"]}' | jq
```

### 成功响应（HTTP 200）

```json
{
  "status": 200,
  "duration_ms": 25.6,
  "data": {
    "operation": "mask_dataframe",
    "rows_in": 2,
    "rows_out": 2,
    "result": [ { "email": "a***@example.com", "phone": "138****8000" } ]
  },
  "via": "python-rest",
  "protocol": "REST"
}
```

> 文件解析与隐私算法均由 agent 负责，后端仅做 multipart 透传与 `ProxyResponse` 包装。`data.result` 为记录数组时，前端会渲染"原始数据 / 处理结果"对比表。

---

## 6. POST /api/lb_test

负载均衡测试：按策略向多个后端节点分发探测请求并统计结果。

### 请求体

```json
{
  "backends": [
    { "name": "agent-a", "url": "http://127.0.0.1:8079" },
    { "name": "agent-b", "url": "http://127.0.0.1:9079" }
  ],
  "num_requests": 10,
  "strategy": "round_robin",
  "probe_path": "/health",
  "probe_body": null
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `backends` | array | 是 | 目标节点列表（`name` + `url`），不能为空 |
| `num_requests` | number | 否 | 探测请求总数，默认 10（1~1000） |
| `strategy` | string | 否 | 分发策略：`round_robin` / `random` / `least_connections`，默认 `round_robin` |
| `probe_path` | string | 否 | 探测路径，默认 `/health` |
| `probe_body` | object | 否 | 提供时以 POST 发送 JSON 体，否则用 GET |

### 成功响应（HTTP 200）

```json
{
  "strategy": "round_robin",
  "total": 10,
  "success": 10,
  "failed": 0,
  "duration_ms": 45.2,
  "distribution": [
    { "name": "agent-a", "url": "http://127.0.0.1:8079", "count": 5, "success": 5, "failed": 0, "avg_latency_ms": 2.1, "min_latency_ms": 1.2, "max_latency_ms": 3.4 },
    { "name": "agent-b", "url": "http://127.0.0.1:9079", "count": 5, "success": 5, "failed": 0, "avg_latency_ms": 2.3, "min_latency_ms": 1.5, "max_latency_ms": 3.1 }
  ]
}
```

### 错误响应

- **backends 为空（HTTP 400）**：`{"detail": "backends 不能为空", "status": 400}`
- **未知策略（HTTP 400）**：`{"detail": "不支持的策略 'xxx'，可选: [...]", "status": 400}`

---

## 7. 常用 cURL 示例

### 7.1 单字段脱敏

```bash
curl -s -X POST http://127.0.0.1:8080/api/proxy \
  -H "Content-Type: application/json" \
  -d '{ "method": "POST", "path": "/v1/privacy/mask",
        "body": { "field_name": "email", "value": "alice@example.com" } }' | jq
```

### 7.2 差分隐私计数

```bash
curl -s -X POST http://127.0.0.1:8080/api/proxy \
  -H "Content-Type: application/json" \
  -d '{ "method": "POST", "path": "/v1/privacy/dp/count",
        "body": { "values": [1,2,3,4,5], "params": { "epsilon": 0.1, "mechanism": "laplace" } } }' | jq
```

### 7.3 K-匿名单条记录

```bash
curl -s -X POST http://127.0.0.1:8080/api/proxy \
  -H "Content-Type: application/json" \
  -d '{ "method": "POST", "path": "/v1/privacy/k_anonymize/record",
        "body": { "record": {"age":"30","zip":"100000","gender":"F"}, "qi_cols": ["age","zip","gender"], "k": 2 } }' | jq
```

### 7.4 查询混淆

```bash
curl -s -X POST http://127.0.0.1:8080/api/proxy \
  -H "Content-Type: application/json" \
  -d '{ "method": "POST", "path": "/v1/privacy/qol/obfuscate",
        "body": { "query": "糖尿病患者用药推荐", "num_dummies": 3, "domain": "medical" } }' | jq
```

### 7.5 字段分类

```bash
curl -s -X POST http://127.0.0.1:8080/api/proxy \
  -H "Content-Type: application/json" \
  -d '{ "method": "POST", "path": "/v1/privacy/classify/field",
        "body": { "field_name": "email", "value": "alice@example.com", "params": {} } }' | jq
```

---

## 8. 环境变量

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_AGENT_URL` | `http://127.0.0.1:8079` | 下游 agent 的 REST 基地址 |
| `PRIVACY_AGENT_API_KEY` | （空） | 可选的认证 API Key（agent 开启 auth 时必填） |
| `PRIVACY_CONSOLE_HOST` | `127.0.0.1` | 本服务监听地址 |
| `PRIVACY_CONSOLE_PORT` | `8080` | 本服务监听端口 |
| `PRIVACY_CONSOLE_STATIC_DIR` | `../web/dist` | 前端构建产物目录，存在时提供 Console UI |

> 配置基于 `pydantic-settings`，亦支持从 `backend/.env` 文件加载。

---

## 9. 状态码约定

| HTTP 状态码 | 含义 |
|---|---|
| 200 | 成功（`/api/health` 即使 agent 不可达也返回 200） |
| 400 | 请求参数错误（base64 解码失败、backends 为空、未知策略等） |
| 404 | 请求了未构建的前端页面（`Frontend not built`） |
| 422 | Pydantic 请求体校验失败，或透传 agent 的参数错误 |
| 502 | 上游 agent 不可达 |
