import sqlite3
import unittest

from backend.base.definitions import ActivityCategory, ActivityEventType
from backend.features.activity_history import (
    delete_activity_history,
    get_activity_history,
    record_activity,
)
from backend.internals.db import DB_SCHEMA


class ActivityHistoryTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(DB_SCHEMA)
        self.connection.executescript("""
            INSERT INTO root_folders(id, folder) VALUES (1, '/library');
            INSERT INTO volumes(
                id, comicvine_id, title, year, root_folder
            ) VALUES (1, 101, 'Example Series', 2026, 1);
            INSERT INTO issues(
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number, title
            ) VALUES (2, 1, 202, '1', 1.0, 'First Issue');
            INSERT INTO files(id, filepath, size)
            VALUES (3, '/library/Example Series/Issue 1.cbz', 100);
        """)

    def tearDown(self):
        self.connection.close()

    def test_records_entity_snapshots_and_details(self):
        event_id = record_activity(
            ActivityCategory.DOWNLOAD,
            ActivityEventType.DOWNLOAD_SUCCEEDED,
            'Downloaded issue #1',
            issue_id=2,
            file_id=3,
            details={'source': 'GetComics'},
            created_at=1_000,
            cursor=self.connection
        )

        page = get_activity_history(cursor=self.connection)
        event = page['items'][0]

        self.assertEqual(event_id, event['id'])
        self.assertEqual(event['volume_id'], 1)
        self.assertEqual(event['volume_comicvine_id'], 101)
        self.assertEqual(event['volume_title'], 'Example Series')
        self.assertEqual(event['issue_comicvine_id'], 202)
        self.assertEqual(event['issue_number'], '1')
        self.assertEqual(event['file_path'],
                         '/library/Example Series/Issue 1.cbz')
        self.assertEqual(event['details'], {'source': 'GetComics'})

    def test_cursor_pagination_and_filters_are_stable(self):
        for created_at, category, event_type in (
            (1_000, ActivityCategory.VOLUME,
             ActivityEventType.VOLUME_ADDED),
            (1_001, ActivityCategory.ISSUE,
             ActivityEventType.ISSUE_MONITORING_CHANGED),
            (1_002, ActivityCategory.DOWNLOAD,
             ActivityEventType.DOWNLOAD_FAILED)
        ):
            record_activity(
                category,
                event_type,
                event_type.value,
                volume_id=1,
                issue_id=2 if category != ActivityCategory.VOLUME else None,
                success=event_type != ActivityEventType.DOWNLOAD_FAILED,
                created_at=created_at,
                cursor=self.connection
            )

        first_page = get_activity_history(
            limit=2,
            cursor=self.connection
        )
        second_page = get_activity_history(
            before_id=first_page['next_before_id'],
            limit=2,
            cursor=self.connection
        )
        failed = get_activity_history(
            category=ActivityCategory.DOWNLOAD,
            success=False,
            cursor=self.connection
        )

        self.assertTrue(first_page['has_more'])
        self.assertEqual([item['id'] for item in first_page['items']], [3, 2])
        self.assertEqual([item['id'] for item in second_page['items']], [1])
        self.assertFalse(second_page['has_more'])
        self.assertEqual(len(failed['items']), 1)
        self.assertEqual(failed['items'][0]['event_type'],
                         'download_failed')

    def test_explicit_snapshot_survives_deleted_entity(self):
        self.connection.execute('DELETE FROM volumes WHERE id = 1;')

        record_activity(
            ActivityCategory.VOLUME,
            ActivityEventType.VOLUME_DELETED,
            'Deleted Example Series',
            snapshot={
                'volume_comicvine_id': 101,
                'volume_title': 'Example Series',
                'volume_year': 2026
            },
            details={'deleted_volume_id': 1},
            created_at=1_000,
            cursor=self.connection
        )

        event = get_activity_history(cursor=self.connection)['items'][0]
        self.assertIsNone(event['volume_id'])
        self.assertEqual(event['volume_title'], 'Example Series')
        self.assertEqual(event['details']['deleted_volume_id'], 1)

    def test_clear_removes_all_events(self):
        record_activity(
            ActivityCategory.VOLUME,
            ActivityEventType.VOLUME_ADDED,
            'Added Example Series',
            volume_id=1,
            cursor=self.connection
        )

        delete_activity_history(self.connection)

        page = get_activity_history(cursor=self.connection)
        self.assertEqual(page['items'], [])
        self.assertFalse(page['has_more'])


if __name__ == '__main__':
    unittest.main()