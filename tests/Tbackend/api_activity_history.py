import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from backend.base.definitions import ActivityCategory, ActivityEventType
from frontend import api


class ActivityHistoryApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.settings = MagicMock()
        self.settings.sv.api_key = 'test-key'

    def _patches(self):
        return (
            patch.object(api, 'Settings', return_value=self.settings),
            patch.object(api.StartTypeHandlers, 'diffuse_timer')
        )

    def test_get_forwards_valid_cursor_and_filters(self):
        expected = {
            'items': [],
            'next_before_id': None,
            'has_more': False
        }
        settings_patch, timer_patch = self._patches()

        with self.app.test_request_context(
            '/api/activity/history',
            query_string={
                'api_key': 'test-key',
                'before_id': '50',
                'category': 'download',
                'event_type': 'download_failed',
                'success': 'false',
                'limit': '25'
            }
        ), settings_patch, timer_patch, patch.object(
            api,
            'get_activity_history',
            return_value=expected
        ) as get_history:
            response, status = api.api_activity_history()

        self.assertEqual(status, 200)
        self.assertEqual(response['result'], expected)
        get_history.assert_called_once_with(
            before_id=50,
            volume_id=None,
            issue_id=None,
            category=ActivityCategory.DOWNLOAD,
            event_type=ActivityEventType.DOWNLOAD_FAILED,
            success=False,
            limit=25
        )

    def test_invalid_filters_are_rejected(self):
        for key, value in (
            ('category', 'unknown'),
            ('event_type', 'unknown'),
            ('success', 'maybe'),
            ('limit', '0'),
            ('limit', '101')
        ):
            with self.subTest(key=key, value=value):
                settings_patch, timer_patch = self._patches()
                with self.app.test_request_context(
                    '/api/activity/history',
                    query_string={
                        'api_key': 'test-key',
                        key: value
                    }
                ), settings_patch, timer_patch:
                    response, status = api.api_activity_history()

                self.assertEqual(status, 400)
                self.assertEqual(response['error'], 'InvalidKeyValue')

    def test_delete_clears_unified_history(self):
        settings_patch, timer_patch = self._patches()
        with self.app.test_request_context(
            '/api/activity/history?api_key=test-key',
            method='DELETE'
        ), settings_patch, timer_patch, patch.object(
            api,
            'delete_activity_history'
        ) as delete_history:
            response, status = api.api_activity_history()

        self.assertEqual(status, 200)
        self.assertEqual(response['result'], {})
        delete_history.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()