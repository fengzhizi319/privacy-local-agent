# privacy-local-agent 多阶段构建
# 支持两种构建目标：
#   --target core : 轻量镜像，仅含隐私原语（DP / K-匿名 / 分类规则接口）
#   --target ml   : 完整镜像，额外包含 torch / transformers / onnxruntime，用于本地 LLM/NER 分类
#
# 示例：
#   docker build --target core -t privacy-local-agent:0.1.0 .
#   docker build --target ml -t privacy-local-agent:0.1.0-ml .

FROM python:3.10-slim AS base

WORKDIR /app

# 安装基础系统工具与 curl（用于 K8s 探针）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先安装核心依赖，利用镜像缓存
COPY requirements-core.txt .
RUN pip install --no-cache-dir -r requirements-core.txt

# ------------------- core 目标 -------------------
FROM base AS core

COPY . .

EXPOSE 8079 50051

ENV PYTHONUNBUFFERED=1
ENV PRIVACY_REST_HOST=0.0.0.0
ENV PRIVACY_GRPC_HOST=0.0.0.0

CMD ["python", "-m", "privacy_local_agent.server"]

# ------------------- ml 目标 -------------------
FROM core AS ml

COPY requirements-ml.txt .
RUN pip install --no-cache-dir -r requirements-ml.txt

CMD ["python", "-m", "privacy_local_agent.server"]
