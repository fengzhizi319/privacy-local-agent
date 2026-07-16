# 生产安全加固 API 参考

## 1. 环境变量

### 1.1 TLS

| 变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| `PRIVACY_TLS_ENABLED` | `false` | 否 | 是否启用 REST/gRPC TLS。 |
| `PRIVACY_TLS_CERT_FILE` | — | TLS 开启时必填 | 服务器证书 PEM 路径。 |
| `PRIVACY_TLS_KEY_FILE` | — | TLS 开启时必填 | 服务器私钥 PEM 路径。 |
| `PRIVACY_TLS_CA_FILE` | — | `optional`/`require` 时必填 | CA 证书 PEM 路径，用于校验客户端证书。 |
| `PRIVACY_TLS_CLIENT_AUTH` | `none` | 否 | 客户端认证模式：`none` / `optional` / `require`。 |
| `PRIVACY_TLS_KEY_PASSWORD` | — | 否 | 加密私钥的口令。 |

### 1.2 认证鉴权

| 变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| `PRIVACY_AUTH_ENABLED` | `false` | 否 | 是否启用 API Key 认证鉴权。 |
| `PRIVACY_AUTH_INTERNAL_KEYS_JSON` | `{}` | 否 | 内部服务 API Key 映射，JSON 对象。 |
| `PRIVACY_AUTH_EXTERNAL_KEYS_JSON` | `{}` | 否 | 外部服务 API Key 映射，JSON 对象。 |
| `PRIVACY_AUTH_INTERNAL_MTLS_ENABLED` | `true` | 否 | gRPC 是否将验证通过的 mTLS 客户端视为内部服务。 |

JSON 格式：

```json
{
  "<token>": {
    "name": "<服务名>",
    "scopes": ["<scope1>", "<scope2>"]
  }
}
```

`scopes` 为 `["*"]` 时表示拥有全部权限。

### 1.3 速率限制

| 变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| `PRIVACY_RATE_LIMIT_ENABLED` | `false` | 否 | 是否启用限速。 |
| `PRIVACY_RATE_LIMIT_DEFAULT_RPS` | `10` | 否 | 默认每秒请求数。 |
| `PRIVACY_RATE_LIMIT_DEFAULT_BURST` | `20` | 否 | 默认突发容量。 |
| `PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON` | `{}` | 否 | 按接口覆盖限速规则。 |
| `PRIVACY_RATE_LIMIT_REDIS_URL` | — | 否 | 多副本时共享计数器，例 `redis://redis:6379/0`。 |

### 1.4 健康检查

| 变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| `PRIVACY_HEALTH_NO_AUTH` | `true` | 否 | `/health` 与 `Health` RPC 是否免认证。 |
| `PRIVACY_HEALTH_NO_RATE_LIMIT` | `true` | 否 | `/health` 与 `Health` RPC 是否免限速。 |

---

## 2. Python SDK

### 2.1 `SecuritySettings`

位置：`privacy_local_agent.security.config.SecuritySettings`

Pydantic v2 模型，集中承载所有安全相关配置。

```python
class SecuritySettings(BaseModel):
    tls_enabled: bool = False
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    tls_ca_file: Path | None = None
    tls_client_auth: Literal["none", "optional", "require"] = "none"
    tls_key_password: str | None = None

    auth_enabled: bool = False
    auth_internal_mtls_enabled: bool = True
    internal_keys: dict[str, KeyConfig] = Field(default_factory=dict)
    external_keys: dict[str, KeyConfig] = Field(default_factory=dict)

    rate_limit_enabled: bool = False
    rate_limit_default_rps: float = 10.0
    rate_limit_default_burst: float = 20.0
    rate_limit_per_endpoint: dict[str, RateLimitConfig] = Field(default_factory=dict)
    rate_limit_redis_url: str | None = None

    health_no_auth: bool = True
    health_no_rate_limit: bool = True
```

#### `KeyConfig`

```python
class KeyConfig(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=list)
```

#### `RateLimitConfig`

```python
class RateLimitConfig(BaseModel):
    rps: float
    burst: float
```

### 2.2 `get_security_settings`

位置：`privacy_local_agent.security.config.get_security_settings`

```python
def get_security_settings() -> SecuritySettings
```

从当前环境变量解析并返回 `SecuritySettings`。每次调用都会重新读取 `os.environ`，便于测试与运行时重载。

### 2.3 TLS 构造器

位置：`privacy_local_agent.security.tls`

#### `uvicorn_ssl_kwargs`

```python
def uvicorn_ssl_kwargs(settings: SecuritySettings) -> dict[str, Any]
```

为 `uvicorn.run(..., **ssl_kwargs)` 生成 SSL 关键字参数。TLS 未启用时返回空字典。

#### `grpc_server_credentials`

```python
def grpc_server_credentials(settings: SecuritySettings) -> grpc.ServerCredentials
```

根据 `tls_client_auth` 返回 gRPC 服务端凭证：

- `none`：仅服务端 TLS。
- `optional`：服务端 TLS + 请求但不强制客户端证书。
- `require`：mTLS，强制客户端证书。

### 2.4 认证依赖

位置：`privacy_local_agent.security.auth`

#### `get_current_identity`

```python
async def get_current_identity(request: Request) -> Identity
```

