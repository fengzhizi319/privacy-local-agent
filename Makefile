# privacy-local-agent 常用命令

.PHONY: help test helm-lint helm-template docker-core docker-ml clean

VERSION ?= 0.1.0
HELM_DIR = deploy/helm/privacy-local-agent

help:
	@echo "Available targets:"
	@echo "  test           - 运行 pytest 测试套件"
	@echo "  helm-lint      - helm lint 检查 chart"
	@echo "  helm-template  - helm template 渲染 chart"
	@echo "  docker-core    - 构建 core 镜像"
	@echo "  docker-ml      - 构建 ml 镜像"
	@echo "  clean          - 清理构建产物"

test:
	pytest tests -q

helm-lint:
	helm lint $(HELM_DIR)

helm-template:
	helm template test $(HELM_DIR)

docker-core:
	docker build --target core -t privacy-local-agent:$(VERSION) .

docker-ml:
	docker build --target ml -t privacy-local-agent:$(VERSION)-ml .

clean:
	rm -rf .pytest_cache __pycache__ .bin
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
