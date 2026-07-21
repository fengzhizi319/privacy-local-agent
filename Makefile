# privacy-local-agent 常用命令

.PHONY: help test test-cov test-unit lint format typecheck check cover cover-html bench \
        helm-lint helm-template docker-core docker-ml clean docs-serve docs-build docs-clean

VERSION ?= 0.1.0
HELM_DIR = deploy/helm/privacy-local-agent

help:
	@echo "Available targets:"
	@echo ""
	@echo "Quality:"
	@echo "  lint           - ruff 静态检查"
	@echo "  format         - ruff 自动格式化"
	@echo "  typecheck      - mypy 类型检查"
	@echo "  check          - lint + typecheck 一键检查"
	@echo ""
	@echo "Testing:"
	@echo "  test           - 运行 pytest 测试套件"
	@echo "  test-unit      - 仅运行单元测试（排除 integration/slow）"
	@echo "  test-cov       - 运行测试 + 覆盖率报告"
	@echo "  cover          - 同 test-cov"
	@echo "  cover-html     - 生成 HTML 覆盖率报告"
	@echo "  bench          - 运行性能基准测试"
	@echo ""
	@echo "Deployment:"
	@echo "  helm-lint      - helm lint 检查 chart"
	@echo "  helm-template  - helm template 渲染 chart"
	@echo "  docker-core    - 构建 core 镜像"
	@echo "  docker-ml      - 构建 ml 镜像"
	@echo ""
	@echo "Docs:"
	@echo "  docs-serve     - 启动 MkDocs 开发服务器"
	@echo "  docs-build     - 构建文档站点"
	@echo "  docs-clean     - 清理文档构建产物"
	@echo ""
	@echo "Other:"
	@echo "  clean          - 清理构建产物"

# ── Quality ──────────────────────────────────────────────────

lint:
	ruff check privacy_local_agent/ tests/

format:
	ruff format privacy_local_agent/ tests/
	ruff check --fix privacy_local_agent/ tests/

typecheck:
	mypy privacy_local_agent/ --ignore-missing-imports

check: lint typecheck

# ── Testing ──────────────────────────────────────────────────

test:
	pytest tests/ -q --tb=short

test-unit:
	pytest tests/ -q --tb=short -m "not integration and not slow"

test-cov:
	pytest tests/ -q --tb=short \
		--cov=privacy_local_agent \
		--cov-report=term-missing \
		-m "not integration and not slow"

cover: test-cov

cover-html:
	pytest tests/ -q --tb=short \
		--cov=privacy_local_agent \
		--cov-report=html \
		-m "not integration and not slow"
	@echo "Open htmlcov/index.html"

bench:
	pytest tests/ -q --benchmark-only --benchmark-columns=mean,stddev,rounds

# ── Deployment ───────────────────────────────────────────────

helm-lint:
	helm lint $(HELM_DIR)

helm-template:
	helm template test $(HELM_DIR)

docker-core:
	docker build --target core -t privacy-local-agent:$(VERSION) .

docker-ml:
	docker build --target ml -t privacy-local-agent:$(VERSION)-ml .

# ── Docs ─────────────────────────────────────────────────────

docs-serve:
	@echo "Starting MkDocs dev server..."
	mkdocs serve

docs-build:
	@echo "Building docs site..."
	mkdocs build

docs-clean:
	rm -rf site/

# ── Other ────────────────────────────────────────────────────

clean:
	rm -rf .pytest_cache __pycache__ .bin htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
