[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [string]$Target = "help",
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$Compose = "docker compose --env-file .env -f deploy/docker/docker-compose.yml"

switch ($Target) {
    "help" {
        Write-Host "AlphaTrader PowerShell Runner (make.ps1):"
        Write-Host "  .\make.ps1 install          - Install package and dev dependencies"
        Write-Host "  .\make.ps1 lint             - Run Ruff lint and format check"
        Write-Host "  .\make.ps1 format           - Format code with Ruff"
        Write-Host "  .\make.ps1 typecheck        - Run Mypy strict mode"
        Write-Host "  .\make.ps1 schemas          - Export JSON schemas"
        Write-Host "  .\make.ps1 schemas-check    - Verify schemas are up to date"
        Write-Host "  .\make.ps1 test             - Run Pytest with coverage"
        Write-Host "  .\make.ps1 test-unit        - Run unit tests only"
        Write-Host "  .\make.ps1 test-integration - Run integration tests"
        Write-Host "  .\make.ps1 check            - Run lint, typecheck, schemas-check, test"
        Write-Host "  .\make.ps1 up               - Start Docker infrastructure"
        Write-Host "  .\make.ps1 down             - Stop Docker infrastructure"
        Write-Host "  .\make.ps1 reset            - Tear down containers and volumes"
        Write-Host "  .\make.ps1 ps               - Show container status"
        Write-Host "  .\make.ps1 logs             - Tail container logs"
        Write-Host "  .\make.ps1 psql             - Open psql shell in Postgres"
        Write-Host "  .\make.ps1 redis-cli        - Open redis-cli shell in Redis"
        Write-Host "  .\make.ps1 topics           - Provision Kafka topics from registry"
        Write-Host "  .\make.ps1 smoke            - Run end-to-end infrastructure smoke test"
    }
    "install" { & $Python -m pip install -e ".[dev]" }
    "lint" {
        & $Python -m ruff check src tests scripts
        & $Python -m ruff format --check src tests scripts
    }
    "format" {
        & $Python -m ruff check --fix src tests scripts
        & $Python -m ruff format src tests scripts
    }
    "typecheck" { & $Python -m mypy --strict src/common scripts src/mcp_servers }
    "schemas" {
        & $Python -m scripts.export_schemas
        & $Python -m scripts.create_topics --generate-init-script
    }
    "schemas-check" { & $Python -m scripts.export_schemas --check }
    "test" { & $Python -m pytest }
    "test-unit" { & $Python -m pytest -m "not integration" }
    "test-integration" { & $Python -m pytest -m integration --no-cov }
    "test-streamer-coverage" {
        & $Python -m pytest tests/unit/test_market_data_streamer.py tests/integration/test_market_data_streamer.py -o "addopts=--cov=src.mcp_servers.market_data_streamer --cov-report=term-missing --cov-fail-under=90"
    }
    "check" {
        & $Python -m ruff check src tests scripts
        & $Python -m ruff format --check src tests scripts
        & $Python -m mypy --strict src/common scripts src/mcp_servers
        & $Python -m scripts.export_schemas --check
        & $Python -m pytest
    }
    "healthcheck" { & $Python -m src.common.healthcheck }
    "up" { Invoke-Expression "$Compose up -d" }
    "down" { Invoke-Expression "$Compose down" }
    "reset" {
        $force = ($ExtraArgs -contains "-y") -or ($ExtraArgs -contains "-f") -or ($ExtraArgs -contains "--force")
        if (-not $force) {
            $confirm = Read-Host "Are you sure you want to destroy all data volumes? [y/N]"
            if ($confirm -ne 'y' -and $confirm -ne 'Y') {
                Write-Host "Reset aborted."
                return
            }
        }
        Invoke-Expression "$Compose down -v"
    }
    "ps" { Invoke-Expression "$Compose ps" }
    "logs" { Invoke-Expression "$Compose logs -f" }
    "psql" { Invoke-Expression "$Compose exec postgres psql -U alphatrader -d alphatrader_ledger" }
    "redis-cli" { Invoke-Expression "$Compose exec redis redis-cli" }
    "topics" { & $Python scripts/create_topics.py }
    "smoke" { & $Python scripts/smoke_test.py }
    "stream" { & $Python -m src.mcp_servers.market_data_streamer.streamer }
    "mcp-market" { & $Python -m src.mcp_servers.market_data_streamer.server }
    "analyst" { & $Python -m src.mcp_servers.technical_analyst.analyst }
    "mcp-analyst" { & $Python -m src.mcp_servers.technical_analyst.server }
    "test-analyst-coverage" {
        & $Python -m pytest tests/unit/test_technical_analyst.py -o "addopts=--cov=src.mcp_servers.technical_analyst --cov-report=term-missing --cov-fail-under=90"
    }
    default {
        Write-Error "Unknown target: $Target. Run .\make.ps1 help for available targets."
        exit 1
    }
}
