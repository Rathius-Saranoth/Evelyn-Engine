"""
Unit and integration tests for Evelyn Versioning & DB Migration Framework.
"""

import os
import sqlite3
import tempfile

import pytest

from Evelyn.tools.db_migrator import (
    DatabaseSchemaMismatchError,
    Migration,
    MigrationExecutionError,
    apply_pending_migrations,
    check_all_dbs_status,
    ensure_tracking_table,
    get_applied_migrations,
    validate_db_schemas_or_raise,
)
from Evelyn.version import (
    compare_versions,
    format_version,
    is_valid_version,
    normalize_version,
    parse_version,
)


class TestVersionUtils:
    def test_version_format_and_parsing(self):
        assert format_version(0, 4, 0) == "000.004.000"
        assert format_version(1, 16, 55) == "001.016.055"

        major, minor, patch = parse_version("000.004.000")
        assert (major, minor, patch) == (0, 4, 0)

        # Standard unpadded parsing
        assert parse_version("0.4.0") == (0, 4, 0)
        assert parse_version("v0.4.0") == (0, 4, 0)

    def test_version_validation(self):
        assert is_valid_version("000.004.000") is True
        assert is_valid_version("1.2.3") is True
        assert is_valid_version("invalid-version") is False
        assert is_valid_version(123) is False

    def test_version_comparison_and_sorting(self):
        # Comparison ensures 1.16.55 is greater than 1.2.55 regardless of unpadded strings
        assert compare_versions("000.004.000", "000.004.000") == 0
        assert compare_versions("000.004.001", "000.004.000") == 1
        assert compare_versions("000.003.099", "000.004.000") == -1
        assert compare_versions("1.16.55", "1.2.55") == 1
        assert compare_versions("001.016.055", "001.002.055") == 1

    def test_normalize_version(self):
        assert normalize_version("0.4.0") == "000.004.000"
        assert normalize_version("v1.2.3") == "001.002.003"
        assert normalize_version("000.004.000") == "000.004.000"


