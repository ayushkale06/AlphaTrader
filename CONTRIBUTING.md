# Contributing to AlphaTrader

AlphaTrader is an engineering-rigorous, audited multi-agent algorithmic paper-trading platform. Every contribution must pass all automated quality gates (`make check`) and follow our commit, safety, and documentation standards.

---

## 1. Non-Negotiable Safety Rules

1. **Paper Trading Only**: AlphaTrader is strictly a paper-trading and quantitative research platform. Any PR introducing live brokerage execution SDKs, real-money order endpoints, or disabling the `TRADING_MODE=PAPER` / `LIVE_TRADING_ENABLED=False` invariants will be immediately rejected.
2. **Deterministic Risk Guardrail**: Pre-trade and intraday risk limits in `src/mcp_servers/risk_guardrail` must remain 100% deterministic, non-LLM Python code. Never introduce LLM prompts or probabilistic overrides into risk calculation paths.
3. **Zero Secrets in Git**: Never commit `.env` files, API tokens, database passwords, or private keys. Always document new configuration variables in `.env.example` with safe defaults.

---

## 2. Conventional Commit Standard

We follow the [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) specification. Every commit message must be structured as:

```text
<type>(<optional-scope>): <short imperative summary>

<optional body explaining the "why" and architectural trade-offs>

<optional footer(s), e.g., BREAKING CHANGE: or Refs #issue>
```

### Allowed Commit Types

| Type | When to Use | Example |
|---|---|---|
| `feat` | A new feature, module, agent capability, or MCP tool | `feat(common): add typed pydantic-settings config and correlation-id JSON logger` |
| `fix` | A bug fix in runtime logic, schema validation, or risk calculation | `fix(risk): handle zero high-water-mark division on initial portfolio boot` |
| `docs` | Documentation-only changes (`README.md`, ADRs, `ROADMAP.md`, `DEV_LOG.md`) | `docs(adr): record ADR-001 multi-agent MCP architecture and ADR-002 paper-only boundary` |
| `test` | Adding missing unit/integration tests or refactoring existing tests | `test(common): add unit tests for config safety invariants and JSON log correlation IDs` |
| `ci` | Changes to GitHub Actions workflows, pre-commit hooks, or Makefile targets | `ci: add GitHub Actions workflow for ruff, mypy strict, and pytest coverage` |
| `chore` | Build scripts, dependency pins, repository scaffolding, `.gitignore` | `chore: initialize repository structure, package skeletons, and license` |
| `refactor` | Code change that neither fixes a bug nor adds a feature | `refactor(ledger): extract double-entry balance assertion into pure helper` |
| `perf` | A code change that improves latency or throughput | `perf(streamer): batch Redpanda tick serialization using pre-compiled TypeAdapter` |

### Commit Hygiene Rules

- **Imperative Mood**: Write `"add healthcheck CLI"`, not `"added healthcheck CLI"` or `"adds healthcheck CLI"`.
- **Small, Atomic Commits**: Each commit should represent a single logical unit of work that leaves the repository in a passing state. Do not bundle unrelated refactors, features, and documentation into a single monolithic commit.
- **Line Length**: Keep the subject line under 72 characters.

---

## 3. Local Development & Quality Gates

Before committing or pushing, run the full quality gate suite from the repository root:

```bash
# Install runtime + dev dependencies
make install

# Format code with Ruff
make format

# Run all quality gates (lint + format check + strict mypy + pytest with coverage)
make check

# Verify the system healthcheck CLI
python -m src.common.healthcheck
```

All checks must exit with code `0` and maintain $\ge 90\%$ test coverage on implemented foundational modules.
