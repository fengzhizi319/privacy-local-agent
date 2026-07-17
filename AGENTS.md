# Privacy Local Agent — Agent Guide

> AI coding agent guide for the `privacy-local-agent` project. Read this before modifying code.

`privacy-local-agent` is a Python sidecar that exposes privacy primitives (masking, differential privacy, K-anonymity, query obfuscation) and a 3-layer data classification funnel over REST and gRPC. It is designed for local/Sidecar deployment and is currently at POC/MVP maturity.

---

## 1. Project Overview

| Capability | Status | Notes |
|---|---|---|
| Masking | ✅ Ready | Field-name-aware masking for common PII |
| Differential Privacy | ✅ Ready | Laplace count/sum/mean with budget accounting |
| K-anonymity | ✅ Ready | Per-record heuristic & dataset-level generalization |
| Query Obfuscation | ✅ Ready | Dummy query injection |
| Classification | ✅ Ready | Rule engine → Small-NER → local VLM/LLM |
| Gateway / Load Balancer | ✅ Ready | REST + gRPC reverse proxy with health checks |
| TLS / Auth / Rate Limit | ✅ Ready | Opt-in via environment variables |
| Observability | ✅ Ready | Structured logs + Prometheus `/metrics` + optional tracing |
| K8s / Helm Deployment | ✅ Ready | `deploy/helm/` + `deploy/k8s/` + `deploy/docker-compose/` |
| Dataset-level K-anonymity | ✅ Ready | Implemented via Mondrian algorithm |
| DP Gaussian / clipping | ✅ Ready | Gaussian mechanism & clipping bounds supported |
| ML dependency split | ✅ Ready | Single Dockerfile with `--target core|ml` |

## 2. Technology Stack

- **Python 3.10+**
- **FastAPI** + **Uvicorn** for REST
- **gRPC** (`grpcio`) for RPC
- **Pydantic v2** for models
- **PyYAML** for profile configuration
- **ONNX Runtime / ModelScope** for Small-NER (optional, lazy-loaded)
- **PyTorch + Transformers + Qwen2-VL** for LLM/VLM layer (optional, lazy-loaded)

Core dependencies are pinned in `pyproject.toml`. Heavy ML dependencies are **not** pinned as runtime deps; they are lazy-loaded and degraded gracefully if absent.

## 3. Repository Layout

```text
privacy-local-agent/
├── privacy_local_agent/           # Main package
│   ├── main.py                    # FastAPI REST entrypoint
│   ├── grpc_server.py             # gRPC servicer
│   ├── server.py                  # REST + gRPC combined launcher
│   ├── service.py                 # PrivacyService orchestrator
│   ├── classification_routes.py   # Classification REST router
│   ├── classification_service.py  # Classification service wrapper
│   ├── classification_grpc.py     # Classification gRPC methods
│   ├── security/                  # TLS / auth / rate-limit
│   ├── observability/             # Logging / metrics / tracing
│   ├── privacy/                   # Primitives and classification
│   │   ├── masking.py
│   │   ├── dp.py
│   │   ├── kano.py
│   │   ├── qol.py
│   │   ├── budget.py
│   │   ├── profile.py
│   │   ├── classification.py
│   │   ├── classification_models.py
│   │   ├── classification_ner.py
│   │   ├── classification_llm.py
│   │   ├── download_model.py
│   │   └── download_ner_model.py
│   └── gateway/                   # Optional gateway/load balancer
│       ├── server.py
│       ├── balancer.py
│       ├── http_proxy.py
│       └── grpc_proxy.py
├── proto/privacy.proto            # gRPC service definition
├── tests/                         # pytest suite
├── docs/                          # Chinese design/PRD/ops docs
├── deploy/                        # Helm, K8s, Docker Compose
│   ├── helm/
│   ├── k8s/
│   └── docker-compose/
├── Makefile
├── pyproject.toml
├── requirements.txt               # Local dev/test deps
├── requirements-core.txt          # Core image runtime deps
├── requirements-ml.txt            # ML image extra deps
└── Dockerfile
```

## 4. Build & Test Commands

```bash
cd /home/charles/code/sfwork/privacy-local-agent

# Install in editable mode
pip install -e .

# Or install dev extras
pip install -e ".[dev]"

# Run tests
PYTHONPATH=. pytest tests -q

# Run a specific test file
PYTHONPATH=. pytest tests/test_rest.py -v

# Benchmark classification layers
PYTHONPATH=. python tests/benchmark_classification.py

# Download models (optional, required for LLM/NER layers)
python -m privacy_local_agent.privacy.download_model
python -m privacy_local_agent.privacy.download_ner_model
```

