import unittest
from unittest.mock import patch

from backend.base.definitions import LibraryFilter
from backend.implementations.volumes import Library


class LibraryFilterTest(unittest.TestCase):
    @patch('backend.implementations.volumes.time', return_value=40_000_000)
    def test_recently_added_365_clause(self, _time):
        clause, params = Library._get_filter_clause(
            LibraryFilter.RECENTLY_ADDED_365
        )

        self.assertEqual(clause, 'WHERE created_at >= ?')
        self.assertEqual(params, [40_000_000 - 365 * 24 * 60 * 60])

    def test_recently_released_180_clause(self):
        clause, params = Library._get_filter_clause(
            LibraryFilter.RECENTLY_RELEASED_180
        )

        self.assertIn("date('now', '-180 days')", clause)
        self.assertEqual(params, [])


if __name__ == '__main__':
    unittest.main()
