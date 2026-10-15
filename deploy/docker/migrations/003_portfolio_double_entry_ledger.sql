-- =============================================================================
-- Migration 003: Double-Entry Ledger Accounts, Journal Entries, Postings, and Integrity Triggers
-- Governance: ADR-002 (Paper Only), ADR-004 (System of Record), ADR-010 (Double-Entry Ledger)
-- =============================================================================

SELECT pg_advisory_xact_lock(74218942);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = '003_portfolio_double_entry_ledger') THEN

        -- ---------------------------------------------------------------------
        -- 1. Ledger Accounts (Chart of Accounts)
        -- ---------------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS ledger_accounts (
            account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_code VARCHAR(64) NOT NULL UNIQUE,
            account_name VARCHAR(128) NOT NULL,
            account_type VARCHAR(32) NOT NULL CHECK (account_type IN ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE')),
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- ---------------------------------------------------------------------
        -- 2. Processed Ingestion Events (Durable Idempotency Wall)
        -- ---------------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS processed_events (
            event_id UUID PRIMARY KEY,
            topic VARCHAR(128) NOT NULL,
            correlation_id VARCHAR(128) NOT NULL,
            payload_hash CHAR(64) NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- ---------------------------------------------------------------------
        -- 3. Monotonic Revision Tracker for Serialized Commit Ordering
        -- ---------------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS portfolio_revision_tracker (
            tracker_id INT PRIMARY KEY DEFAULT 1 CHECK (tracker_id = 1),
            current_revision BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        INSERT INTO portfolio_revision_tracker (tracker_id, current_revision)
        VALUES (1, 0)
        ON CONFLICT (tracker_id) DO NOTHING;

        -- ---------------------------------------------------------------------
        -- 4. Journal Entries (Transaction Header)
        -- ---------------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS journal_entries (
            entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id UUID NOT NULL REFERENCES processed_events(event_id) ON DELETE RESTRICT,
            fill_id UUID REFERENCES fills(fill_id) ON DELETE RESTRICT,
            narration TEXT NOT NULL,
            posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            correlation_id VARCHAR(128) NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_journal_entries_posted_at ON journal_entries (posted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_journal_entries_correlation_id ON journal_entries (correlation_id);

        -- ---------------------------------------------------------------------
        -- 5. Journal Postings (Debit/Credit Line Items)
        -- ---------------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS journal_postings (
            posting_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entry_id UUID NOT NULL REFERENCES journal_entries(entry_id) ON DELETE RESTRICT,
            account_id UUID NOT NULL REFERENCES ledger_accounts(account_id) ON DELETE RESTRICT,
            direction VARCHAR(8) NOT NULL CHECK (direction IN ('DEBIT', 'CREDIT')),
            amount NUMERIC(20, 4) NOT NULL CHECK (amount > 0),
            sequence_num INT NOT NULL,
            CONSTRAINT uq_entry_sequence UNIQUE (entry_id, sequence_num)
        );

        CREATE INDEX IF NOT EXISTS idx_journal_postings_entry_id ON journal_postings (entry_id);
        CREATE INDEX IF NOT EXISTS idx_journal_postings_account_id ON journal_postings (account_id);

        -- ---------------------------------------------------------------------
        -- 6. Deferred Commit-Time Integrity Verification Triggers
        -- ---------------------------------------------------------------------
        CREATE OR REPLACE FUNCTION verify_journal_header_not_empty()
        RETURNS TRIGGER AS $f$
        DECLARE
            v_count INT;
        BEGIN
            SELECT COUNT(*) INTO v_count
            FROM journal_postings
            WHERE entry_id = NEW.entry_id;

            IF v_count < 2 THEN
                RAISE EXCEPTION 'Journal entry % committed with invalid posting count (%). Expected at least 2 postings.',
                    NEW.entry_id, v_count;
            END IF;

            RETURN NEW;
        END;
        $f$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_verify_journal_header_not_empty ON journal_entries;
        CREATE CONSTRAINT TRIGGER trg_verify_journal_header_not_empty
            AFTER INSERT ON journal_entries
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION verify_journal_header_not_empty();

        CREATE OR REPLACE FUNCTION verify_journal_postings_balanced()
        RETURNS TRIGGER AS $f$
        DECLARE
            v_debit_count INT;
            v_credit_count INT;
            v_debit_sum NUMERIC(20, 4);
            v_credit_sum NUMERIC(20, 4);
        BEGIN
            SELECT 
                COUNT(*) FILTER (WHERE direction = 'DEBIT'),
                COUNT(*) FILTER (WHERE direction = 'CREDIT'),
                COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE 0 END), 0)
            INTO v_debit_count, v_credit_count, v_debit_sum, v_credit_sum
            FROM journal_postings
            WHERE entry_id = NEW.entry_id;

            IF v_debit_count < 1 OR v_credit_count < 1 THEN
                RAISE EXCEPTION 'Journal entry % must contain at least one DEBIT and at least one CREDIT (debits=%, credits=%).',
                    NEW.entry_id, v_debit_count, v_credit_count;
            END IF;

            IF v_debit_sum <> v_credit_sum THEN
                RAISE EXCEPTION 'Double-entry balance violation for entry %: debits (%) != credits (%) [diff: %].',
                    NEW.entry_id, v_debit_sum, v_credit_sum, (v_debit_sum - v_credit_sum);
            END IF;

            RETURN NEW;
        END;
        $f$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_verify_journal_postings_balanced ON journal_postings;
        CREATE CONSTRAINT TRIGGER trg_verify_journal_postings_balanced
            AFTER INSERT OR UPDATE ON journal_postings
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION verify_journal_postings_balanced();

        -- ---------------------------------------------------------------------
        -- 7. Immutability & Anti-Truncate Protection Triggers
        -- ---------------------------------------------------------------------
        CREATE OR REPLACE FUNCTION prevent_ledger_modification()
        RETURNS TRIGGER AS $f$
        BEGIN
            RAISE EXCEPTION '% is immutable: % operations are prohibited (ADR-004/ADR-010)',
                TG_TABLE_NAME, TG_OP;
            RETURN NULL;
        END;
        $f$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_prevent_journal_entries_mutation ON journal_entries;
        CREATE TRIGGER trg_prevent_journal_entries_mutation
            BEFORE UPDATE OR DELETE ON journal_entries
            FOR EACH ROW EXECUTE FUNCTION prevent_ledger_modification();

        DROP TRIGGER IF EXISTS trg_prevent_journal_entries_truncate ON journal_entries;
        CREATE TRIGGER trg_prevent_journal_entries_truncate
            BEFORE TRUNCATE ON journal_entries
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_modification();

        DROP TRIGGER IF EXISTS trg_prevent_journal_postings_mutation ON journal_postings;
        CREATE TRIGGER trg_prevent_journal_postings_mutation
            BEFORE UPDATE OR DELETE ON journal_postings
            FOR EACH ROW EXECUTE FUNCTION prevent_ledger_modification();

        DROP TRIGGER IF EXISTS trg_prevent_journal_postings_truncate ON journal_postings;
        CREATE TRIGGER trg_prevent_journal_postings_truncate
            BEFORE TRUNCATE ON journal_postings
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_modification();

        -- ---------------------------------------------------------------------
        -- 8. Enforce No-Short Policy on Positions Table
        -- ---------------------------------------------------------------------
        ALTER TABLE positions DROP CONSTRAINT IF EXISTS check_no_short_inventory;
        ALTER TABLE positions ADD CONSTRAINT check_no_short_inventory CHECK (quantity >= 0);

        -- ---------------------------------------------------------------------
        -- 9. Record Migration Completion
        -- ---------------------------------------------------------------------
        INSERT INTO schema_migrations (version, description)
        VALUES (
            '003_portfolio_double_entry_ledger',
            'Double-entry ledger accounts, journal entries, postings, and integrity triggers'
        );

    END IF;
END $$;
