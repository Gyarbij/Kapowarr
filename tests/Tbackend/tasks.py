import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from backend.base.custom_exceptions import CredentialInvalid, InvalidComicVineApiKey
from backend.base.definitions import StartType
from backend.features.tasks import (
    RefreshReleaseCache,
    RefreshReleaseDiscovery,
    SearchAll,
    TaskHandler,
    UpdateAll,
)
from frontend import api


class TaskFlowTest(unittest.TestCase):
    @staticmethod
    def _handler():
        handler = object.__new__(TaskHandler)
        handler.context = Flask('task-handler').app_context
        handler.stopping = False
        handler.task_interval_waiter = None
        handler.startup_task_waiter = None
        return handler

    @patch('backend.features.tasks.WebSocket')
    @patch('backend.features.tasks.Settings')
    def test_release_cache_refresh_skips_missing_credentials(
        self,
        settings,
        _websocket
    ):
        settings.return_value.sv.metadata_source = 'comicvine'
        with patch(
            'backend.implementations.metadata_sources.get_metadata_source',
            side_effect=InvalidComicVineApiKey
        ):
            RefreshReleaseCache().run()

    @patch('backend.features.tasks.WebSocket')
    @patch('backend.features.tasks.Settings')
    def test_release_cache_refresh_skips_invalid_metron_credentials(
        self,
        settings,
        _websocket
    ):
        settings.return_value.sv.metadata_source = 'metron'
        with patch(
            'backend.implementations.metadata_sources.get_metadata_source',
            side_effect=CredentialInvalid
        ):
            RefreshReleaseCache().run()

    def test_manual_update_all_does_not_skip_by_default(self):
        app = Flask(__name__)
        task_handler = MagicMock()
        task_handler.add.return_value = 1

        with app.test_request_context(
            '/api/system/tasks',
            method='POST',
            json={'cmd': 'update_all'}
        ), patch.object(
            api, 'TaskHandler', return_value=task_handler
        ), patch.object(
            api, 'extract_key'
        ), patch.object(
            api.StartTypeHandlers, 'diffuse_timer'
        ):
            api.api_tasks()

        queued_task = task_handler.add.call_args.args[0]
        self.assertFalse(queued_task.allow_skipping)

    def test_filtered_search_queues_selected_volume_ids(self):
        app = Flask(__name__)
        task_handler = MagicMock()
        task_handler.add.return_value = 7

        with app.test_request_context(
            '/api/system/tasks',
            method='POST',
            json={'cmd': 'search_all', 'volume_ids': [2, 5]}
        ), patch.object(
            api, 'TaskHandler', return_value=task_handler
        ), patch.object(
            api.Library, 'get_volume'
        ) as get_volume, patch.object(
            api, 'extract_key'
        ), patch.object(
            api.StartTypeHandlers, 'diffuse_timer'
        ):
            api.api_tasks()

        queued_task = task_handler.add.call_args.args[0]
        self.assertEqual(queued_task.volume_ids, [2, 5])
        self.assertEqual(
            [call.args[0] for call in get_volume.call_args_list],
            [2, 5]
        )

    def test_filtered_task_only_blocks_selected_volumes(self):
        TaskHandler.queue = [{
            'id': 8,
            'task': SearchAll(volume_ids=[2, 5]),
            'status': 'running',
            'thread': MagicMock()
        }]

        try:
            self.assertTrue(TaskHandler.task_for_volume_running(2))
            self.assertFalse(TaskHandler.task_for_volume_running(3))
        finally:
            TaskHandler.queue = []

    @patch('backend.features.tasks.WebSocket')
    @patch('backend.features.tasks.auto_search')
    @patch('backend.features.tasks.get_db')
    def test_search_all_returns_download_queue_tuples(
        self,
        get_db,
        auto_search,
        _websocket
    ):
        cursor = MagicMock()
        cursor.__iter__.return_value = iter(((1, 'First'), (2, 'Second')))
        get_db.return_value = cursor
        auto_search.side_effect = (
            [{'link': 'https://getcomics.org/first/'}],
            []
        )

        self.assertEqual(
            SearchAll().run(),
            [('https://getcomics.org/first/', 1, None)]
        )

    @patch('backend.features.tasks.WebSocket')
    def test_running_task_can_be_cancelled_without_blocking(self, _websocket):
        task = MagicMock()
        task.stop = False
        task.display_title = 'Update All'
        task.message = ''
        thread = MagicMock()
        TaskHandler.queue = [{
            'id': 41,
            'task': task,
            'status': 'running',
            'thread': thread
        }]

        try:
            TaskHandler().remove(41)

            self.assertTrue(task.stop)
            self.assertEqual(TaskHandler.queue[0]['status'], 'cancelling')
            thread.join.assert_not_called()
        finally:
            TaskHandler.queue = []

    def test_cancelling_task_that_just_finished_is_idempotent(self):
        TaskHandler.queue = []

        TaskHandler().remove(999)

    @patch('backend.features.tasks.WebSocket')
    def test_queued_task_cancellation_removes_without_joining(self, websocket):
        task = MagicMock()
        task.stop = False
        task.display_title = 'Search Missing'
        thread = MagicMock()
        TaskHandler.queue = [{
            'id': 42,
            'task': task,
            'status': 'queued',
            'thread': thread
        }]

        try:
            TaskHandler().remove(42)

            self.assertEqual(TaskHandler.queue, [])
            thread.join.assert_not_called()
            websocket.return_value.emit.assert_called_once()
        finally:
            TaskHandler.queue = []

    @patch.object(TaskHandler, '_process_queue')
    @patch('backend.features.tasks.WebSocket')
    def test_stopped_task_finalizer_advances_queue(
        self,
        websocket,
        process_queue
    ):
        stopped_task = MagicMock()
        stopped_task.stop = True
        next_task = MagicMock()
        TaskHandler.queue = [{
            'id': 43,
            'task': stopped_task,
            'status': 'cancelling',
            'thread': MagicMock()
        }, {
            'id': 44,
            'task': next_task,
            'status': 'queued',
            'thread': MagicMock()
        }]

        try:
            TaskHandler()._TaskHandler__finish_task(43, stopped_task)

            self.assertEqual(
                [entry['id'] for entry in TaskHandler.queue],
                [44]
            )
            websocket.return_value.emit.assert_called_once()
            process_queue.assert_called_once_with()
        finally:
            TaskHandler.queue = []

    @patch('backend.features.tasks.WebSocket')
    @patch('backend.features.tasks.auto_search')
    @patch('backend.features.tasks.get_db')
    def test_search_all_stops_before_next_volume(
        self,
        get_db,
        auto_search,
        _websocket
    ):
        cursor = MagicMock()
        cursor.__iter__.return_value = iter(((1, 'First'), (2, 'Second')))
        get_db.return_value = cursor
        task = SearchAll()

        def stop_after_first(*_args, **_kwargs):
            task.stop = True
            return []

        auto_search.side_effect = stop_after_first

        task.run()

        auto_search.assert_called_once_with(1, outcome=task.search_outcome)

    @patch('backend.features.tasks.WebSocket')
    @patch('backend.features.tasks.refresh_and_scan')
    def test_update_all_passes_live_stop_check(
        self,
        refresh_and_scan,
        _websocket
    ):
        task = UpdateAll()

        task.run()

        stop_check = refresh_and_scan.call_args.kwargs['stop_check']
        self.assertFalse(stop_check())
        task.stop = True
        self.assertTrue(stop_check())

    @patch('backend.features.tasks.WebSocket')
    @patch('backend.features.tasks.auto_search')
    @patch('backend.features.tasks.get_db')
    def test_discovery_promotes_only_resolved_pending_issue(
        self,
        get_db,
        auto_search,
        _websocket
    ):
        cursor = MagicMock()
        cursor.execute.return_value.__iter__.return_value = iter((
            ('lobo:6',),
        ))
        get_db.return_value = cursor
        auto_search.return_value = [{
            'link': 'https://getcomics.org/dc/lobo-6-2026/'
        }]
        discovered = [{
            'record_key': 'lobo:6',
            'local_status': 'missing_monitored',
            'local_volume_id': 3643,
            'local_issue_id': 306
        }, {
            'record_key': 'ambiguous:1',
            'local_status': 'ambiguous',
            'local_volume_id': None,
            'local_issue_id': None
        }]

        with patch(
            'backend.implementations.release_discovery.get_release_discovery',
            return_value=discovered
        ):
            result = RefreshReleaseDiscovery().run()

        auto_search.assert_called_once_with(3643, 306)
        self.assertEqual(result, [(
            'https://getcomics.org/dc/lobo-6-2026/',
            3643,
            306
        )])

    def test_disabled_intervals_do_not_arm_timer(self):
        handler = self._handler()
        cursor = MagicMock()
        cursor.execute.return_value.fetchone.return_value = (None,)

        with patch(
            'backend.features.tasks.get_db',
            return_value=cursor
        ), patch('backend.features.tasks.Timer') as timer:
            handler.handle_intervals()

        timer.assert_not_called()
        self.assertIsNone(handler.task_interval_waiter)

    def test_interval_check_skips_disabled_tasks_and_uses_skip_setting(self):
        handler = self._handler()
        handler.add = MagicMock()
        handler.handle_intervals = MagicMock()
        cursor = MagicMock()
        cursor.execute.return_value.fetchall.return_value = [{
            'task_name': 'update_all',
            'interval': 0,
            'next_run': 0
        }, {
            'task_name': 'update_all',
            'interval': 3600,
            'next_run': 0
        }]
        settings = MagicMock()
        settings.return_value.sv.scheduled_update_skip_recent = False

        with patch(
            'backend.features.tasks.get_db',
            return_value=cursor
        ), patch('backend.features.tasks.Settings', settings), patch(
            'backend.features.tasks.time',
            return_value=100
        ):
            handler._TaskHandler__check_intervals()

        handler.add.assert_called_once()
        self.assertFalse(handler.add.call_args.args[0].allow_skipping)
        handler.handle_intervals.assert_called_once_with()

    def test_startup_tasks_are_queued_in_order(self):
        handler = self._handler()
        handler.add = MagicMock()
        settings = SimpleNamespace(
            update_all_on_startup=True,
            search_all_on_startup=True,
            refresh_releases_on_startup=True,
            scheduled_update_skip_recent=False,
            startup_task_delay=0
        )
        cursor = MagicMock()

        with patch(
            'backend.features.tasks.Settings',
            return_value=SimpleNamespace(sv=settings)
        ), patch(
            'backend.features.tasks.get_task_intervals',
            return_value={
                'update_all': 3600,
                'search_all': 86_400,
                'refresh_release_cache': 3600,
                'refresh_release_discovery': 3600
            }
        ), patch(
            'backend.features.tasks.get_db',
            return_value=cursor
        ), patch('backend.features.tasks.commit'), patch(
            'backend.features.tasks.time',
            return_value=100
        ):
            handler.queue_startup_tasks(StartType.STARTUP)

        self.assertEqual(
            [type(call.args[0]) for call in handler.add.call_args_list],
            [UpdateAll, SearchAll, RefreshReleaseCache, RefreshReleaseDiscovery]
        )
        self.assertFalse(handler.add.call_args_list[0].args[0].allow_skipping)

    def test_startup_tasks_do_not_run_for_self_restart(self):
        handler = self._handler()
        handler.add = MagicMock()

        with patch('backend.features.tasks.Settings') as settings:
            handler.queue_startup_tasks(StartType.RESTART)

        settings.assert_not_called()
        handler.add.assert_not_called()

    def test_startup_task_delay_uses_timer(self):
        handler = self._handler()
        settings = SimpleNamespace(
            update_all_on_startup=True,
            search_all_on_startup=False,
            refresh_releases_on_startup=False,
            scheduled_update_skip_recent=True,
            startup_task_delay=30
        )

        with patch(
            'backend.features.tasks.Settings',
            return_value=SimpleNamespace(sv=settings)
        ), patch(
            'backend.features.tasks.get_task_intervals',
            return_value={'update_all': 3600}
        ), patch('backend.features.tasks.get_db'), patch(
            'backend.features.tasks.commit'
        ), patch('backend.features.tasks.Timer') as timer:
            handler.queue_startup_tasks(StartType.STARTUP)

        timer.assert_called_once()
        timer.return_value.start.assert_called_once_with()

    def test_stop_handle_cancels_startup_timer(self):
        handler = self._handler()
        interval_timer = MagicMock()
        startup_timer = MagicMock()
        handler.task_interval_waiter = interval_timer
        handler.startup_task_waiter = startup_timer
        TaskHandler.queue = []

        handler.stop_handle()

        interval_timer.cancel.assert_called_once_with()
        startup_timer.cancel.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
