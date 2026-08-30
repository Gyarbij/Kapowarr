import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.implementations.response_cache import ResponseCache


class ResponseCacheTest(unittest.TestCase):
    def test_returns_fresh_value_without_fetching(self):
        cache = ResponseCache('test')
        cache._read_response = MagicMock(return_value={'cached': True})
        fetcher = AsyncMock()

        result = asyncio.run(cache.get('resource', 'key', 60, fetcher))

        self.assertEqual(result, {'cached': True})
        fetcher.assert_not_awaited()

    def test_uses_stale_value_after_refresh_failure(self):
        cache = ResponseCache('test-stale')
        cache._read_response = MagicMock(
            side_effect=[None, None, {'stale': True}]
        )
        fetcher = AsyncMock(side_effect=RuntimeError('offline'))

        result = asyncio.run(cache.get('resource', 'key', 60, fetcher))

        self.assertEqual(result, {'stale': True})

    @patch('backend.implementations.response_cache.time', return_value=100)
    def test_stores_successful_refresh_with_ttl(self, _time):
        cache = ResponseCache('test-store')
        cache._read_response = MagicMock(return_value=None)
        cache._store_response = MagicMock()
        fetcher = AsyncMock(return_value={'fresh': True})

        result = asyncio.run(cache.get('resource', 'key', 60, fetcher))

        self.assertEqual(result, {'fresh': True})
        cache._store_response.assert_called_once_with(
            'resource', 'key', {'fresh': True}, 100, 60
        )


if __name__ == '__main__':
    unittest.main()
