# Production Improvements — 生产可用性改进设计方案

为了让 `privacy-local-agent` 达到工业级可用，我们将针对之前梳理出的遗留缺陷，开展以下三项核心改进设计：

---

## 1. 优雅关闭 (Graceful Shutdown)

### 1.1 现状与问题
当前双协议启动入口 `privacy_local_agent/server.py` 将 FastAPI REST 放在守护线程（`daemon=True`）启动，主线程运行 gRPC 并阻塞。
在接收到系统关闭信号（如 `SIGTERM` / `SIGINT`）时：
1. 主线程的 gRPC 收到信号退出。
2. 守护线程 REST 被瞬间强行终结，正在处理中的 HTTP 请求被截断，引发客户端错误。

### 1.2 改进方案
我们将主线程改写为统一的生命周期管理器，取消 REST 线程的守护属性（`daemon=False`），并捕获系统信号：
1. **统一信号捕捉**：使用 `signal` 模块在主线程捕捉 `SIGTERM` 和 `SIGINT`。
2. **程序化控制 Uvicorn 关闭**：不再直接调用阻塞的 `uvicorn.run()`，而是使用 `uvicorn.Server` 实例，在收到关闭信号时将 `server.should_exit` 设为 `True`，使其优雅退出并处理完剩余的连接。
3. **gRPC 优雅退出**：接收到信号后，调用 `grpc_server.stop(grace=5)` 停止服务，并预留 5 秒处理在途的 RPC 调用。
4. **主线程协同等待**：主线程在触发两者退出后，调用 `.join()` 等待 REST 线程退出，确保整个进程干净退出。

---

## 2. 生产级探针 (Readiness & Liveness Probes)

### 2.1 现状与问题
目前只有一个基础的 `/health` 接口，仅返回简单的 `{"status": "ok"}`。
在生产环境中，**存活状态（Liveness）** 和 **就绪状态（Readiness）** 往往含义不同：
* **存活探针** 只需要判断容器进程是否活着，不卡死即可。
* **就绪探针** 还需要确认该容器是否能够正常服务（如：配置文件是否正确解析、隐私预算数据库是否可以读写、重型 ML 模型是否已经成功加载）。如果只是单 `/health` 返回 ok，Kubernetes 可能会将流量导入到一个模型尚未加载完毕或数据库不可用的 Container，造成调用超时和 502/500 错误。

### 2.2 改进方案（已部分实现）
在 `privacy_local_agent/main.py` 中新增并细化以下端点：
1. **`/livez`**：存活检查。仅返回 `{"status": "alive"}`，不进行复杂的依赖检测。响应迅速，作为 Liveness Probe。
2. **`/readyz`**：就绪检查。检查关键依赖是否就绪：
   - 验证 `BudgetAccountant` 对应的存储后端（SQLite 文件/内存锁）是否健康。
   - 验证配置参数是否已解析。
   - 返回 `llm_ready` 字段，指示本地大模型是否已预热（不影响状态码，保持向后兼容）。
3. **`/readyz/llm`**：独立的 LLM 就绪探针。当本地大模型未就绪时返回 `503`，可用于 K8s 针对 LLM 就绪的独立 readiness probe。

同时新增 LLM 异步预热机制：
- `Qwen2VLClassifier` 提供 `warmup()` 与 `is_ready` 属性。
- `ClassificationAPI` 提供 `warmup_async()` 与 `is_llm_ready()`。
- 设置环境变量 `PRIVACY_WARMUP_LLM=true` 后，REST 服务启动时会在后台异步预热本地大模型，避免首个请求阻塞。

---

## 3. 可配置的查询混淆（QoL）Dummy 查询池

### 3.1 现状与问题
虚假查询模板（`MEDICAL_DUMMY`、`GENERIC_DUMMY`）被硬编码在 `privacy_local_agent/privacy/qol.py` 内部。
不同业务在不同行业（如金融、电商）落地时，需要混入与自己行业高度相关的虚假词典才能起到混淆效果，硬编码导致用户无法自行调整。

### 3.2 改进方案
将虚假词典池纳入隐私配置文件中，并支持请求参数级覆盖：
1. **扩展默认参数**：在 `privacy_local_agent/privacy/profile.py` 中，为 `qol` 原语的默认参数增加 `medical_pool` 与 `generic_pool` 默认列表。
2. **参数透传与覆盖**：在 [qol.py](file:///home/charles/code/sfwork/privacy-local-agent/privacy_local_agent/privacy/qol.py) 中，使 `obfuscate_query` 接收可选的 `medical_pool` 与 `generic_pool` 参数。若传入，则覆盖硬编码的静态列表。
3. **服务层解析**：在 [service.py](file:///home/charles/code/sfwork/privacy-local-agent/privacy_local_agent/service.py) 调用 `obfuscate_query` 时，先从 `resolver` 中解析出 profile 中的配置或请求体覆盖的词典列表，最终传给算法层。
