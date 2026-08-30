import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from flask import Flask

from frontend import api


class MetadataApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.source = MagicMock()
        self.cache = MagicMock()
        self.settings = MagicMock()
        self.settings.sv.api_key = 'test-key'

    def _patches(self):
        return (
            patch.object(api, 'Settings', return_value=self.settings),
            patch.object(
                api, '_get_configured_metadata_source',
                return_value=self.source
            ),
            patch.object(api, 'MetadataCache', return_value=self.cache),
            patch.object(api.StartTypeHandlers, 'diffuse_timer')
        )

    def test_custom_release_range_uses_cache_and_legacy_array_response(self):
        releases = [{'issue_cv_id': 1}]
        self.cache.get_releases = AsyncMock(return_value=releases)
        settings_patch, source_patch, cache_patch, timer_patch = \
            self._patches()

        with self.app.test_request_context(
            '/api/releases/new',
            query_string={
                'api_key': 'test-key',
                'start_date': '2026-01-01',
                'end_date': '2026-06-01',
                'limit': '250',
                'force_refresh': 'true'
            }
        ), settings_patch, source_patch, cache_patch, timer_patch:
            response, status = api.api_releases_new()

        self.assertEqual(status, 200)
        self.assertEqual(response['result'], releases)
        self.cache.get_releases.assert_awaited_once_with(
            start_date='2026-01-01',
            end_date='2026-06-01',
            limit=250,
            force_refresh=True
        )

    def test_publishers_use_cache_with_requested_limit(self):
        publishers = [{'comicvine_id': 1}]
        self.cache.get_publishers = AsyncMock(return_value=publishers)
        settings_patch, source_patch, cache_patch, timer_patch = \
            self._patches()

        with self.app.test_request_context(
            '/api/publishers',
            query_string={
                'api_key': 'test-key',
                'limit': '1000'
            }
        ), settings_patch, source_patch, cache_patch, timer_patch:
            response, status = api.api_publishers()

        self.assertEqual(status, 200)
        self.assertEqual(response['result'], publishers)
        self.cache.get_publishers.assert_awaited_once_with(
            limit=1000,
            force_refresh=False
        )

    def test_release_range_over_365_days_is_rejected(self):
        settings_patch, source_patch, cache_patch, timer_patch = \
            self._patches()

        with self.app.test_request_context(
            '/api/releases/new',
            query_string={
                'api_key': 'test-key',
                'start_date': '2025-01-01',
                'end_date': '2026-06-01'
            }
        ), settings_patch, source_patch, cache_patch, timer_patch:
            response, status = api.api_releases_new()

        self.assertEqual(status, 400)
        self.assertEqual(response['error'], 'InvalidKeyValue')


if __name__ == '__main__':
    unittest.main()