class TestDatabaseMigrator:
    @pytest.fixture
    def temp_db(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    def test_ensure_tracking_table(self, temp_db):
        ensure_tracking_table(temp_db)
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            assert "schema_migrations" in tables

    def test_migration_sql_execution(self, temp_db, monkeypatch):
        # Override DB_MAP to point "memory" to temp_db
        monkeypatch.setattr("Evelyn.tools.db_migrator.DB_MAP", {"memory": temp_db})
        monkeypatch.setattr("Evelyn.tools.db_migrator.MIGRATIONS", [
            Migration(
                target_db="memory",
                version="000.001.000",
                name="create_test_table",
                up_sql="CREATE TABLE test_items (id INTEGER PRIMARY KEY, name TEXT);"
            )
        ])

        results = apply_pending_migrations(target_db="memory", target_version="000.001.000", create_snapshots=False)
        assert len(results) == 1
        assert results[0]["status"] == "success"

        # Verify tracking table records migration
        applied = get_applied_migrations(temp_db)
        assert "000.001.000" in applied
        assert applied["000.001.000"]["name"] == "create_test_table"
        assert applied["000.001.000"]["status"] == "success"

        # Verify actual table was created
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            assert "test_items" in tables

    def test_migration_python_callable_transform(self, temp_db, monkeypatch):
        # Create baseline table and data
        with sqlite3.connect(temp_db) as conn:
            conn.execute("CREATE TABLE legacy_notes (id INTEGER PRIMARY KEY, note_text TEXT);")
            conn.execute("INSERT INTO legacy_notes VALUES (1, 'tag1,tag2: observation body');")
            conn.commit()

        # Define Python transformation function using active conn
        def transform_fn(conn, db_paths, cfg):
            conn.execute("CREATE TABLE parsed_notes (id INTEGER PRIMARY KEY, tags TEXT, body TEXT);")
            rows = conn.execute("SELECT id, note_text FROM legacy_notes").fetchall()
            for row_id, text in rows:
                tags, body = text.split(":", 1)
                conn.execute("INSERT INTO parsed_notes VALUES (?, ?, ?)", (row_id, tags.strip(), body.strip()))

        monkeypatch.setattr("Evelyn.tools.db_migrator.DB_MAP", {"memory": temp_db})
        monkeypatch.setattr("Evelyn.tools.db_migrator.MIGRATIONS", [
            Migration(
                target_db="memory",
                version="000.001.000",
                name="python_transform_test",
                up_fn=transform_fn
            )
        ])

        results = apply_pending_migrations(target_db="memory", target_version="000.001.000", create_snapshots=False)
        assert len(results) == 1

        with sqlite3.connect(temp_db) as conn:
            row = conn.execute("SELECT id, tags, body FROM parsed_notes WHERE id = 1").fetchone()
            assert row == (1, "tag1,tag2", "observation body")

    def test_atomic_rollback_on_failure(self, temp_db, monkeypatch):
        def failing_fn(conn, db_paths, cfg):
            conn.execute("CREATE TABLE should_not_exist (id INTEGER PRIMARY KEY);")
            raise RuntimeError("Intentional migration error during data transform")

        monkeypatch.setattr("Evelyn.tools.db_migrator.DB_MAP", {"memory": temp_db})
        monkeypatch.setattr("Evelyn.tools.db_migrator.MIGRATIONS", [
            Migration(
                target_db="memory",
                version="000.001.000",
                name="failing_migration",
                up_fn=failing_fn
            )
        ])

        with pytest.raises(MigrationExecutionError):
            apply_pending_migrations(target_db="memory", target_version="000.001.000", create_snapshots=False)

        applied = get_applied_migrations(temp_db)
        assert "000.001.000" not in applied

    def test_strip_legacy_kw_tags_from_memory(self, temp_db, monkeypatch):
        from Evelyn.tools.db_migrator import strip_legacy_kw_tags_from_memory

        with sqlite3.connect(temp_db) as conn:
            conn.execute("""
                CREATE TABLE context_entries (
                    id INTEGER PRIMARY KEY,
                    tags TEXT,
                    observation TEXT
                );
            """)
            conn.execute("""
                CREATE TABLE proposals (
                    id INTEGER PRIMARY KEY,
                    merged_tags TEXT,
                    merged_observation TEXT
                );
            """)
            # Insert test records
            conn.execute(
                "INSERT INTO context_entries VALUES (1, ?, 'obs 1')",
                ("CY-2025/03/12, kw/new-beginnings, kw/Ricky_Sekulich, ctx/personal-growth",)
            )
            conn.execute(
                "INSERT INTO context_entries VALUES (2, ?, 'obs 2')",
                ("kw/plain-tag",)
            )
            conn.execute(
                "INSERT INTO context_entries VALUES (3, NULL, 'obs 3')"
            )
            conn.execute(
                "INSERT INTO proposals VALUES (1, ?, 'prop 1')",
                ("kw/alpha-tag, kw/BetaTag, ctx/gamma-tag",)
            )
            conn.commit()

        # Run migration function directly
        with sqlite3.connect(temp_db) as conn:
            strip_legacy_kw_tags_from_memory(conn, {"memory": temp_db}, None)
            conn.commit()

        with sqlite3.connect(temp_db) as conn:
            ce_rows = conn.execute("SELECT id, tags FROM context_entries ORDER BY id").fetchall()
            assert ce_rows[0] == (1, "CY-2025/03/12, new-beginnings, Ricky_Sekulich, personal-growth")
            assert ce_rows[1] == (2, "plain-tag")
            assert ce_rows[2] == (3, None)

            p_rows = conn.execute("SELECT id, merged_tags FROM proposals ORDER BY id").fetchall()
            assert p_rows[0] == (1, "alpha-tag, Beta_Tag, gamma-tag")

    def test_check_all_dbs_status_with_multi_versions(self, temp_db, monkeypatch):
        # Create schema_migrations tracking table in temp_db
        ensure_tracking_table(temp_db)
        with sqlite3.connect(temp_db) as conn:
            conn.execute("""
                INSERT INTO schema_migrations VALUES ('000.004.000', 'baseline', '2026-08-22T00:00:00Z', 10, 'success');
            """)
            conn.commit()

        monkeypatch.setattr("Evelyn.tools.db_migrator.DB_MAP", {"chat": temp_db, "memory": temp_db})
        monkeypatch.setattr("Evelyn.tools.db_migrator.MIGRATIONS", [
            Migration(target_db="chat", version="000.004.000", name="chat_base", up_sql="SELECT 1;"),
            Migration(target_db="memory", version="000.004.000", name="memory_base", up_sql="SELECT 1;"),
            Migration(target_db="memory", version="000.004.002", name="memory_v2", up_sql="SELECT 1;"),
        ])

        # At target 000.004.002, chat is up to date (no pending migrations), memory is pending 000.004.002
        status = check_all_dbs_status(target_version="000.004.002")
        assert status["chat"]["is_up_to_date"] is True
        assert status["memory"]["is_up_to_date"] is False
        assert status["memory"]["pending_count"] == 1

        monkeypatch.setattr("Evelyn.tools.db_migrator.__version__", "000.004.002")
        with pytest.raises(DatabaseSchemaMismatchError):
            validate_db_schemas_or_raise()

        # Apply migration for memory
        with sqlite3.connect(temp_db) as conn:
            conn.execute("""
                INSERT INTO schema_migrations VALUES ('000.004.002', 'memory_v2', '2026-08-22T00:00:00Z', 10, 'success');
            """)
            conn.commit()

        status_after = check_all_dbs_status(target_version="000.004.002")
        assert status_after["chat"]["is_up_to_date"] is True
        assert status_after["memory"]["is_up_to_date"] is True
        # Now validation passes without error
        validate_db_schemas_or_raise()

