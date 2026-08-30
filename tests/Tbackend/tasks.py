import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from backend.base.custom_exceptions import (CredentialInvalid,
                                            InvalidComicVineApiKey)
from backend.features.tasks import (RefreshReleaseCache,
                                    RefreshReleaseDiscovery, SearchAll)
from frontend import api


class TaskFlowTest(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
