# Python REST 代理后端测试文档

## 1. 运行全部单元测试

```bash
cd /home/charles/code/sfwork/privacy-local-agent/frontend/backend
source .venv/bin/activate          # 或使用 .venv/bin/python -m pytest
pytest tests -q
```

该命令会执行：

- `tests/test_routes.py`：`/api/health`、`/api/samples`、`/api/proxy` 的单元测试；
- `tests/test_upload_lb.py`：`/api/upload`、`/api/lb_test` 的单元测试。

当前共 **14 个测试用例**，全部通过即输出 `14 passed`。

## 2. 测试策略

单元测试**不依赖**运行中的 `privacy-local-agent`，核心手段有二：

1. **`fastapi.testclient.TestClient`**：直接包裹 FastAPI 应用发起内存请求，无需真实监听端口；
2. **`unittest.mock.AsyncMock` 打桩**：对上游客户端的异步方法打桩，隔离对真实 agent 的网络依赖：
   - `app.main.agent_client.request`（JSON / 二进制转发）；
   - `app.main.agent_client.request_multipart`（文件上传转发）。

负载均衡测试则通过**可注入的 transport** 解耦：`_run_lb_test(req, transport=...)` 接受 `httpx.MockTransport`，在内存中伪造后端节点，无需真实网络。

## 3. 各测试文件覆盖内容

### 3.1 tests/test_routes.py

| 测试函数 | 覆盖场景 |
|---|---|
| `test_health_ok` | agent 可达时返回 `backend/agent` 双正常、`latency_ms` 与 `via/protocol` 标识 |
| `test_health_agent_unreachable` | agent 不可达时仍返回 200，`agent == "unreachable"` 且携带 `error` |
| `test_samples` | `/api/samples` 返回数量与 `get_samples()` 一致的示例列表 |
| `test_proxy_json` | `/api/proxy` 转发 JSON 请求并包装为 `status/duration_ms/data` + `via/protocol` |
| `test_proxy_invalid_body` | 缺少必填字段 `path` 时 Pydantic v2 返回 422 |
| `test_proxy_upstream_error` | 上游 agent 返回错误时透传状态码与 `detail` |

### 3.2 tests/test_upload_lb.py

| 测试函数 | 覆盖场景 |
|---|---|
| `test_upload_forwards_multipart` | 上传 CSV 经 `request_multipart` 转发到 `/v1/privacy/process_file`，校验文件名/内容/表单字段透传 |
| `test_upload_upstream_error` | agent 返回错误时透传状态码与中文 `detail` |
| `test_run_lb_test_round_robin_distribution` | round_robin 下 6 个请求均匀分发到 2 节点，统计字段完整 |
| `test_run_lb_test_failed_probe` | 探测返回 500 时计入 `failed` |
| `test_run_lb_test_empty_backends` | `backends` 为空时抛出 400 |
| `test_lb_pick_backends_strategies` | 三种策略生成的下标序列合法且长度正确 |
| `test_lb_pick_backends_invalid_strategy` | 未知策略抛出 400 |
| `test_lb_test_endpoint_empty_backends` | 端点层：`backends` 为空返回 400 |

## 4. 运行指定测试

```bash
# 只跑路由测试
pytest tests/test_routes.py -v

# 只跑上传与负载均衡测试
pytest tests/test_upload_lb.py -v

# 按名称模糊匹配
pytest tests -k "lb_test" -v
```

> 异步测试使用 `@pytest.mark.anyio`（anyio pytest 插件，默认 asyncio 后端），需已安装 `anyio`（`requirements.txt` 已含）。

## 5. 冒烟测试（面向真实环境）

`smoke_test.py` 经 `/api/proxy` 逐个调用 `get_samples()` 返回的**所有**示例端点，验证真实连通性。

### 前置条件

后端（8080）与 `privacy-local-agent`（8079）均已启动：

```bash
# 终端一：启动 agent
cd /home/charles/code/sfwork/privacy-local-agent
python -m privacy_local_agent.server

# 终端二：启动后端
cd /home/charles/code/sfwork/privacy-local-agent/frontend/backend
source .venv/bin/activate
./run.sh
```

### 运行

```bash
cd /home/charles/code/sfwork/privacy-local-agent/frontend/backend
source .venv/bin/activate
python smoke_test.py
```

### 行为约定

- 非 200 响应**只打印不中断**，避免局部环境问题（如缺少 LLM 模型）掩盖其他端点的真实结果；
- 依赖运行时真实 ID 的端点（异步任务查询 `/classify/jobs/*`、复核确认 `/classify/review/confirm`）会自动 **SKIP**，需在 UI 中手动验证；
- 全部通过退出码为 0，有失败为 1（便于 CI 集成）。

## 6. 启动与停止

### 启动后端

```bash
cd /home/charles/code/sfwork/privacy-local-agent/frontend/backend
./run.sh        # 等价于 uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

### 使用一键脚本（同时启动 agent 与后端）

```bash
cd /home/charles/code/sfwork/privacy-local-agent
./frontend/start.sh       # 启动
./frontend/stop.sh        # 停止
```

## 7. 调试技巧

- 测试失败时，用 `pytest -v -s` 打印响应体，确认 `detail` 与预期一致；
- 若 `/api/proxy` 透传的错误与直连 agent 不一致，检查 `client._extract_detail` 的降级逻辑；
- 若本地出现 "All connection attempts failed"，确认系统代理（如 Clash）已关闭——客户端已设置 `trust_env=False` 直连，但仍需保证 agent 端口可达；
- 修改 `samples.py` 后记得同步运行冒烟测试，确认新增示例端点真实可用。
