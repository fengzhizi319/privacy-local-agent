# Privacy Test Console - Backend

Python FastAPI 代理服务，用于转发请求到 `privacy_local_agent` 并提供示例数据。

## 运行

```bash
cd frontend/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PRIVACY_AGENT_URL` | `http://127.0.0.1:8079` | `privacy_local_agent` REST 地址 |
| `PRIVACY_AGENT_API_KEY` | - | 认证 API Key（agent 开启 auth 时） |
| `PRIVACY_CONSOLE_HOST` | `127.0.0.1` | 后端监听地址 |
| `PRIVACY_CONSOLE_PORT` | `8080` | 后端监听端口 |
| `PRIVACY_CONSOLE_STATIC_DIR` | `../web/dist` | 前端构建产物目录 |

## 核心文件

- `app/main.py` - FastAPI 入口，注册路由和静态资源
- `app/client.py` - 转发请求到 agent 的 httpx 客户端
- `app/config.py` - 环境变量配置
- `app/fixtures/samples.py` - 所有端点的示例数据
- `smoke_test.py` - 自动化冒烟测试

## 烟雾测试

```bash
source .venv/bin/activate
python smoke_test.py
```

