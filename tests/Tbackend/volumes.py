import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import (
    ActivityCategory,
    ActivityEventType,
    LibraryDateFilter,
    LibraryFilter,
    LibraryStatusFilter,
    SpecialVersion,
)
from backend.implementations.volumes import (
    Issue,
    Library,
    Volume,
    determine_special_version,
    refresh_and_scan,
)


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


class VolumeSearchMatchTest(unittest.TestCase):
    @patch('backend.implementations.volumes.get_db')
    def test_search_match_serializes_and_replaces(self, get_db):
        cursor = MagicMock()
        get_db.return_value = cursor
        volume = Volume(4)
        match = volume.set_search_match({
            'source': 'getcomics',
            'source_id': 'https://getcomics.org/example/',
            'title': 'Replacement',
            'aliases': ['Replacement Comics'],
            'year': 2020,
            'volume_number': 2,
            'comicvine_id': None,
            'release_link': 'https://getcomics.org/example/',
            'display_title': 'Replacement Vol 2'
        })

        self.assertEqual(match['volume_id'], 4)
        sql, params = cursor.execute.call_args.args
        self.assertIn('ON CONFLICT(volume_id) DO UPDATE', sql)
        self.assertEqual(json.loads(params[4]), ['Replacement Comics'])

    @patch('backend.implementations.volumes.get_db')
    def test_clear_search_match_deletes_only_override(self, get_db):
        cursor = MagicMock()
        get_db.return_value = cursor
        Volume(4).clear_search_match()
        sql, params = cursor.execute.call_args.args
        self.assertIn('DELETE FROM volume_search_matches', sql)
        self.assertEqual(params, (4,))


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


class DetermineSpecialVersionTest(unittest.TestCase):
    def _determine(self, description):
        volume = MagicMock()
        volume.get_data.return_value = SimpleNamespace(
            title='Iron Fist',
            description=description
        )
        volume.get_issues.return_value = [
            SimpleNamespace(title='', date='2000-01-01')
        ]
        with patch(
            'backend.implementations.volumes.Volume',
            return_value=volume
        ):
            return determine_special_version(1)

    def test_old_single_issue_volume_is_tpb(self):
        self.assertEqual(
            self._determine('<p>A story about Danny Rand.</p>'),
            SpecialVersion.TPB
        )

    def test_annual_in_description_is_not_tpb(self):
        self.assertEqual(
            self._determine('<p>The first annual of Iron Fist.</p>'),
            SpecialVersion.NORMAL
        )


class RefreshAndScanTest(unittest.TestCase):
    OLD_FETCH = 1_000_000

    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.cursor = self.connection.cursor()
        self.cursor.executescript("""
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER NOT NULL,
                title, alt_title, year, publisher, volume_number,
                description, site_url, special_version,
                special_version_locked DEFAULT 0,
                monitor_new_issues DEFAULT 1,
                last_cv_fetch DEFAULT 0
            );
            CREATE TABLE volumes_covers(volume_id, cover);
            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                comicvine_id INTEGER NOT NULL UNIQUE,
                issue_number, calculated_issue_number, title, date,
                store_date, description, monitored
            );
        """)
        self.addCleanup(self.connection.close)

    def _refresh(self, volume_datas, issue_datas):
        """Refresh all volumes in the database, with the metadata source
        giving the supplied volumes and issues."""
        self.cursor.executemany(
            'INSERT INTO volumes(id, comicvine_id, last_cv_fetch) '
            'VALUES (?, ?, ?);',
            (
                (vd['comicvine_id'] // 10, vd['comicvine_id'], self.OLD_FETCH)
                for vd in volume_datas
            )
        )
        metadata_source = MagicMock()
        metadata_source.fetch_volumes = AsyncMock(return_value=[
            {
                'title': 'Title', 'aliases': [], 'year': 2026,
                'publisher': 'DC Comics', 'volume_number': 1,
                'description': '', 'site_url': '', 'cover': None,
                **vd
            }
            for vd in volume_datas
        ])
        metadata_source.fetch_issues = AsyncMock(return_value=[
            {
                'issue_number': '1', 'calculated_issue_number': 1.0,
                'title': None, 'date': None, 'description': '',
                **isd
            }
            for isd in issue_datas
        ])
        module = 'backend.implementations.volumes.'
        with patch(
            module + 'get_db', return_value=self.cursor
        ), patch(
            module + 'ComicVine', return_value=metadata_source
        ), patch(
            module + 'Settings'
        ) as settings, patch(
            module + '_get_activity_counts', return_value=(0, 0)
        ), patch(
            module + 'determine_special_version', return_value=None
        ), patch(
            module + 'commit'
        ), patch(
            module + 'PortablePool'
        ), patch(
            module + 'FilesDB'
        ):
            settings.return_value.sv.refresh_skip_window = 24
            refresh_and_scan(
                volume_ids=[vd['comicvine_id'] // 10 for vd in volume_datas],
                allow_skipping=False
            )

        return dict(self.cursor.execute(
            'SELECT id, last_cv_fetch > ? FROM volumes;',
            (self.OLD_FETCH,)
        ).fetchall())

    def test_volume_without_fetched_issues_is_not_marked_as_refreshed(self):
        # The rate limit was reached before the issues of volume 2 were fetched
        marked_as_refreshed = self._refresh(
            volume_datas=[
                {'comicvine_id': 10, 'issue_count': 1},
                {'comicvine_id': 20, 'issue_count': 3},
                {'comicvine_id': 30, 'issue_count': 0}
            ],
            issue_datas=[
                {'comicvine_id': 101, 'volume_id': 10}
            ]
        )

        self.assertEqual(marked_as_refreshed, {1: 1, 2: 0, 3: 1})
        self.assertEqual(
            self.cursor.execute('SELECT COUNT(*) FROM issues;').fetchone()[0],
            1
        )


if __name__ == '__main__':
    unittest.main()
