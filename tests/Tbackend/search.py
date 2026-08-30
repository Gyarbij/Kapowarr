import asyncio
import unittest
from unittest.mock import patch

from backend.features.search import search_multiple_queries


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


if __name__ == '__main__':
    unittest.main()
