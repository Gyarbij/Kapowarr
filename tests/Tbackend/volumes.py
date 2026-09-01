import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import (
    ActivityCategory,
    ActivityEventType,
    LibraryDateFilter,
    LibraryFilter,
    LibraryStatusFilter,
)
from backend.implementations.volumes import Issue, Library, Volume


class ActivityMutationTest(unittest.TestCase):
    @patch('backend.implementations.volumes.record_activity')
    @patch('backend.implementations.volumes.get_db')
    def test_public_volume_update_records_changed_values(
        self,
        get_db,
        record_activity
    ):
        cursor = MagicMock()
        get_db.return_value = cursor
        volume = Volume(1)

        with patch.object(
            volume,
            'get_data',
            return_value=SimpleNamespace(monitored=True)
        ):
            volume.update({'monitored': False}, from_public=True)

        args, kwargs = record_activity.call_args
        self.assertEqual(args[0], ActivityCategory.VOLUME)
        self.assertEqual(
            args[1],
            ActivityEventType.VOLUME_MONITORING_CHANGED
        )
        self.assertEqual(
            kwargs['details']['changes']['monitored'],
            {'from': True, 'to': False}
        )
        self.assertIs(kwargs['cursor'], cursor)

    @patch('backend.implementations.volumes.record_activity')
    @patch('backend.implementations.volumes.get_db')
    def test_public_volume_update_ignores_noop(
        self,
        get_db,
        record_activity
    ):
        volume = Volume(1)

        with patch.object(
            volume,
            'get_data',
            return_value=SimpleNamespace(monitored=True)
        ):
            volume.update({'monitored': True}, from_public=True)

        get_db.assert_not_called()
        record_activity.assert_not_called()

    @patch('backend.implementations.volumes.record_activity')
    @patch('backend.implementations.volumes.get_db')
    def test_public_issue_update_records_monitoring(
        self,
        get_db,
        record_activity
    ):
        cursor = MagicMock()
        get_db.return_value = cursor
        issue = Issue(2)

        with patch.object(
            issue,
            'get_data',
            return_value=SimpleNamespace(
                monitored=True,
                issue_number='1',
                volume_id=1
            )
        ):
            issue.update({'monitored': False}, from_public=True)

        args, kwargs = record_activity.call_args
        self.assertEqual(args[0], ActivityCategory.ISSUE)
        self.assertEqual(
            args[1],
            ActivityEventType.ISSUE_MONITORING_CHANGED
        )
        self.assertEqual(kwargs['volume_id'], 1)
        self.assertEqual(kwargs['issue_id'], 2)

    @patch('backend.implementations.volumes.record_activity')
    @patch('backend.implementations.volumes.FilesDB.delete_linked_files')
    @patch('backend.implementations.volumes.get_db')
    def test_volume_delete_detaches_links_and_records_snapshot(
        self,
        get_db,
        _delete_linked_files,
        record_activity
    ):
        cursor = MagicMock()
        get_db.return_value = cursor
        volume = Volume(7)
        volume_data = SimpleNamespace(
            comicvine_id=707,
            title='Deleted Series',
            year=2020,
            folder='/library/deleted',
            root_folder=1
        )

        with patch(
            'backend.features.tasks.TaskHandler'
        ) as task_handler, patch(
            'backend.features.download_queue.DownloadHandler'
        ) as download_handler, patch.object(
            volume, 'get_data', return_value=volume_data
        ), patch.object(
            volume, 'get_issues', return_value=[object(), object()]
        ), patch.object(
            volume,
            'get_all_files',
            return_value=[{'filepath': '/library/deleted/1.cbz'}]
        ):
            task_handler.task_for_volume_running.return_value = False
            download_handler.return_value.download_for_volume_queued\
                .return_value = False
            volume.delete(delete_folder=False)

        sql_calls = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any(
            'UPDATE activity_history' in statement
            for statement in sql_calls
        ))
        cursor.__enter__.assert_called_once_with()
        cursor.__exit__.assert_called_once()
        args, kwargs = record_activity.call_args
        self.assertEqual(args[1], ActivityEventType.VOLUME_DELETED)
        self.assertNotIn('volume_id', kwargs)
        self.assertEqual(kwargs['snapshot']['volume_title'], 'Deleted Series')
        self.assertEqual(kwargs['details']['deleted_volume_id'], 7)
        self.assertEqual(kwargs['details']['issue_count'], 2)
        self.assertEqual(kwargs['details']['file_count'], 1)


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