## 5. Running Locally

### REST + gRPC in one process

```bash
python -m privacy_local_agent.server
```

Defaults:
- REST: `http://127.0.0.1:8079`
- gRPC: `127.0.0.1:50051`

### REST only

```bash
python -m privacy_local_agent.main
```

### gRPC only

```bash
python -m privacy_local_agent.grpc_server
```

### Gateway + worker pool

```bash
python -m privacy_local_agent.gateway.server
```

## 6. Configuration

Key environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PRIVACY_PROFILE` | — | Path to YAML parameter profile |
| `PRIVACY_NAMESPACE` | `default` | Budget namespace |
| `PRIVACY_REST_HOST` | `127.0.0.1` | REST host |
| `PRIVACY_REST_PORT` | `8079` | REST port |
| `PRIVACY_GRPC_HOST` | `127.0.0.1` | gRPC host |
| `PRIVACY_GRPC_PORT` | `50051` | gRPC port |
| `PRIVACY_BUDGET_DB` | — | SQLite DB path for distributed budget |
| `PRIVACY_BUDGET_WINDOW_SECONDS` | — | Time window for automatic privacy budget reset |
| `PRIVACY_LOG_LEVEL` | `INFO` | Logging level |
| `PRIVACY_LOG_FORMAT` | `text` | `text` or `json` |
| `PRIVACY_SERVICE_NAME` | `privacy-local-agent` | Service name in logs/traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | Optional OpenTelemetry OTLP endpoint |
| `PRIVACY_TLS_ENABLED` | `false` | Enable TLS on REST/gRPC |
| `PRIVACY_AUTH_ENABLED` | `false` | Enable API key auth |
| `PRIVACY_RATE_LIMIT_ENABLED` | `false` | Enable rate limiting |
| `PRIVACY_REVIEW_DB` | — | SQLite DB path for classification review store |
| `PRIVACY_ASYNC_MAX_WORKERS` | `4` | Thread pool size for async classification jobs |
| `PRIVACY_ASYNC_JOB_TTL_SECONDS` | `3600` | TTL for async classification jobs |
| `PRIVACY_ASYNC_MAX_JOBS` | `1000` | Max concurrent async classification jobs |

## 7. Code Conventions

- Follow **PEP 8**.
- Use **type hints** on public functions.
- Use **Pydantic v2** models for request/response schemas.
- Keep primitives stateless; state lives in `PrivacyService` / `BudgetAccountant`.
- Lazy-load heavy ML models; never import `torch`/`transformers` at module top level unless unavoidable.
- Add tests for new primitives and classification rules.
- Prefer `pathlib.Path` over string paths.

## 8. Adding a New Privacy Primitive

1. Implement the algorithm in `privacy_local_agent/privacy/<primitive>.py`.
2. Add a Pydantic request/response model in `privacy_local_agent/privacy/classification_models.py` or a new models file.
3. Expose it in:
   - `privacy_local_agent/service.py` (business logic)
   - `privacy_local_agent/main.py` (REST route)
   - `privacy_local_agent/grpc_server.py` (gRPC method)
4. Add tests in `tests/test_rest.py` and/or `tests/test_<primitive>.py`.
5. Update `proto/privacy.proto` and regenerate stubs if adding gRPC:
   ```bash
   python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/privacy.proto
   ```

## 9. Adding a Classification Rule / Template / Composite Rule

### 9.1 Adding a Layer-1 Rule

1. Add rule logic in `privacy_local_agent/privacy/classification.py` (`DefaultRuleEngine`).
2. Update `ClassificationResult` models if new output fields are needed.
3. Add a test case in `tests/test_classification.py`.
4. Document the rule in `docs/classification/testing.md`.

### 9.2 Adding a Compliance Template

1. Define template defaults in `privacy_local_agent/privacy/classification_templates.py`.
2. If the template requires new field-name rules, add them in `DefaultRuleEngine._apply_template_field_rules`.
3. Add a test in `tests/test_classification_templates.py`.
4. Document the template in `docs/classification/prd.md` and `docs/classification/design.md`.

### 9.3 Adding a Composite Rule

1. Add the rule to `CompositeRuleEngine.DEFAULT_RULES` in `privacy_local_agent/privacy/classification_composite.py`,
   or pass it via the `compositeRules` request parameter.
2. Add a test in `tests/test_classification_composite.py`.
3. Document the rule in `docs/classification/design.md`.

### 9.4 Extending Async / Review APIs

1. Core logic lives in `privacy_local_agent/privacy/classification_async.py` and `privacy_local_agent/privacy/classification_review.py`.
2. Expose new methods via `ClassificationService` and REST/gRPC routes.
3. Update `proto/privacy.proto` and regenerate stubs when adding gRPC methods.
4. Add tests in `tests/test_classification_async.py` and `tests/test_classification_review.py`.

## 10. Testing Guidelines

- All changes must include tests.
- Mock heavy ML models in unit tests (see `tests/test_classification_ner.py` and `tests/test_classification_llm.py`).
- Gateway tests use `httpx` / `grpc.aio` channels; run them with the gateway server fixture.
- Budget tests cover both in-memory and SQLite backends.

## 11. Deployment Notes

### Docker

```bash
# core 镜像（默认推荐）
docker build --target core -t privacy-local-agent:0.1.0 .

