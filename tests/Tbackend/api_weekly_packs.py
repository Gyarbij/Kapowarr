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


if __name__ == '__main__':
    unittest.main()