FastAPI dependency，解析请求中的 `Authorization: Bearer <token>` 并返回 `Identity`。认证未启用时返回匿名管理员身份。

#### `require_permission`

```python
def require_permission(permission: str) -> Depends
```

返回 FastAPI dependency，要求调用者拥有指定 scope。

```python
from fastapi import Depends
from privacy_local_agent.security.auth import require_permission

@app.post("/v1/privacy/mask", dependencies=[require_permission("privacy:mask")])
```

#### `require_rest_path_permission`

```python
def require_rest_path_permission(path: str) -> Depends
```

根据 REST 路径自动推导所需权限。

#### `AuthInterceptor`

```python
class AuthInterceptor(grpc.ServerInterceptor):
    def __init__(self, settings: SecuritySettings | None = None): ...
```

gRPC server interceptor，校验 metadata / mTLS auth_context 中的身份与 scope。

### 2.5 速率限制

位置：`privacy_local_agent.security.ratelimit`

#### `Limiter`

```python
class Limiter:
    def __init__(self, settings: SecuritySettings): ...
    def is_allowed(self, identity: Identity, endpoint: str) -> bool: ...
```

基于 `limits` 库的滑动窗口限速器。支持内存与 Redis 两种后端。

#### `rate_limit_dependency`

```python
async def rate_limit_dependency(request: Request) -> None
```

FastAPI dependency，依赖 `request.state.identity`（由 `get_current_identity` 设置），超限时抛出 `HTTPException(429)`。

#### `RateLimitInterceptor`

```python
class RateLimitInterceptor(grpc.ServerInterceptor):
    def __init__(self, settings: SecuritySettings | None = None): ...
```

gRPC server interceptor，超限时返回 `grpc.StatusCode.RESOURCE_EXHAUSTED`。

### 2.6 身份与权限

位置：`privacy_local_agent.security.identity`

#### `Identity`

```python
@dataclass(frozen=True)
class Identity:
    service_type: Literal["internal", "external"]
    name: str
    scopes: list[str]

    def has_permission(self, permission: str) -> bool: ...
```

#### 权限映射

| REST 路径 | 权限 |
|---|---|
| `/health`, `/livez`, `/readyz` | `health:read` |
| `/v1/privacy/mask`, `/v1/privacy/mask_record` | `privacy:mask` |
| `/v1/privacy/hash` | `privacy:hash` |
| `/v1/privacy/dp/*` | `privacy:dp` |
| `/v1/privacy/k_anonymize/record` | `privacy:kano` |
| `/v1/privacy/qol/obfuscate` | `privacy:qol` |
| `/v1/privacy/budget` | `privacy:budget` |
| `/v1/privacy/classify/*` | `classification:read` |

| gRPC 方法 | 权限 |
|---|---|
| `Health` | `health:read` |
| `Mask`, `MaskRecord` | `privacy:mask` |
| `Hash` | `privacy:hash` |
| `DPCount`, `DPSum`, `DPMean` | `privacy:dp` |
| `KAnonymizeRecord` | `privacy:kano` |
| `ObfuscateQuery` | `privacy:qol` |
| `ClassifyField`, `ClassifyRecord`, `ClassifyTable` | `classification:read` |
| `RecommendParams` | `privacy:profile` |

---

## 3. REST 接口行为

### 3.1 认证

请求头：

```http
Authorization: Bearer <token>
```

| 场景 | HTTP 状态码 | 响应体 |
|---|---|---|
| 认证关闭 | — | 正常处理，使用匿名身份 |
| 未携带凭证 | 401 | `{"detail": "Unauthorized: missing credentials"}` |
| 无效凭证 | 401 | `{"detail": "Unauthorized: invalid credentials"}` |
| 越权 | 403 | `{"detail": "Forbidden: insufficient scope"}` |

### 3.2 速率限制

| 场景 | HTTP 状态码 | 响应体 |
|---|---|---|
| 未超速 | — | 正常处理 |
| 超速 | 429 | `{"detail": "Rate limit exceeded"}` |

---

## 4. gRPC 接口行为

### 4.1 认证

metadata：

```python
metadata=(("authorization", "Bearer <token>"),)
```

| 场景 | gRPC 状态码 |
|---|---|
| 未携带凭证 | `UNAUTHENTICATED` |
| 无效凭证 | `UNAUTHENTICATED` |
| 越权 | `PERMISSION_DENIED` |

### 4.2 mTLS 身份

当 `PRIVACY_AUTH_INTERNAL_MTLS_ENABLED=true` 且连接使用 TLS 并携带客户端证书时，gRPC 服务端从 `auth_context["x509_common_name"]` 提取 CN，构造 `Identity("internal", cn, ["*"])`。

### 4.3 速率限制

| 场景 | gRPC 状态码 |
|---|---|
| 未超速 | — |
| 超速 | `RESOURCE_EXHAUSTED` |

---

## 5. 错误码汇总

| 场景 | REST | gRPC |
|---|---|---|
| 未认证 | 401 Unauthorized | `UNAUTHENTICATED` |
| 越权 | 403 Forbidden | `PERMISSION_DENIED` |
| 超速 | 429 Too Many Requests | `RESOURCE_EXHAUSTED` |
| TLS 握手失败 | SSL/TLS 连接断开 | `UNAVAILABLE` |
| 配置错误（TLS 开启但缺少证书） | 启动失败 | 启动失败 |
