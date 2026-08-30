import sqlite3
import unittest
from unittest.mock import patch

from backend.internals import db_migration
from backend.internals.db import DB_SCHEMA, DB_SCHEMA_INDEXES


class DatabaseMigrationReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')

    def tearDown(self):
        self.connection.close()

    def _create_common_tables(self, created_at=False, forced=False):
        created_at_column = (
            ', created_at INTEGER NOT NULL DEFAULT 0'
            if created_at
            else ''
        )
        forced_column = (
            ', forced BOOL NOT NULL DEFAULT 0'
            if forced
            else ''
        )
        self.connection.executescript(f"""
            CREATE TABLE volumes (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                year INTEGER,
                volume_number INTEGER,
                publisher TEXT,
                monitored BOOL NOT NULL DEFAULT 0
                {created_at_column}
            );
            CREATE TABLE issues (
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                calculated_issue_number REAL NOT NULL DEFAULT 0,
                monitored BOOL NOT NULL DEFAULT 1
            );
            CREATE TABLE issues_files (
                file_id INTEGER NOT NULL,
                issue_id INTEGER NOT NULL
                {forced_column}
            );
            CREATE TABLE volume_files (
                file_id INTEGER NOT NULL,
                volume_id INTEGER NOT NULL
                {forced_column}
            );
        """)

    def _columns(self, table):
        return {
            row[1]
            for row in self.connection.execute(f'PRAGMA table_info({table});')
        }

    def _run_reconciliation(self):
        with patch.object(
            db_migration,
            'get_db',
            return_value=self.connection
        ):
            db_migration._migrate_reconcile_divergent_version_44()

    def test_release_discovery_migration_is_idempotent(self):
        self.connection.execute(
            'CREATE TABLE volumes (id INTEGER PRIMARY KEY);'
        )
        with patch.object(
            db_migration,
            'get_db',
            return_value=self.connection
        ):
            db_migration._migrate_add_release_discovery()
            db_migration._migrate_add_release_discovery()

        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table';"
            )
        }
        self.assertIn('release_discovery', tables)
        self.assertIn('pending_release_watches', tables)

    def test_reconciles_upstream_version_45_schema(self):
        self._create_common_tables(forced=True)

        self._run_reconciliation()

        self.assertIn('created_at', self._columns('volumes'))
        self.assertIn('store_date', self._columns('issues'))
        self.assertIn('forced', self._columns('issues_files'))
        self.assertIn('forced', self._columns('volume_files'))
        self.assertIsNotNone(self.connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'release_cache';"
        ).fetchone())
        self.assertIsNotNone(self.connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'publisher_cache';"
        ).fetchone())

    def test_reconciles_fork_version_46_schema_idempotently(self):
        self._create_common_tables(created_at=True)
        with patch.object(
            db_migration,
            'get_db',
            return_value=self.connection
        ):
            db_migration._migrate_add_release_cache_and_publisher_tables()

        self._run_reconciliation()
        self._run_reconciliation()

        self.assertIn('forced', self._columns('issues_files'))
        self.assertIn('forced', self._columns('volume_files'))
        self.assertIn('created_at', self._columns('volumes'))
        self.assertIn('store_date', self._columns('issues'))

    def test_source_aware_cache_allows_ids_from_multiple_sources(self):
        self._create_common_tables()
        with patch.object(
            db_migration,
            'get_db',
            return_value=self.connection
        ):
            db_migration._migrate_add_release_cache_and_publisher_tables()
            db_migration._migrate_source_aware_metadata_cache()

        release = (
            1, 2, 'Example', '1', 1.0,
            '2026-08-01', '2026-08-01', None, None, 1
        )
        for source in ('comicvine', 'metron'):
            self.connection.execute("""
                INSERT INTO release_cache(
                    metadata_source, issue_cv_id, volume_cv_id,
                    volume_title, issue_number, calculated_issue_number,
                    store_date, cover_date, cover_url, publisher, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (source, *release))

        count = self.connection.execute(
            'SELECT COUNT(*) FROM release_cache;'
        ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_startup_schema_allows_legacy_cache_before_migration(self):
        self.connection.executescript("""
            CREATE TABLE release_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_cv_id INTEGER NOT NULL UNIQUE,
                volume_cv_id INTEGER NOT NULL,
                volume_title TEXT NOT NULL,
                issue_number TEXT NOT NULL,
                calculated_issue_number REAL,
                store_date TEXT,
                cover_date TEXT,
                cover_url TEXT,
                publisher TEXT,
                fetched_at INTEGER NOT NULL
            );
            CREATE TABLE publisher_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comicvine_id INTEGER NOT NULL UNIQUE,
                name TEXT NOT NULL,
                site_url TEXT,
                volume_count INTEGER DEFAULT 0,
                fetched_at INTEGER NOT NULL
            );
        """)

        self.connection.executescript(DB_SCHEMA)
        with patch.object(
            db_migration,
            'get_db',
            return_value=self.connection
        ):
            db_migration._migrate_reconcile_divergent_version_44()
            db_migration._migrate_source_aware_metadata_cache()
        self.connection.executescript(DB_SCHEMA_INDEXES)

        self.assertIn('metadata_source', self._columns('release_cache'))
        indexes = {
            row[1]
            for row in self.connection.execute(
                'PRAGMA index_list(release_cache);'
            )
        }
        self.assertIn('release_cache_source_date_index', indexes)

    def test_startup_schema_allows_missing_created_at_before_migration(self):
        self._create_common_tables(forced=True)

        self.connection.executescript(DB_SCHEMA)

    def test_fresh_schema_contains_current_metadata_cache(self):
        self.connection.executescript(DB_SCHEMA)
        self.connection.executescript(DB_SCHEMA_INDEXES)

        self.assertIn('store_date', self._columns('issues'))
        issue_columns = [
            row[1]
            for row in self.connection.execute('PRAGMA table_info(issues);')
        ]
        self.assertEqual(issue_columns[-1], 'store_date')
        self.assertIn('metadata_source', self._columns('release_cache'))
        self.assertIn('metadata_source', self._columns('publisher_cache'))
        self.assertTrue(self._columns('release_cache_windows'))
        self.assertTrue(self._columns('metadata_response_cache'))
        indexes = {
            row[1]
            for row in self.connection.execute('PRAGMA index_list(volumes);')
        }
        self.assertIn('volumes_created_at_index', indexes)


if __name__ == '__main__':
    unittest.main()
