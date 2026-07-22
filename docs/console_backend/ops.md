# 测试控制台后端（Python）运维文档

本文档面向 SRE 与运维人员，说明控制台 Python 后端（`frontend/backend`）的部署、配置、启停与故障排查。

## 1. 部署形态

控制台后端是一个**无状态**的 FastAPI 服务，典型部署形态有两种：

1. **本地开发**：直接 `./run.sh` 启动（带 `--reload` 热重载）；
2. **与 agent 同机 / 同 Pod**：作为 sidecar 或独立进程，把浏览器流量代理到本机 agent。

它本身不持有数据（示例数据为内置常量），可任意水平扩展或重启。

## 2. 依赖与环境准备

```bash
cd frontend/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

核心依赖：`fastapi` / `uvicorn[standard]` / `httpx` / `pydantic` / `pydantic-settings` / `pyarrow`（解析 Arrow IPC 响应）。

## 3. 启动与停止

### 3.1 开发模式（热重载）

```bash
cd frontend/backend
./run.sh                      # 默认 127.0.0.1:8080
```

`run.sh` 实际执行 `uvicorn app.main:app --host $HOST --port $PORT --reload`。

### 3.2 生产模式

生产环境去掉 `--reload`，并按需增加 worker 数：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 2
```

### 3.3 一键脚本

```bash
./frontend/start.sh           # 同时启动 agent + 控制台后端
./frontend/stop.sh            # 读取 frontend/.pids/ 下的 PID 安全停止
```

## 4. 配置

全部通过环境变量配置（支持 `.env` 文件），详见 [api_reference.md](./api_reference.md) 的「环境变量」一节。运维最常用的是：

| 变量 | 用途 |
|---|---|
| `PRIVACY_AGENT_URL` | 指向目标 agent 的 REST 地址（连错目标是常见故障） |
| `PRIVACY_AGENT_API_KEY` | agent 开启认证后必须配置，否则转发返回 401 |
| `PRIVACY_CONSOLE_PORT` | 修改监听端口 |
| `PRIVACY_CONSOLE_STATIC_DIR` | 前端构建产物目录（默认 `../web/dist`） |

## 5. 前端构建产物

后端通过 `PRIVACY_CONSOLE_STATIC_DIR` 挂载前端 `dist`。若目录不存在，应用仍可提供 API（仅无 UI）。更新 UI 的流程：

```bash
cd frontend/web && npm run build   # 产物输出到 frontend/web/dist
# 后端无需重启（静态文件每次请求实时读取）
```

## 6. 健康检查与监控

- **探活**：`GET /api/health`。
  - `backend == "ok"` 表示后端存活；
  - `agent == "unreachable"` 表示后端正常但连不上 agent（用于区分故障域）。
  - 注意该接口**恒返回 200**，做探针时应解析响应体而非仅看状态码。
- **日志**：uvicorn 标准输出，含访问日志与错误堆栈。
- **耗时**：每次代理返回 `duration_ms`，可用于前端/网关层的性能观测。

## 7. 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `/api/health` 返回 `agent: "unreachable"` | agent 未启动 / `PRIVACY_AGENT_URL` 配错 / 端口被占 | 确认 agent 已启动且地址正确；`curl $PRIVACY_AGENT_URL/health` 直连验证 |
| 转发报 `All connection attempts failed` | 系统代理（Clash 等）劫持了本地连接 | 客户端已设 `trust_env=False` 直连；若仍异常，检查是否在上游加了代理层 |
| 转发返回 401 / 403 | agent 开启了认证 | 配置 `PRIVACY_AGENT_API_KEY` |
| 转发返回 502 | agent 超时（默认 60s）或网络错误 | 查看 agent 侧日志；排查慢查询 |
| 页面 404 `Frontend not built` | `dist` 不存在或 `index.html` 缺失 | 重新构建前端 |
| 页面打开但样式/脚本丢失 | `dist/assets` 与 `index.html` 版本不一致 | 清理后重新 `npm run build` |
| 422 校验错误 | 请求体不符合 Pydantic 模型 | 对照 [api_reference.md](./api_reference.md) 检查字段 |

## 8. 安全建议

- 控制台后端**不做认证**，仅适合内网 / 本地使用；暴露到不受信网络前，应置于带认证的反向代理之后。
- 生产环境建议配合 agent 的 TLS（`PRIVACY_TLS_ENABLED`）与认证（`PRIVACY_AUTH_ENABLED`）使用，并把 `PRIVACY_AGENT_URL` 改为 `https://`。
- CORS 当前为 `allow_origins=["*"]`（便于 Vite 开发）；生产同源部署时浏览器直接访问本后端，该配置不构成额外风险，但如需收敛可改为白名单。
