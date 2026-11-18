import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from flask import Flask

from backend.implementations.metadata_sources.base import MetadataSourceType
from frontend import api
from tests.Tbackend.release_catalog import MARVEL_URL, announced_issue


class MetadataApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.source = MagicMock()
        self.source.source_type = MetadataSourceType.COMICVINE
        self.cache = MagicMock()
        self.settings = MagicMock()
        self.settings.sv.api_key = 'test-key'
        self.task_handler = MagicMock()
        task_handler_patch = patch.object(
            api, 'TaskHandler', return_value=self.task_handler
        )
        task_handler_patch.start()
        self.addCleanup(task_handler_patch.stop)

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

    def _get_releases(self):
        self.cache.get_releases = AsyncMock(return_value=[{'issue_cv_id': 1}])
        settings_patch, source_patch, cache_patch, timer_patch = \
            self._patches()

        with self.app.test_request_context(
            '/api/releases/recent',
            query_string={'api_key': 'test-key'}
        ), settings_patch, source_patch, cache_patch, timer_patch:
            return api.api_releases_recent()

    def test_serving_releases_updates_volumes_with_new_releases(self):
        _response, status = self._get_releases()

        self.assertEqual(status, 200)
        self.task_handler.update_new_releases.assert_called_once()

    def test_failing_library_update_does_not_fail_serving_releases(self):
        self.task_handler.update_new_releases.side_effect = RuntimeError

        with self.assertLogs('Kapowarr', level='ERROR'):
            response, status = self._get_releases()

        self.assertEqual(status, 200)
        self.assertEqual(response['result'], [{'issue_cv_id': 1}])

    def _get_publisher_volumes(self):
        volumes = [{'already_added': 4, 'issue_count': 12}]
        self.cache.get_publisher_volumes = AsyncMock(return_value=volumes)
        self.cache.get_fetch_time.return_value = 1234
        settings_patch, source_patch, cache_patch, timer_patch = \
            self._patches()
        find_changed_patch = patch.object(
            api, 'find_changed_publisher_volumes', return_value={4}
        )

        with self.app.test_request_context(
            '/api/publishers/31/volumes',
            query_string={'api_key': 'test-key'}
        ), settings_patch, source_patch, cache_patch, timer_patch, \
                find_changed_patch as find_changed:
            _response, status = api.api_publisher_volumes(31)

        self.assertEqual(status, 200)
        return volumes, find_changed

    def test_publisher_volumes_update_volumes_with_changed_issue_count(self):
        volumes, find_changed = self._get_publisher_volumes()

        find_changed.assert_called_once_with(volumes, 1234)
        self.cache.get_fetch_time.assert_called_once_with(
            'publisher_volumes', '31'
        )
        self.task_handler.update_new_releases.assert_called_once_with({4})

    def test_publisher_volumes_of_other_source_are_not_compared(self):
        # Its IDs don't refer to the volumes in the library
        self.source.source_type = MetadataSourceType.METRON

        _volumes, find_changed = self._get_publisher_volumes()

        find_changed.assert_not_called()
        self.task_handler.update_new_releases.assert_not_called()

    def test_issue_search_is_independent_of_the_canonical_catalogue(self):
        loader = AsyncMock(return_value=[
            announced_issue(),
            announced_issue(10),
            announced_issue(publisher='DC Comics')
        ])
        with self.app.test_request_context(
            '/api/releases/search',
            query_string={
                'api_key': 'test-key',
                'query': 'X-Orcists #1',
                'publisher': 'Marvel',
                'start_date': '2026-11-01',
                'end_date': '2026-11-30'
            }
        ), patch.object(
            api, 'Settings', return_value=self.settings
        ), patch.object(
            api.StartTypeHandlers, 'diffuse_timer'
        ), patch.object(
            api, 'get_release_catalog', loader
        ), patch.object(api, 'ComicVine') as comicvine:
            response, status = api.api_release_search()

        self.assertEqual(status, 200)
        self.assertEqual(response['result']['total'], 1)
        self.assertIsNone(response['result']['items'][0]['local_volume_id'])
        loader.assert_awaited_once_with(
            '2026-11-01', '2026-11-30', force_refresh=False
        )
        comicvine.assert_not_called()

    def test_issue_search_accepts_a_publisher_issue_url(self):
        with self.app.test_request_context(
            '/api/releases/search',
            query_string={'api_key': 'test-key', 'query': MARVEL_URL}
        ), patch.object(
            api, 'Settings', return_value=self.settings
        ), patch.object(
            api.StartTypeHandlers, 'diffuse_timer'
        ), patch.object(
            api, 'get_release_catalog',
            AsyncMock(return_value=[announced_issue()])
        ):
            response, status = api.api_release_search()

        self.assertEqual(status, 200)
        self.assertEqual(response['result']['total'], 1)

    def test_issue_search_validates_filters_before_fetching(self):
        invalid_filters = (
            {'sort': 'invalid'},
            {'local_status': 'invalid'},
            {'page': '0'},
            {'per_page': '101'},
            {'start_date': '2026-12-01', 'end_date': '2026-11-01'},
            {'query': 'https://[invalid'}
        )
        for filters in invalid_filters:
            with self.subTest(filters=filters), self.app.test_request_context(
                '/api/releases/search',
                query_string={'api_key': 'test-key', **filters}
            ), patch.object(
                api, 'Settings', return_value=self.settings
            ), patch.object(
                api.StartTypeHandlers, 'diffuse_timer'
            ), patch.object(api, 'get_release_catalog', AsyncMock()) as loader:
                _response, status = api.api_release_search()

                self.assertEqual(status, 400)
                loader.assert_not_awaited()

    def test_issue_search_requires_authentication(self):
        with self.app.test_request_context(
            '/api/releases/search?api_key=wrong-key'
        ), patch.object(
            api, 'Settings', return_value=self.settings
        ), patch.object(api, 'get_release_catalog', AsyncMock()) as loader:
            _response, status = api.api_release_search()

        self.assertEqual(status, 401)
        loader.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
