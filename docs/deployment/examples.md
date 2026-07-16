# 部署使用示例

## 1. 概述

本文档提供 `privacy-local-agent` 的完整部署示例，包括 Helm 安装、Kubernetes 原生部署与 Docker Compose 启动。示例覆盖 `core` / `ml` 两种镜像选择、TLS 证书注入与 API Key Secret 配置，可直接用于本地联调与生产部署参考。

## 2. 镜像选择

| 镜像 | 标签 | 适用场景 | 资源建议 |
|---|---|---|---|
| `core` | `0.1.0` | 脱敏、DP、K-匿名、规则分类 | 256Mi ~ 1Gi 内存 |
| `ml` | `0.1.0-ml` | 完整三层分类（规则 → NER → LLM/VLM） | 2Gi ~ 8Gi 内存 |

Helm Chart 通过 `flavor: core` 或 `flavor: ml` 自动选择镜像标签；当 `image.tag` 留空时，`ml` 会自动附加 `-ml` 后缀。

## 3. Helm 部署示例

### 3.1 开发模式（默认关闭 TLS/认证）

```bash
cd /home/charles/code/sfwork/privacy-local-agent

# 构建 core 镜像
docker build --target core -t privacy-local-agent:0.1.0 .

# 安装 Chart
helm install pla ./deploy/helm/privacy-local-agent

# 验证 Pod 状态
kubectl get pods -l app.kubernetes.io/name=privacy-local-agent
```

### 3.2 生产模式（启用 TLS + API Key 认证）

```bash
# 1. 创建 TLS Secret（证书需为 PEM 格式）
kubectl create secret tls pla-tls \
  --cert=tls.crt \
  --key=tls.key

# 2. 创建 API Key Secret，key 必须为 api-keys.json
cat > api-keys.json <<'EOF'
{
  "prod-gateway-key": {
    "name": "production-gateway",
    "scopes": ["*"]
  }
}
EOF

kubectl create secret generic pla-apikeys \
  --from-file=api-keys.json

# 3. 使用生产 values 安装，并传入 Secret 名称
helm install pla ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=pla-tls \
  --set security.auth.apiKeysSecret=pla-apikeys \
  --set image.repository=privacy-local-agent \
  --set image.tag=0.1.0
```

### 3.3 ML 镜像部署

```bash
# 构建 ml 镜像
docker build --target ml -t privacy-local-agent:0.1.0-ml .

# 使用 values-ml.yaml，并指定仓库地址
helm install pla-ml ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-ml.yaml \
  --set image.repository=privacy-local-agent \
  --set image.tag=0.1.0-ml
```

### 3.4 引用自定义 values 文件

```bash
helm install pla ./deploy/helm/privacy-local-agent \
  -f docs/deployment/examples/values-custom.yaml
```

## 4. 原生 Kubernetes 部署示例

### 4.1 基础部署

```bash
cd /home/charles/code/sfwork/privacy-local-agent

# 直接 apply kustomization 组织的 manifests
kubectl apply -k ./deploy/k8s/

# 查看资源
kubectl get all -n privacy-local-agent
```

### 4.2 启用 TLS 与认证

1. 复制示例 Secret 并替换为真实值：

```bash
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml
# 编辑 deploy/k8s/secret.yaml 替换 tls.crt / tls.key / api-keys.json
```

2. 取消 `kustomization.yaml` 中 secret 的注释：

```yaml
resources:
  - namespace.yaml
  - configmap.yaml
  - deployment.yaml
  - service.yaml
  - secret.yaml
```

3. 取消 `deployment.yaml` 中 TLS 与认证环境变量的注释，并确认挂载卷名称与 Secret 一致。

4. 应用：

```bash
kubectl apply -k ./deploy/k8s/
```

## 5. Docker Compose 部署示例

### 5.1 基础启动

```bash
cd /home/charles/code/sfwork/privacy-local-agent/deploy/docker-compose

# 启动服务（-d 后台运行）
docker-compose up -d

# 查看日志
docker-compose logs -f privacy-local-agent

# 测试健康检查
curl http://localhost:8079/health
```

### 5.2 启用 TLS 与认证

1. 准备证书与私钥：

```bash
mkdir -p certs
mv tls.crt tls.key certs/
```

2. 编辑 `docker-compose.yml`，取消以下环境变量与挂载注释：

```yaml
environment:
  PRIVACY_TLS_ENABLED: "true"
  PRIVACY_TLS_CERT_FILE: "/certs/tls.crt"
  PRIVACY_TLS_KEY_FILE: "/certs/tls.key"
  PRIVACY_AUTH_ENABLED: "true"
  PRIVACY_AUTH_EXTERNAL_KEYS_JSON: '{"dev-key":{"name":"local","scopes":["*"]}}'

volumes:
  - ./certs:/certs:ro
```

3. 重启服务：

```bash
docker-compose down
docker-compose up -d
```

4. 验证（注意使用 `-k` 跳过自签名证书校验）：

```bash
curl -k https://localhost:8079/health
```

## 6. core/ml 镜像选择建议

| 场景 | 推荐镜像 | 说明 |
|---|---|---|
| 仅使用脱敏、DP、K-匿名、规则分类 | `core` | 体积小、启动快、攻击面小 |
| 需要 NER 或 LLM/VLM 分类 | `ml` | 包含 torch / transformers / onnxruntime |
| 生产网关后方仅做隐私计算 | `core` | 分类可前置到独立 ml 实例 |
| 本地全功能联调 | `ml` | 一次启动覆盖全部能力 |

## 7. TLS / Secret 配置要点

- TLS 证书与私钥建议通过 `kubectl create secret tls` 或外部 Secret 管理工具（如 cert-manager、Vault）创建，不写入 values 明文。
- API Key Secret 的 data key 必须为 `api-keys.json`，格式为 JSON 对象，每个 key 对应 `name` 与 `scopes`。
- 当 `security.enabled=true` 时，Chart 会自动启用 rate limit，并保留 `/health` 作为公开路径。
- 原生 K8s 中启用 TLS/认证时，需同步修改 `deployment.yaml` 中的环境变量、volumeMounts 与 volumes。

## 8. 验证部署

无论哪种部署方式，均可通过 `/health` 端点进行基础验证：

```bash
# HTTP 模式
curl http://<host>:8079/health

# HTTPS 模式（自签名证书加 -k）
curl -k https://<host>:8079/health

# 预期返回
{"status":"ok"}
```

## 9. 常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| `ImagePullBackOff` | 镜像未构建或仓库不可达 | 确认 `image.repository` / `image.tag` 正确，并推送到可访问仓库 |
| `CrashLoopBackOff` | 配置文件路径错误或 TLS 证书加载失败 | 检查 `PRIVACY_PROFILE` 与 `/certs` 挂载 |
| 健康检查失败 | Service/探针配置与端口不一致 | 确认 Service 暴露 8079，探针路径为 `/health` |
| gRPC 调用 TLS 错误 | 客户端未配置对应证书 | 使用相同 CA 签名的证书，或在测试环境使用 `-k` |
| 认证 401 | API Key 不匹配或 Secret key 名称错误 | 确认 Secret 中 key 为 `api-keys.json`，且请求携带正确 key |
