# privacy-local-agent 部署运维手册

## 1. 环境准备

- Kubernetes >= 1.25
- Helm >= 3.12
- Docker / Docker Compose（本地）
- 可选：Prometheus Operator（用于 ServiceMonitor）

## 2. 镜像构建

```bash
# core 镜像（推荐生产默认）
docker build --target core -t privacy-local-agent:0.1.0 -f Dockerfile ..

# ml 镜像（含 torch/transformers/onnxruntime，用于完整分类）
docker build --target ml -t privacy-local-agent:0.1.0-ml -f Dockerfile ..
```

## 3. Helm 安装

### 3.1 默认安装

```bash
helm install pla ./deploy/helm/privacy-local-agent
```

### 3.2 生产安装（启用 TLS + 认证）

```bash
# 1. 准备 TLS Secret
kubectl create secret tls pla-tls \
  --cert=tls.crt --key=tls.key

# 2. 准备 API Key Secret
kubectl create secret generic pla-apikeys \
  --from-file=api_keys

# 3. 安装
helm install pla ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=pla-tls \
  --set security.auth.apiKeysSecret=pla-apikeys \
  --set image.repository=myregistry/privacy-local-agent \
  --set image.tag=0.1.0
```

### 3.3 升级

```bash
helm upgrade pla ./deploy/helm/privacy-local-agent -f values-production.yaml
```

### 3.4 卸载

```bash
helm uninstall pla
```

## 4. 原生 K8s 部署

```bash
# 编辑 secret.example.yaml 后重命名为 secret.yaml
kubectl apply -f deploy/k8s/
```

## 5. 验证

```bash
# 查看 Pod
kubectl get pods -l app.kubernetes.io/name=privacy-local-agent

# 端口转发测试 REST
kubectl port-forward svc/privacy-local-agent 8079:8079

curl http://localhost:8079/health
```

## 6. 监控与告警

- Prometheus 指标：`/metrics`
- 关键指标：
  - `privacy_agent_http_requests_total`
  - `privacy_agent_http_request_duration_seconds`
  - `privacy_agent_dp_budget_total_spent_epsilon`
  - `privacy_agent_active_requests`
- 告警建议：
  - 预算耗尽： budget_total_spent_epsilon / budget_total_limit_epsilon > 0.9
  - 错误率： rate(http_requests_total{status=~"5.."}[5m]) > 0.05
  - P99 延迟： histogram_quantile(0.99, rate(...)) > 1s

## 7. 故障排查

| 现象 | 排查 |
|---|---|
| Pod 启动失败 | `kubectl logs deploy/privacy-local-agent` 检查 TLS 证书路径或 profile 语法。 |
| 健康检查失败 | 检查 `/health` 是否被 security 白名单放行。 |
| gRPC 调用失败 | 确认 Service 端口 50051 与 TLS 设置一致。 |
| 资源不足 | 调整 `resources.requests` 与 HPA 阈值。 |
