import sqlite3
import unittest
from unittest.mock import patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import (
    LibraryDateFilter,
    LibraryFilter,
    LibraryStatusFilter,
)
from backend.implementations.volumes import Library


class LibraryFilterTest(unittest.TestCase):
    @patch('backend.implementations.volumes.time', return_value=40_000_000)
    def test_recently_added_365_clause(self, _time):
        clause, params = Library._get_filter_clause(
            LibraryFilter.RECENTLY_ADDED_365
        )

        self.assertEqual(clause, 'WHERE created_at >= ?')
        self.assertEqual(params, [40_000_000 - 365 * 24 * 60 * 60])

    def test_recently_released_180_clause(self):
        clause, params = Library._get_filter_clause(
            None,
            date_filter=LibraryDateFilter.RECENTLY_RELEASED_180
        )

        self.assertIn("date('now', ?)", clause)
        self.assertEqual(params, ['-180 days'])

    @patch('backend.implementations.volumes.time', return_value=40_000_000)
    def test_status_and_date_filters_are_combined(self, _time):
        clause, params = Library._get_filter_clause(
            None,
            status_filter=LibraryStatusFilter.MISSING_MONITORED,
            date_filter=LibraryDateFilter.RECENTLY_ADDED_180
        )

        self.assertIn('i.monitored = 1', clause)
        self.assertIn('created_at >= ?', clause)
        self.assertIn(' AND ', clause)
        self.assertEqual(params, [40_000_000 - 180 * 24 * 60 * 60])

    def test_legacy_filter_is_classified(self):
        legacy_clause, legacy_params = Library._get_filter_clause(
            LibraryFilter.MISSING_MONITORED
        )
        new_clause, new_params = Library._get_filter_clause(
            None,
            status_filter=LibraryStatusFilter.MISSING_MONITORED
        )

        self.assertEqual((legacy_clause, legacy_params),
                         (new_clause, new_params))

    def test_legacy_and_dimension_filters_are_rejected(self):
        with self.assertRaises(InvalidKeyValue):
            Library._get_filter_clause(
                LibraryFilter.MISSING_MONITORED,
                date_filter=LibraryDateFilter.RECENTLY_RELEASED_30
            )

    def test_partially_downloaded_requires_files_and_gaps(self):
        clause, params = Library._get_filter_clause(
            None,
            status_filter=LibraryStatusFilter.PARTIALLY_DOWNLOADED
        )

        self.assertEqual(clause.count('EXISTS ('), 2)
        self.assertIn('INNER JOIN issues_files', clause)
        self.assertIn('if.issue_id IS NULL', clause)
        self.assertEqual(params, [])

    def test_missing_monitored_and_recently_released_execute_together(self):
        connection = sqlite3.connect(':memory:')
        connection.executescript("""
            CREATE TABLE volumes (
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER,
                monitored BOOL,
                created_at INTEGER,
                publisher TEXT,
                description TEXT
            );
            CREATE TABLE issues (
                id INTEGER PRIMARY KEY,
                volume_id INTEGER,
                monitored BOOL,
                date TEXT
            );
            CREATE TABLE issues_files (issue_id INTEGER, file_id INTEGER);

            INSERT INTO volumes VALUES
                (1, 101, 1, 0, 'DC Comics', ''),
                (2, 102, 1, 0, 'DC Comics', ''),
                (3, 103, 1, 0, 'DC Comics', '');
            INSERT INTO issues VALUES
                (11, 1, 1, date('now', '-30 days')),
                (21, 2, 1, date('now', '-300 days')),
                (31, 3, 1, date('now', '-30 days'));
            INSERT INTO issues_files VALUES (31, 301);
        """)
        clause, params = Library._get_filter_clause(
            None,
            status_filter=LibraryStatusFilter.MISSING_MONITORED,
            date_filter=LibraryDateFilter.RECENTLY_RELEASED_180
        )

        result = connection.execute(
            f'SELECT id FROM volumes {clause} ORDER BY id;',
            params
        ).fetchall()
        connection.close()

        self.assertEqual(result, [(1,)])


if __name__ == '__main__':
    unittest.main()
