import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientSession
from yarl import URL

from backend.base.custom_exceptions import EnqueuingDownloadFailure
from backend.base.definitions import EnqueuingDownloadFailureReason
from backend.base.helpers import AsyncSession
from backend.features.download_queue import DownloadHandler
from backend.implementations.getcomics import GetComicsPage


class DownloadQueueRateLimitTest(unittest.TestCase):
    def _handler(self):
        handler = object.__new__(DownloadHandler)
        handler.queue = []
        return handler

    def test_rate_limited_page_is_not_blocklisted(self):
        page = MagicMock()
        page.load_data = AsyncMock(side_effect=EnqueuingDownloadFailure(
            EnqueuingDownloadFailureReason.WEBPAGE_RATE_LIMITED
        ))
        handler = self._handler()

        with patch(
            'backend.features.download_queue.GetComicsPage',
            return_value=page
        ), patch(
            'backend.features.download_queue.add_to_blocklist'
        ) as add_to_blocklist:
            _added, failure = asyncio.run(handler.add(
                'https://getcomics.org/example/', 1, 2
            ))

        self.assertEqual(
            failure.value,
            'GetComics temporarily rate limited the request'
        )
        add_to_blocklist.assert_not_called()

    def test_successful_page_retry_clears_automatic_block(self):
        page = MagicMock()
        page.load_data = AsyncMock()
        page.create_downloads = AsyncMock(return_value=[])
        handler = self._handler()

        with patch(
            'backend.features.download_queue.GetComicsPage',
            return_value=page
        ), patch(
            'backend.features.download_queue.clear_automatic_blocklist',
        ) as clear_automatic_blocklist, patch.object(
            handler,
            '_DownloadHandler__prepare_downloads_for_queue',
            return_value=[]
        ), patch.object(
            handler,
            '_process_queue'
        ):
            asyncio.run(handler.add(
                'https://getcomics.org/example/', 1, 2
            ))

        clear_automatic_blocklist.assert_called_once_with(
            'https://getcomics.org/example/'
        )


class DownloadQueueRequestPacingTest(unittest.TestCase):
    def test_selected_pages_are_added_sequentially(self):
        handler = object.__new__(DownloadHandler)
        active = 0
        max_active = 0

        async def add(link, *_args):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return [link], None

        entries = (
            ('https://getcomics.org/one/', 1, 1, False),
            ('https://getcomics.org/two/', 1, 2, False),
            ('https://getcomics.org/three/', 1, 3, False)
        )

        with patch.object(handler, 'add', side_effect=add):
            result = handler.add_multiple(entries)

        self.assertEqual(max_active, 1)
        self.assertEqual(
            result,
            [
                (['https://getcomics.org/one/'], None),
                (['https://getcomics.org/two/'], None),
                (['https://getcomics.org/three/'], None)
            ]
        )


class GetComicsPageRateLimitTest(unittest.TestCase):
    def test_load_data_reports_http_429_separately(self):
        response = MagicMock(status=429, ok=False)
        session = MagicMock()
        session.get = AsyncMock(return_value=response)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=None)

        with patch(
            'backend.implementations.getcomics.AsyncSession',
            return_value=context
        ), self.assertRaises(EnqueuingDownloadFailure) as raised:
            asyncio.run(GetComicsPage(
                'https://getcomics.org/example/'
            ).load_data())

        self.assertEqual(
            raised.exception.reason.value,
            'GetComics temporarily rate limited the request'
        )


class AsyncSessionRateLimitTest(unittest.TestCase):
    def test_http_429_is_retried_before_returning(self):
        first = MagicMock(
            status=429,
            url=URL('https://getcomics.org/example/'),
            headers={},
            request_info=MagicMock(),
            history=(),
            reason='Too Many Requests'
        )
        second = MagicMock(
            status=200,
            url=URL('https://getcomics.org/example/'),
            headers={},
            reason='OK'
        )
        request = AsyncMock(side_effect=(first, second))
        sleep = AsyncMock()
        flaresolverr = MagicMock()
        flaresolverr.get_ua_cookies.return_value = ('Kapowarr', {})

        async def run_request():
            with patch(
                'backend.implementations.flaresolverr.FlareSolverr',
                return_value=flaresolverr
            ), patch.object(
                ClientSession,
                '_request',
                request
            ), patch(
                'backend.base.helpers.sleep',
                sleep
            ):
                async with AsyncSession() as session:
                    return await session._request(
                        'GET',
                        'https://getcomics.org/example/'
                    )

        result = asyncio.run(run_request())

        self.assertIs(result, second)
        self.assertEqual(request.await_count, 2)
        sleep.assert_awaited_once_with(5)


if __name__ == '__main__':
    unittest.main()
