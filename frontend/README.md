# Privacy Test Console

用于与运行中的 `privacy_local_agent` 进行通信、发送测试数据并验证其全部功能的前端 + 后端测试控制台。

## 目录结构

- `backend/` - Python FastAPI 代理服务，统一转发请求到 `privacy_local_agent` REST 接口，并提供示例数据。
- `web/` - React + TypeScript + Vite 前端，按功能分组展示所有端点，支持一键加载示例和发送请求。

## 快速开始

### 1. 一键启动（推荐）

确保已安装 `privacy_local_agent` 和 `frontend/backend` 的虚拟环境依赖，并已构建前端（`frontend/web/dist` 存在），然后执行：

```bash
./frontend/start.sh
```

该脚本会同时启动 `privacy_local_agent` 和测试控制台后端，等待健康检查后输出访问地址，按 `Ctrl+C` 停止所有服务。

### 2. 手动启动

启动 agent：

```bash
python -m privacy_local_agent.server
```

启动测试控制台后端：

```bash
cd frontend/backend
./run.sh
```

### 3. 构建前端

```bash
cd frontend/web
# WSL 环境推荐使用 corepack pnpm；其它环境也可用 npm install
# 若使用 npm，请将下面命令中的 corepack pnpm 替换为 npm
corepack pnpm install
corepack pnpm build
```

构建产物输出到 `frontend/web/dist/`，后端会自动挂载为静态资源。

### 4. 打开控制台

浏览器访问 `http://127.0.0.1:8080`，左侧选择功能分组和端点，点击「Send Request」即可测试。

## 后端提供的 API

- `GET /api/health` - 检查后端与 agent 的连通性
- `GET /api/samples` - 获取所有端点的示例数据
- `POST /api/proxy` - 通用代理，将请求转发到 `privacy_local_agent`

## 烟雾测试

```bash
cd frontend/backend
source .venv/bin/activate
python smoke_test.py
```

该脚本会遍历所有示例端点，通过后端代理发送请求并统计结果。需要预存资源的端点（如异步任务查询、复核确认）会被跳过。

## 覆盖的隐私功能

- Health / 健康检查
- Masking / 数据脱敏（字段、记录、批量、DataFrame）
- Hash / HMAC 哈希
- DP / 差分隐私（count、sum、mean、histogram、noisy、aggregate、vector、adaptive clip、groupby、chunked、Arrow IPC）
- LDP / 本地差分隐私（二值/类别扰动与估计）
- K-Anonymity / K-匿名（记录、表、DataFrame）
- Query Obfuscation / 查询混淆
- Classification / 数据分类（字段、记录、表、异步、SecretFlow、复核、导出）
- Budget / 隐私预算查询
- Profile / 隐私参数推荐

## 已知限制

- 默认使用 REST 协议与 agent 通信；gRPC 端点未在此控制台中直接暴露。
- 若 agent 启用了认证或限速，请正确配置 `PRIVACY_AGENT_API_KEY` 或相应环境变量。
- `Arrow IPC` 端点的二进制响应会被后端解析为 JSON 记录后返回。
