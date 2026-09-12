import unittest
from unittest.mock import patch

from backend.base.definitions import (
    IssueData,
    SearchResultData,
    SpecialVersion,
    VolumeData,
    VolumeSearchMatch,
)
from backend.implementations.matching import (
    check_search_result_match,
    file_importing_filter,
    match_title,
)


class SearchResultMatchingTest(unittest.TestCase):
    def setUp(self):
        self.volume = VolumeData(
            id=1,
            comicvine_id=100,
            title='Lobo',
            alt_title=None,
            year=2025,
            volume_number=1,
            description='',
            site_url='https://example.test/lobo',
            publisher='DC Comics',
            monitored=True,
            monitor_new_issues=True,
            root_folder=1,
            folder='/library/Lobo',
            custom_folder=False,
            special_version=SpecialVersion.NORMAL,
            special_version_locked=False,
            last_cv_fetch=0
        )
        self.issues = [
            IssueData(
                id=number,
                volume_id=1,
                comicvine_id=1000 + number,
                issue_number=str(number),
                calculated_issue_number=float(number),
                title=None,
                date='2026-01-01',
                description='',
                monitored=True,
                files=[]
            )
            for number in range(1, 6)
        ]
        self.number_to_year = {
            issue.calculated_issue_number: 2026
            for issue in self.issues
        }

    def _result(
        self,
        series: str = 'Lobo',
        issue_number: float = 3.0
    ) -> SearchResultData:
        return {
            'series': series,
            'year': 2026,
            'volume_number': None,
            'special_version': None,
            'issue_number': issue_number,
            'annual': False,
            'link': 'https://getcomics.org/lobo-3-2026/',
            'display_title': 'Lobo #3 (2026)',
            'source': 'GetComics'
        }

    @patch(
        'backend.implementations.matching.blocklist_contains',
        return_value=None
    )
    def test_numbered_result_uses_issue_publication_year(self, _blocklist):
        match = check_search_result_match(
            self._result(), self.volume, self.issues, self.number_to_year
        )

        self.assertTrue(match['match'])
        self.assertIsNone(match['match_issue'])
        self.assertIsNone(match['match_reason_code'])
        self.assertEqual(match['matched_issue_ids'], [3])

    @patch(
        'backend.implementations.matching.blocklist_contains',
        return_value=None
    )
    def test_search_match_alias_allows_override_identity(self, _blocklist):
        search_match: VolumeSearchMatch = {
            'volume_id': 1,
            'source': 'comicvine',
            'source_id': '4050-1',
            'title': 'Lobo Archive',
            'aliases': ['Lobo'],
            'year': 2025,
            'volume_number': 1,
            'comicvine_id': 4050,
            'release_link': 'https://comicvine.gamespot.com/lobo/',
            'display_title': 'Lobo Archive',
            'matched_at': 1
        }
        match = check_search_result_match(
            self._result(series='Lobo Archive'),
            self.volume,
            self.issues,
            self.number_to_year,
            search_match=search_match
        )
        self.assertTrue(match['match'])

    @patch(
        'backend.implementations.matching.blocklist_contains',
        return_value=None
    )
    def test_unknown_canonical_issue_is_rejected(self, _blocklist):
        match = check_search_result_match(
            self._result(issue_number=6.0),
            self.volume,
            self.issues,
            self.number_to_year
        )

        self.assertFalse(match['match'])
        self.assertEqual(match['match_reason_code'], 'issue_number_mismatch')
        self.assertEqual(match['matched_issue_ids'], [])

    @patch(
        'backend.implementations.matching.blocklist_contains',
        return_value=None
    )
    def test_same_number_from_different_title_is_rejected(self, _blocklist):
        match = check_search_result_match(
            self._result(series='Lobo: Cancellation Special'),
            self.volume,
            self.issues,
            self.number_to_year
        )

        self.assertFalse(match['match'])
        self.assertEqual(match['match_reason_code'], 'title_mismatch')

    @patch(
        'backend.implementations.matching.blocklist_contains',
        return_value=9
    )
    def test_blocklisted_result_has_stable_reason(self, _blocklist):
        match = check_search_result_match(
            self._result(), self.volume, self.issues, self.number_to_year
        )

        self.assertFalse(match['match'])
        self.assertEqual(match['match_reason_code'], 'blocklisted')

    def test_file_import_override_only_bypasses_special_version(self):
        file_data = self._result(issue_number=1.0)
        file_data['special_version'] = SpecialVersion.TPB

        self.assertFalse(file_importing_filter(
            file_data, self.volume, self.issues, self.number_to_year
        ))
        self.assertTrue(file_importing_filter(
            file_data,
            self.volume,
            self.issues,
            self.number_to_year,
            allow_special_version_mismatch=True
        ))

        file_data['year'] = 1999
        self.assertFalse(file_importing_filter(
            file_data,
            self.volume,
            self.issues,
            self.number_to_year,
            allow_special_version_mismatch=True
        ))

    def test_publication_descriptors_and_possessives_match(self):
        self.assertTrue(match_title(
            "Archie's Double Digest Magazine",
            'Archie Comics Double Digest'
        ))

    def test_pokemon_title_matching(self):
        self.assertTrue(match_title('Pokemon – Classics', 'Pokémon Classics'))
        self.assertFalse(match_title('Pokemon Classics', 'Pokemon Adventures'))


if __name__ == '__main__':
    unittest.main()
