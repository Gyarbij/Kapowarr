import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.implementations.comicvine import ComicVine
from backend.implementations.metadata_sources.metron_source import MetronSource


class MetadataSourcePaginationTest(unittest.IsolatedAsyncioTestCase):
    async def test_comicvine_paginates_with_offsets(self):
        source = ComicVine.__new__(ComicVine)
        first_page = [{'id': index} for index in range(100)]
        second_page = [{'id': index} for index in range(100, 150)]
        source._ComicVine__call_api = AsyncMock(side_effect=(
            {
                'results': first_page,
                'number_of_total_results': 150
            },
            {
                'results': second_page,
                'number_of_total_results': 150
            }
        ))

        results = await source._ComicVine__get_paginated_results(
            MagicMock(), '/issues', {'sort': 'store_date:desc'}, 150
        )

        self.assertEqual(len(results), 150)
        calls = source._ComicVine__call_api.await_args_list
        self.assertEqual(calls[0].args[2]['offset'], 0)
        self.assertEqual(calls[1].args[2]['offset'], 100)

    async def test_metron_follows_next_pages(self):
        source = MetronSource.__new__(MetronSource)
        first_page = [{'id': index} for index in range(100)]
        second_page = [{'id': index} for index in range(100, 150)]
        source._call_api = AsyncMock(side_effect=(
            {'results': first_page, 'next': 'page=2'},
            {'results': second_page, 'next': None}
        ))

        results = await source._get_paginated_results(
            MagicMock(), '/issue/', {}, 150
        )

        self.assertEqual(len(results), 150)
        calls = source._call_api.await_args_list
        self.assertEqual(calls[0].args[2]['page'], 1)
        self.assertEqual(calls[1].args[2]['page'], 2)


if __name__ == '__main__':
    unittest.main()
