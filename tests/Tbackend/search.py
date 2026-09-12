import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.base.definitions import SpecialVersion
from backend.features.search import manual_search, search_multiple_queries


class SearchRequestPacingTest(unittest.TestCase):
    def test_query_variants_are_serialized_per_source(self):
        class SessionContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_args):
                return None

        class Source:
            active = 0
            max_active = 0

            def __init__(self, query):
                self.query = query

            async def search(self, _session):
                Source.active += 1
                Source.max_active = max(Source.max_active, Source.active)
                await asyncio.sleep(0)
                Source.active -= 1
                return []

        with patch(
            'backend.features.search.AsyncSession',
            return_value=SessionContext()
        ), patch(
            'backend.features.search.get_subclasses',
            return_value=[Source]
        ):
            asyncio.run(search_multiple_queries('one', 'two', 'three'))

        self.assertEqual(Source.max_active, 1)


class ManualSearchTest(unittest.TestCase):
    @patch('backend.features.search.Volume')
    @patch(
        'backend.features.search.search_multiple_queries',
        new_callable=AsyncMock
    )
    def test_title_fallback_reaches_long_running_series(
        self,
        search_queries,
        volume_class
    ):
        volume_data = SimpleNamespace(
            title="Archie's Double Digest Magazine",
            alt_title=None,
            publisher='Archie Comics',
            year=1984,
            volume_number=1,
            special_version=SpecialVersion.NORMAL
        )
        volume_class.return_value.get_data.return_value = volume_data
        volume_class.return_value.get_issues.return_value = [
            SimpleNamespace(
                id=number,
                calculated_issue_number=float(number),
                date='1984-01-01'
            )
            for number in range(1, 364)
        ]
        search_queries.return_value = [
            {
                'series': 'Archie Comics Double Digest',
                'year': 2025,
                'volume_number': None,
                'special_version': None,
                'issue_number': (1.0, 3.0),
                'annual': False,
                'link': 'https://getcomics.org/archie-2025/',
                'display_title': 'Archie (Comics) Double Digest 1-3 (2025)',
                'source': 'GetComics'
            },
            {
                'series': 'Archie Comics Double Digest',
                'year': 1984,
                'volume_number': None,
                'special_version': None,
                'issue_number': (1.0, 363.0),
                'annual': False,
                'link': 'https://getcomics.org/archie-1984/',
                'display_title': 'Archie Comics Double Digest 1-363 (1984)',
                'source': 'GetComics'
            }
        ]

        with patch(
            'backend.implementations.matching.blocklist_contains',
            return_value=None
        ):
            results = manual_search(18796)

        queries = search_queries.await_args.args
        self.assertTrue(any(
            query.startswith('Archie Comics Double Digest')
            for query in queries
        ))
        self.assertEqual(len({result['link'] for result in results}), 2)
        self.assertTrue(next(
            result['match']
            for result in results
            if result['link'].endswith('archie-1984/')
        ))
        self.assertEqual(
            next(
                result['match_reason_code']
                for result in results
                if result['link'].endswith('archie-2025/')
            ),
            'year_mismatch'
        )


if __name__ == '__main__':
    unittest.main()
