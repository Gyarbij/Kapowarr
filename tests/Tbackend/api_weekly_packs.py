import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from flask import Flask

from frontend import api


class WeeklyPacksApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.settings = MagicMock()
        self.settings.sv.api_key = 'test-key'

    def _request(self, body):
        return self.app.test_request_context(
            '/api/weekly-packs?api_key=test-key',
            method='POST',
            json=body
        )

    def _patches(self, outcomes=None):
        return (
            patch.object(api, 'Settings', return_value=self.settings),
            patch.object(api.StartTypeHandlers, 'diffuse_timer'),
            patch.object(
                api,
                'queue_weekly_pack_items',
                AsyncMock(return_value=outcomes or [])
            )
        )

    def test_legacy_post_defaults_to_download(self):
        settings_patch, timer_patch, queue_patch = self._patches()
        body = {'record_keys': ['https://getcomics.org/example/'], 'weeks': 8}

        with self._request(body), settings_patch, timer_patch, queue_patch as queue:
            _response, status = api.api_weekly_packs()

        self.assertEqual(status, 201)
        queue.assert_awaited_once_with(
            body['record_keys'], 8, action='download'
        )

    def test_monitor_and_download_mode_is_forwarded(self):
        settings_patch, timer_patch, queue_patch = self._patches()
        body = {
            'record_keys': ['https://getcomics.org/example/'],
            'weeks': 8,
            'action': 'monitor_and_download'
        }

        with self._request(body), settings_patch, timer_patch, queue_patch as queue:
            _response, status = api.api_weekly_packs()

        self.assertEqual(status, 201)
        queue.assert_awaited_once_with(
            body['record_keys'], 8, action='monitor_and_download'
        )

    def test_unknown_action_is_rejected_before_queueing(self):
        settings_patch, timer_patch, queue_patch = self._patches()
        body = {
            'record_keys': ['https://getcomics.org/example/'],
            'action': 'import_archive'
        }

        with self._request(body), settings_patch, timer_patch, queue_patch as queue:
            response, status = api.api_weekly_packs()

        self.assertEqual(status, 400)
        self.assertEqual(response['error'], 'InvalidKeyValue')
        queue.assert_not_awaited()

    def test_viewing_packs_updates_volumes_with_pending_metadata(self):
        packs = [{
            'week_date': '2026-11-11',
            'archives': [],
            'items': [{
                'series_title': 'Lobo',
                'local_status': 'metadata_pending',
                'local_volume_id': 3
            }]
        }]
        settings_patch, timer_patch, _queue_patch = self._patches()
        task_handler = MagicMock()

        with self.app.test_request_context(
            '/api/weekly-packs?api_key=test-key'
        ), settings_patch, timer_patch, patch.object(
            api, 'get_enriched_weekly_packs', AsyncMock(return_value=packs)
        ), patch.object(
            api, 'find_pending_weekly_pack_volumes', return_value={3}
        ) as find_pending, patch.object(
            api, 'TaskHandler', return_value=task_handler
        ):
            response, status = api.api_weekly_packs()

        self.assertEqual(status, 200)
        self.assertEqual(response['result']['total_items'], 1)
        find_pending.assert_called_once_with(packs)
        task_handler.update_new_releases.assert_called_once_with({3})


if __name__ == '__main__':
    unittest.main()