# ml 镜像（含 torch/transformers/onnxruntime）
docker build --target ml -t privacy-local-agent:0.1.0-ml .

docker run -p 8079:8079 -p 50051:50051 privacy-local-agent:0.1.0
```

### Helm

```bash
helm install pla ./deploy/helm/privacy-local-agent

# 生产模式（需自管 TLS/API Key Secret）
helm install pla ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=your-tls-secret \
  --set security.auth.apiKeysSecret=your-apikeys-secret
```

### 原生 K8s

```bash
kubectl apply -k ./deploy/k8s/
```

### Docker Compose

```bash
cd deploy/docker-compose && docker-compose up -d
```

### Production Gaps

- KMS integration and automated key rotation are not yet implemented.
- Load/chaos/memory-leak test suites are not yet implemented.

Address these before any hardened production deployment.

## 12. Security Considerations

- Never commit model weights or large `.models/` files to git.
- Do not expose the gRPC/REST ports to untrusted networks without TLS.
- HMAC salt should be provided by the caller; consider KMS integration for production.
- Privacy budget in memory mode is not consistent across multiple instances; use `PRIVACY_BUDGET_DB` for multi-instance deployments.
- Validate and sanitize all inputs; Pydantic models are the first line of defense.

## 13. Key Documentation

| Document | Path | Purpose |
|---|---|---|
| README | `README.md` | Quick start and examples |
| Classification design | `docs/classification/design.md` | 3-layer funnel architecture |
| Classification ops | `docs/classification/ops.md` | Deployment and YAML profile |
| Classification PRD | `docs/classification/prd.md` | Requirements |
| LLM PRD | `docs/classification_llm/prd.md` | Multimodal LLM gateway requirements |
| Gateway design | `docs/gateway_balancer/design.md` | Gateway and load balancer |
| Production security PRD | `docs/production_security/prd.md` | TLS/auth/rate-limit requirements |
| Production security design | `docs/production_security/design.md` | TLS/auth/rate-limit architecture |
| Production security ops | `docs/production_security/ops.md` | Deployment and cert quick reference |
| Observability PRD | `docs/production_observability/prd.md` | Logging/metrics/tracing requirements |
| Observability design | `docs/production_observability/design.md` | Architecture and metric design |
| Observability ops | `docs/production_observability/ops.md` | Configuration and Grafana examples |
| Masking design | `docs/masking/design.md` | Field-name-aware masking architecture |
| Masking ops | `docs/masking/ops.md` | Masking deployment and tuning |
| Masking testing | `docs/masking/testing.md` | Masking test checklist |
| Query obfuscation design | `docs/qol/design.md` | Query obfuscation architecture |
| Query obfuscation ops | `docs/qol/ops.md` | Query obfuscation monitoring |
| Query obfuscation testing | `docs/qol/testing.md` | Query obfuscation test checklist |
| Deployment PRD | `docs/deployment/prd.md` | K8s/Helm/Docker Compose requirements |
| Deployment design | `docs/deployment/design.md` | Chart structure and parameters |
| Deployment ops | `docs/deployment/ops.md` | Install, upgrade and troubleshooting |

## 14. Quick Reference

| Goal | Command |
|---|---|
| Install | `pip install -e .` |
| Test | `PYTHONPATH=. pytest tests -q` |
| Helm lint | `make helm-lint` |
| Helm template | `make helm-template` |
| Build core image | `make docker-core` |
| Build ml image | `make docker-ml` |
| Run REST + gRPC | `python -m privacy_local_agent.server` |
| Run gateway | `python -m privacy_local_agent.gateway.server` |
| Regenerate gRPC stubs | `python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/privacy.proto` |
| Download LLM | `python -m privacy_local_agent.privacy.download_model` |
| Download NER | `python -m privacy_local_agent.privacy.download_ner_model` |
