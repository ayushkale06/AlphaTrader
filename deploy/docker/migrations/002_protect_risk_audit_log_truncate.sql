-- =============================================================================
-- Migration 002: Statement-level trigger blocking TRUNCATE on risk_audit_log
-- Enforces append-only immutability against statement-level TRUNCATE (ADR-004).
-- =============================================================================

DROP TRIGGER IF EXISTS trg_prevent_risk_audit_log_truncate ON risk_audit_log;
CREATE TRIGGER trg_prevent_risk_audit_log_truncate
    BEFORE TRUNCATE ON risk_audit_log
    FOR EACH STATEMENT
    EXECUTE FUNCTION prevent_risk_audit_log_modification();

INSERT INTO schema_migrations (version, description)
VALUES (
    '002_protect_risk_audit_log_truncate',
    'Statement-level trigger blocking TRUNCATE on risk_audit_log for append-only immutability (ADR-004)'
)
ON CONFLICT (version) DO NOTHING;
