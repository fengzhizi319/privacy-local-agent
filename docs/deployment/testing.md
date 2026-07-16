# 部署验证测试文档

## 1. 概述

本文档定义 `privacy-local-agent` 部署包的验证策略、测试命令与验收标准，覆盖 Helm Chart、原生 Kubernetes manifests 与 Docker Compose 三种交付形式。所有命令均可在本地或 CI 环境中直接执行。

## 2. 测试目标

- 验证 Helm Chart 语法正确、模板可渲染、无未定义变量。
- 验证原生 K8s manifests 可被 `kubectl apply` 接受。
- 验证 Docker Compose 能正确启动服务并通过健康检查。
- 验证 `core` / `ml` 两种镜像构建成功。
- 验证 TLS、认证、HPA、NetworkPolicy 等可选配置在渲染后符合预期。

## 3. 环境准备

| 工具 | 最低版本 | 用途 |
|---|---|---|
| Kubernetes | 1.25 | 运行原生 K8s 与 Helm 部署 |
| Helm | 3.12 | Chart lint / template / install |
| kubectl | 1.25 | manifests 应用与状态检查 |
| Docker / Docker Compose | 3.9+ | 本地镜像构建与 Compose 启动 |
| curl / wget | — | 健康检查与接口探测 |

## 4. Helm Chart 验证

### 4.1 Lint 检查

```bash
cd /home/charles/code/sfwork/privacy-local-agent
make helm-lint
```

等效命令：

```bash
helm lint deploy/helm/privacy-local-agent
```

**预期结果**：无 ERROR，提示 `1 chart(s) linted, 0 chart(s) failed`。

### 4.2 模板渲染

```bash
cd /home/charles/code/sfwork/privacy-local-agent
make helm-template
```

等效命令：

```bash
helm template test deploy/helm/privacy-local-agent
```

**预期结果**：成功输出渲染后的 Kubernetes YAML，无渲染错误。

### 4.3 生产配置渲染验证

```bash
helm template prod deploy/helm/privacy-local-agent \
  -f deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=pla-tls \
  --set security.auth.apiKeysSecret=pla-apikeys
```

**检查项**：
- Deployment 中 `PRIVACY_TLS_ENABLED=true`、`PRIVACY_AUTH_ENABLED=true`。
- TLS 卷挂载使用 `existingSecret` 名称。
- HPA 资源被渲染。
- NetworkPolicy 被渲染。

### 4.4 ML 配置渲染验证

```bash
helm template ml deploy/helm/privacy-local-agent \
  -f deploy/helm/privacy-local-agent/values-ml.yaml
```

**检查项**：
- 镜像标签渲染为 `0.1.0-ml`（当 `image.tag` 为空时）。
- 资源限制符合 ml 场景（如 8Gi 内存上限）。

## 5. 原生 Kubernetes 验证

### 5.1 Dry Run

```bash
cd /home/charles/code/sfwork/privacy-local-agent
kubectl apply -k deploy/k8s/ --dry-run=client
```

**预期结果**：所有资源通过客户端校验，无语法错误。

### 5.2 真实部署

```bash
kubectl apply -k deploy/k8s/
```

### 5.3 状态检查

```bash
# 查看命名空间下所有资源
kubectl get all -n privacy-local-agent

# 查看 Pod 日志
kubectl logs -n privacy-local-agent deployment/privacy-local-agent

# 端口转发测试 REST
kubectl port-forward -n privacy-local-agent svc/privacy-local-agent 8079:8079

# 健康检查
curl http://localhost:8079/health
```

## 6. Docker Compose 验证

### 6.1 启动服务

```bash
cd /home/charles/code/sfwork/privacy-local-agent/deploy/docker-compose
docker-compose up -d
```

### 6.2 容器状态检查

```bash
docker-compose ps
docker-compose logs -f privacy-local-agent
```

### 6.3 健康检查

```bash
curl http://localhost:8079/health
```

### 6.4 停止服务

```bash
docker-compose down
```

## 7. 镜像构建验证

```bash
cd /home/charles/code/sfwork/privacy-local-agent

# core 镜像
make docker-core

# ml 镜像
make docker-ml
```

**检查项**：
- 镜像构建成功，无阶段错误。
- `docker images | grep privacy-local-agent` 显示 `0.1.0` 与 `0.1.0-ml`。

## 8. 功能冒烟测试

部署完成后，执行以下冒烟测试：

```bash
# 测试健康检查端点
curl -s http://localhost:8079/health | grep -q "ok" && echo "health ok"

# 测试 DP count 接口
curl -s -X POST http://localhost:8079/v1/privacy/dp/count \
  -H "Content-Type: application/json" \
  -d '{"values": [1, 0, 1, 1, 0], "params": {"epsilon": 1.0}}'

# 测试 masking 接口
curl -s -X POST http://localhost:8079/v1/privacy/mask \
  -H "Content-Type: application/json" \
  -d '{"data": {"phone": "13800138000", "email": "user@example.com"}}'
```

若启用认证，请在请求头中加入 `X-API-Key`：

```bash
curl -s -H "X-API-Key: my-api-key" http://localhost:8079/health
```

## 9. 持续集成建议

在 CI 流水线中加入以下步骤：

```yaml
- name: Helm lint
  run: make helm-lint

- name: Helm template
  run: make helm-template

- name: K8s dry run
  run: kubectl apply -k deploy/k8s/ --dry-run=client

- name: Build core image
  run: make docker-core

- name: Build ml image
  run: make docker-ml
```

## 10. 验收检查清单

- [ ] `helm lint deploy/helm/privacy-local-agent` 通过。
- [ ] `helm template test deploy/helm/privacy-local-agent` 成功渲染。
- [ ] 生产 values + TLS/认证 Secret 可正确渲染。
- [ ] `kubectl apply -k deploy/k8s/ --dry-run=client` 通过。
- [ ] `docker-compose up -d` 能启动服务并通过 `/health` 检查。
- [ ] `core` 与 `ml` 镜像均可成功构建。
- [ ] 冒烟测试（health / dp / mask）返回 200。
- [ ] 启用认证后，无 Key 请求返回 401，携带正确 Key 请求返回 200。
