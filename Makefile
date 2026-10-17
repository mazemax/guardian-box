# GuardianBox Makefile
# Target: NVIDIA DGX Spark (GB10, ARM64, 128GB unified memory)

.PHONY: help up down up-mock down-mock build doctor test test-unit test-scenarios \
        export-results logs clean pull-models shell lint

COMPOSE := docker compose
PROFILE_GPU := --profile gpu
PROFILE_MOCK := --profile mock

# Default target
help:
	@echo "GuardianBox - Local Fall Detection and Voice Check-in"
	@echo ""
	@echo "Usage:"
	@echo "  make up           Start all services (requires GPU)"
	@echo "  make up-mock      Start in mock/simulation mode (no GPU required)"
	@echo "  make down         Stop all services"
	@echo "  make build        Build all container images"
	@echo "  make doctor       Check system health and prerequisites"
	@echo "  make test         Run full test suite in mock mode"
	@echo "  make test-unit    Run unit tests only"
	@echo "  make test-scenarios  Run scenario tests (F1-F8, T1-T8)"
	@echo "  make export-results  Export test results to tests/scenarios/results.csv"
	@echo "  make logs         Follow container logs"
	@echo "  make clean        Remove containers, volumes, and build artifacts"
	@echo "  make pull-models  Download required model weights (run before the hackathon)"
	@echo "  make shell        Open a shell in the orchestrator container"
	@echo "  make lint         Run linters on Python code"

# =============================================================================
# Service Management
# =============================================================================

up:
	@echo "Starting GuardianBox with GPU profile..."
	$(COMPOSE) $(PROFILE_GPU) up -d
	@echo ""
	@echo "Dashboard available at: http://localhost:$${DASHBOARD_PORT:-8080}"
	@echo "Run 'make logs' to follow container logs"

up-mock:
	@echo "Starting GuardianBox in MOCK mode (no GPU required)..."
	MOCK_MODE=true $(COMPOSE) $(PROFILE_MOCK) up -d orchestrator notifier egress-proxy vision-mock vlm-mock stt-mock voice-mock
	@echo ""
	@echo "Dashboard available at: http://localhost:$${DASHBOARD_PORT:-8080}"
	@echo "Run 'make logs' to follow container logs"

down:
	@echo "Stopping all GuardianBox services..."
	$(COMPOSE) $(PROFILE_GPU) $(PROFILE_MOCK) down

build:
	@echo "Building all container images..."
	$(COMPOSE) $(PROFILE_GPU) $(PROFILE_MOCK) build

# =============================================================================
# Health Checks
# =============================================================================

doctor:
	@echo "=== GuardianBox System Check ==="
	@echo ""
	@echo "1. Docker..."
	@docker --version || (echo "ERROR: Docker not found" && exit 1)
	@echo ""
	@echo "2. Docker Compose..."
	@docker compose version || (echo "ERROR: Docker Compose not found" && exit 1)
	@echo ""
	@echo "3. NVIDIA Container Toolkit..."
	@docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi > /dev/null 2>&1 \
		&& echo "OK: GPU access working" \
		|| echo "WARNING: GPU not available (mock mode still works)"
	@echo ""
	@echo "4. Checking .env file..."
	@test -f .env && echo "OK: .env exists" || echo "WARNING: .env not found (copy from .env.example)"
	@echo ""
	@echo "5. Checking model cache..."
	@test -d "$${HF_CACHE:-$$HOME/.cache/huggingface}" \
		&& echo "OK: HF cache exists at $${HF_CACHE:-$$HOME/.cache/huggingface}" \
		|| echo "WARNING: HF cache not found"
	@echo ""
	@echo "6. Validating docker-compose.yml..."
	@$(COMPOSE) config --quiet && echo "OK: docker-compose.yml is valid" || echo "ERROR: Invalid compose file"
	@echo ""
	@echo "=== Check Complete ==="

# =============================================================================
# Testing
# =============================================================================

test: test-unit test-scenarios
	@echo "All tests complete!"

test-unit:
	@echo "Running unit tests..."
	cd services/orchestrator && python -m pytest tests/ -v --tb=short

test-scenarios:
	@echo "Running scenario tests in mock mode..."
	MOCK_MODE=true $(COMPOSE) $(PROFILE_MOCK) up -d orchestrator notifier egress-proxy vlm-mock stt-mock voice-mock
	@sleep 5  # Wait for services to be ready
	cd services/orchestrator && python -m pytest tests/test_scenarios.py -v --tb=short
	$(COMPOSE) $(PROFILE_MOCK) down

export-results:
	@echo "Exporting test results..."
	cd services/orchestrator && python -c "from tests.export_results import export; export()"
	@echo "Results written to tests/scenarios/results.csv"

# =============================================================================
# Development
# =============================================================================

logs:
	$(COMPOSE) $(PROFILE_GPU) $(PROFILE_MOCK) logs -f

shell:
	$(COMPOSE) exec orchestrator /bin/bash

lint:
	@echo "Running linters..."
	cd services/orchestrator && python -m ruff check . && python -m ruff format --check .
	cd services/notifier && python -m ruff check . && python -m ruff format --check .

# =============================================================================
# Cleanup
# =============================================================================

clean:
	@echo "Stopping and removing all GuardianBox resources..."
	$(COMPOSE) $(PROFILE_GPU) $(PROFILE_MOCK) down -v --remove-orphans
	docker image rm -f guardianbox-orchestrator guardianbox-vision guardianbox-stt \
		guardianbox-voice guardianbox-notifier guardianbox-egress-proxy \
		guardianbox-vision-mock guardianbox-vlm-mock guardianbox-stt-mock \
		guardianbox-voice-mock 2>/dev/null || true
	rm -rf services/orchestrator/__pycache__ services/orchestrator/.pytest_cache
	rm -rf services/notifier/__pycache__
	@echo "Clean complete"

# =============================================================================
# Model Download (run before hackathon)
# =============================================================================

pull-models:
	@echo "Downloading required model weights..."
	@echo "This may take a while depending on your connection."
	@echo ""
	@echo "1. Nemotron 3 Nano Omni (NVFP4)..."
	huggingface-cli download nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
	@echo ""
	@echo "2. Malaysian Whisper large-v3-turbo..."
	huggingface-cli download mesolitica/Malaysian-whisper-large-v3-turbo-v3 || \
		huggingface-cli download openai/whisper-large-v3-turbo
	@echo ""
	@echo "3. Kokoro-82M TTS..."
	huggingface-cli download hexgrad/Kokoro-82M
	@echo ""
	@echo "4. YOLO pose model..."
	@echo "Note: YOLO model will be downloaded on first container start"
	@echo ""
	@echo "Model download complete!"
