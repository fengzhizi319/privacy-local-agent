# Privacy Local Agent

Welcome to the **Privacy Local Agent** documentation.

`privacy-local-agent` is a Python sidecar that exposes privacy primitives (masking, differential privacy, K-anonymity, query obfuscation) and a 3-layer data classification funnel over REST and gRPC. It is designed for local/Sidecar deployment and is currently at POC/MVP maturity.

---

## Capabilities

| Capability | Status | Description |
|---|---|---|
| Masking | ✅ Ready | Field-name-aware masking for common PII |
| Differential Privacy | ✅ Ready | Laplace/Gaussian count/sum/mean with budget accounting |
| K-anonymity | ✅ Ready | Per-record heuristic & dataset-level (Mondrian) |
| Query Obfuscation | ✅ Ready | Dummy query injection |
| Classification | ✅ Ready | Rule engine → Small-NER → local VLM/LLM |
| Gateway / Load Balancer | ✅ Ready | REST + gRPC reverse proxy with health checks |
| TLS / Auth / Rate Limit | ✅ Ready | Opt-in via environment variables |
| Observability | ✅ Ready | Structured logs + Prometheus `/metrics` + tracing |
| K8s / Helm Deployment | ✅ Ready | Helm chart + Kustomize + Docker Compose |

## Quick Navigation

- **Privacy Primitives** — Masking, DP, K-Anonymity, Query Obfuscation
- **Data Classification** — 3-layer funnel: Rule Engine → NER → LLM
- **Infrastructure** — Gateway, load balancer, health checks
- **Production** — Security, observability, deployment
- **Appendix** — Personalized profiles, improvement suggestions

---

!!! tip "Getting Started"
    Head over to the [Quick Start](quickstart.md) guide to install and run the agent locally.
