# 部署文档索引

本目录包含 `privacy-local-agent` 部署模块的全套 SDLC 文档，覆盖 Helm Chart、原生 Kubernetes manifests 与 Docker Compose 三种交付形式。

## 文档清单

| 文档 | 说明 | 目标读者 |
|---|---|---|
| [prd.md](./prd.md) | 产品需求文档 | 产品经理、项目经理 |
| [design.md](./design.md) | 部署架构、交付形式与配置管理策略 | 架构师、后端开发 |
| [examples.md](./examples.md) | Helm、K8s 原生、Docker Compose 完整部署示例 | SRE、运维、开发 |
| [examples/values-custom.yaml](./examples/values-custom.yaml) | 自定义 Helm values 示例 | SRE、运维 |
| [testing.md](./testing.md) | 部署验证测试策略与可执行命令 | QA、测试开发、SRE |
| [ops.md](./ops.md) | 运维手册、参数建议与故障排查 | SRE、运维 |

## 快速开始

1. 阅读 [prd.md](./prd.md) 了解部署产品需求与验收标准。
2. 阅读 [design.md](./design.md) 掌握架构选型与 Chart 结构。
3. 查看 [examples.md](./examples.md) 或参考 [examples/values-custom.yaml](./examples/values-custom.yaml) 完成首次部署。
4. 部署后按 [testing.md](./testing.md) 执行验证测试。
5. 日常运维与排障参考 [ops.md](./ops.md)。

## 运行示例

```bash
cd /home/charles/code/sfwork/privacy-local-agent

# Helm 开发模式一键部署
helm install pla ./deploy/helm/privacy-local-agent

# 生产模式（需提前创建 TLS 与 API Key Secret）
helm install pla ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=pla-tls \
  --set security.auth.apiKeysSecret=pla-apikeys

# 原生 K8s 部署
kubectl apply -k ./deploy/k8s/

# Docker Compose 本地启动
cd deploy/docker-compose && docker-compose up -d
```
