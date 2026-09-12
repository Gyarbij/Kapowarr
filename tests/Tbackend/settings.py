import sqlite3
import unittest
from unittest.mock import patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.helpers import Singleton
from backend.internals.db import DB_SCHEMA, setup_db_adapters_and_converters
from backend.internals.settings import Settings, sync_task_intervals


class SettingsTest(unittest.TestCase):
    settings_instance_key = 'backend.internals.settings.Settings'

    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(DB_SCHEMA)
        setup_db_adapters_and_converters()
        self.cursor = self.connection.cursor()
        self.get_db_patch = patch(
            'backend.internals.settings.get_db',
            return_value=self.cursor
        )
        self.commit_patch = patch(
            'backend.internals.settings.commit',
            side_effect=self.connection.commit
        )
        self.get_db_patch.start()
        self.commit_patch.start()
        self.previous_settings = Singleton._instances.pop(
            self.settings_instance_key,
            None
        )

    def tearDown(self):
        Singleton._instances.pop(self.settings_instance_key, None)
        if self.previous_settings is not None:
            Singleton._instances[
                self.settings_instance_key
            ] = self.previous_settings
        self.commit_patch.stop()
        self.get_db_patch.stop()
        self.connection.close()

    def test_sync_inserts_configured_intervals(self):
        intervals = {
            'update_all': 3600,
            'search_all': 86_400,
            'refresh_release_cache': 3600,
            'refresh_release_discovery': 3600
        }
        with patch(
            'backend.internals.settings.get_task_intervals',
            return_value=intervals
        ), patch('backend.internals.settings.time', return_value=1000):
            sync_task_intervals()

        rows = self.cursor.execute(
            'SELECT task_name, interval, next_run FROM task_intervals '
            'ORDER BY task_name;'
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ('refresh_release_cache', 3600, 1000),
                ('refresh_release_discovery', 3600, 1000),
                ('search_all', 86_400, 1000),
                ('update_all', 3600, 1000)
            ]
        )

    def test_sync_preserves_due_time_and_disables_without_rescheduling(self):
        self.cursor.executemany(
            'INSERT INTO task_intervals VALUES (?, ?, ?);',
            (
                ('update_all', 3600, 900),
                ('search_all', 0, 777)
            )
        )
        intervals = {
            'update_all': 7200,
            'search_all': 0
        }
        with patch(
            'backend.internals.settings.get_task_intervals',
            return_value=intervals
        ), patch('backend.internals.settings.time', return_value=1000):
            sync_task_intervals()

        rows = self.cursor.execute(
            'SELECT task_name, interval, next_run FROM task_intervals '
            'ORDER BY task_name;'
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [('search_all', 0, 777), ('update_all', 7200, 900)]
        )

    def test_sync_reenables_task_from_now(self):
        self.cursor.execute(
            'INSERT INTO task_intervals VALUES (?, ?, ?);',
            ('update_all', 0, 100)
        )
        with patch(
            'backend.internals.settings.get_task_intervals',
            return_value={'update_all': 3600}
        ), patch('backend.internals.settings.time', return_value=1000):
            sync_task_intervals()

        self.assertEqual(
            tuple(self.cursor.execute(
                'SELECT interval, next_run FROM task_intervals '
                'WHERE task_name = ?;',
                ('update_all',)
            ).fetchone()),
            (3600, 4600)
        )

    def test_new_settings_are_public_and_validate_ranges(self):
        settings = Settings()
        public = settings.get_public_settings().todict()
        self.assertEqual(public['update_all_interval'], 1)
        self.assertTrue(public['scheduled_update_skip_recent'])
        self.assertEqual(public['refresh_skip_window'], 24)

        invalid_values = (
            ('update_all_interval', -1),
            ('refresh_skip_window', 0),
            ('startup_task_delay', -1)
        )
        for key, value in invalid_values:
            with self.subTest(key=key):
                with self.assertRaises(InvalidKeyValue):
                    settings.update({key: value})

    def test_interval_change_reschedules_task_handler(self):
        settings = Settings()
        with patch('backend.features.tasks.TaskHandler') as task_handler:
            settings.update({'update_all_interval': 2})

        task_handler.return_value.reschedule_intervals.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
