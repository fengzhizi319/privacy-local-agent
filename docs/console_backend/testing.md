# 测试控制台后端（Python）测试文档

## 1. 测试目的与策略

控制台后端的测试目标是保证**代理转发逻辑**与**接口契约**的正确性。由于后端只是薄代理，测试重点不在算法，而在：

- 请求/响应的格式包装是否正确；
- 错误（校验失败、上游不可达、上游错误）是否按约定转换；
- 批量执行的容错与汇总统计是否准确。

测试分两层：

| 层次 | 工具 | 是否依赖真实 agent | 位置 |
|---|---|---|---|
| 单元测试 | `pytest` + `fastapi.testclient.TestClient` + `unittest.mock` | 否（mock `agent_client.request`） | `frontend/backend/tests/test_routes.py` |
| 冒烟测试 | `httpx`（真实 HTTP 调用） | 是（需 agent + 后端运行） | `frontend/backend/smoke_test.py` |

**关键设计**：单元测试通过 `AsyncMock` 对模块级单例 `app.main.agent_client.request` 打桩，完全隔离对真实 agent 的依赖，可在 CI 中快速、稳定地运行。

## 2. 单元测试用例说明

测试文件 `frontend/backend/tests/test_routes.py` 覆盖以下场景：

### 2.1 `/api/health`

| 用例 | 场景 | 断言要点 |
|---|---|---|
| `test_health_ok` | agent 可达 | 返回 200；`backend == "ok"`；`agent.status == "ok"`；含 `agent_url` 与 `latency_ms` |
| `test_health_agent_unreachable` | agent 抛 502 | 仍返回 **200**；`agent == "unreachable"`；含 `error` 字段 |

### 2.2 `/api/samples`

| 用例 | 场景 | 断言要点 |
|---|---|---|
| `test_samples` | 获取示例 | 返回 200；`samples` 数量与 `get_samples()` 一致；首条含 `path` |

### 2.3 `/api/proxy`

| 用例 | 场景 | 断言要点 |
|---|---|---|
| `test_proxy_json` | 转发 JSON 请求 | 返回 200；包装为 `status/duration_ms/data`；`data` 为上游返回 |
| `test_proxy_invalid_body` | 缺少必填字段 `path` | Pydantic 返回 **422**；响应含 `detail` |
| `test_proxy_upstream_error` | 上游返回 422 | **透传**状态码 422 与 `detail == "invalid field"` |

### 2.4 测试夹具

- `client`：`TestClient(app)`，直接调用路由，无需真实监听端口。
- `mock_agent_client`：`patch("app.main.agent_client.request", new_callable=AsyncMock)`，用 `return_value` / `side_effect` 控制上游返回与异常。

## 3. 冒烟测试说明

`frontend/backend/smoke_test.py` 遍历 `get_samples()` 返回的全部示例端点，通过后端代理**真实**发送请求并统计成功 / 失败 / 跳过：

- 需要 agent 与控制台后端均已启动；
- 需预存资源的端点（如异步任务查询、复核确认）会被自动跳过；
- 输出每个端点的状态码与耗时，并在末尾给出汇总。

## 4. 测试执行与结果

### 4.1 运行单元测试

```bash
cd frontend/backend
source .venv/bin/activate
pytest tests -v
```

预期输出（示意）：

```text
tests/test_routes.py::test_health_ok PASSED
tests/test_routes.py::test_health_agent_unreachable PASSED
tests/test_routes.py::test_samples PASSED
tests/test_routes.py::test_proxy_json PASSED
tests/test_routes.py::test_proxy_invalid_body PASSED
tests/test_routes.py::test_proxy_upstream_error PASSED
====== 6 passed ======
```

### 4.2 运行冒烟测试

```bash
# 前置：先启动 agent 与控制台后端
./frontend/start.sh

# 另一终端
cd frontend/backend
source .venv/bin/activate
python smoke_test.py
```

### 4.3 CI 集成

单元测试不依赖外部服务，可直接纳入 CI：

```bash
pip install -r frontend/backend/requirements.txt
pytest frontend/backend/tests -q
```

## 5. 测试覆盖建议

现有单元测试聚焦 `/api/health`、`/api/samples`、`/api/proxy`。后续可补充：

- `/api/batch` 的汇总统计与「单个失败不中断」行为；
- `raw_payload_b64` 解码失败返回 400 的分支；
- Arrow IPC 响应解析（`_parse_arrow_response`）的单元测试（mock 二进制响应）。
