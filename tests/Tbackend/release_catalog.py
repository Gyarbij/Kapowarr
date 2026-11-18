import asyncio
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from backend.implementations import metadata_ids, release_catalog, release_discovery
from backend.internals.db import DB_SCHEMA, KapowarrCursor

MARVEL_URL = (
    'https://www.marvel.com/comics/issue/132266/x-orcists_2026_1'
)


def announced_issue(number=1, **overrides):
    return {
        'record_key': f'marvel:xorcists:{number}',
        'series_title': 'X Orcists',
        'series_year': 2026,
        'issue_number': float(number),
        'calculated_issue_number': float(number),
        'publisher': 'Marvel Comics',
        'release_date': '2026-11-18',
        'providers': ['marvel'],
        'provider': 'marvel',
        'external_id': '132266',
        'external_url': MARVEL_URL,
        'provenance': [{
            'provider': 'marvel',
            'external_id': '132266',
            'external_url': MARVEL_URL
        }],
        'local_status': 'not_in_library',
        'local_volume_id': None,
        'local_issue_id': None,
        **overrides
    }


class ReleaseCatalogQueryTest(unittest.TestCase):
    def test_title_issue_number_and_publisher_are_searchable(self):
        record = announced_issue()
        for query in ('X-Orcists', 'Xorcists #1', 'X-Orcists (2026) #1',
                      'Marvel X Orcists 1', '#1'):
            with self.subTest(query=query):
                self.assertTrue(
                    release_catalog.matches_release_query(record, query)
                )
        self.assertFalse(
            release_catalog.matches_release_query(announced_issue(10),
                                                  'X-Orcists #1')
        )
        self.assertFalse(
            release_catalog.matches_release_query(record, 'X-Orcists #2')
        )

    def test_official_issue_url_matches_without_using_it_as_a_series_id(self):
        self.assertTrue(release_catalog.matches_release_query(
            announced_issue(), MARVEL_URL + '?utm_source=test'
        ))
        self.assertFalse(release_catalog.matches_release_query(
            announced_issue(), MARVEL_URL.replace('132266', '132267')
        ))
        self.assertTrue(release_catalog.matches_release_query(
            announced_issue(), 'marvel:132266'
        ))

    def test_publisher_alias_and_pagination_preserve_the_search_scope(self):
        records = [
            announced_issue(1),
            announced_issue(2),
            announced_issue(3, publisher='DC Comics'),
            announced_issue(4, series_title='Other series')
        ]
        result = release_catalog.filter_release_catalog(
            records, query='X-Orcists', publisher='Marvel',
            sort='number-asc', page=2, per_page=1
        )

        self.assertEqual(result['total'], 2)
        self.assertEqual(result['total_pages'], 2)
        self.assertEqual(result['items'][0]['calculated_issue_number'], 2.0)
        self.assertEqual(
            {p['key']: p['issue_count'] for p in result['publishers']},
            {'marvel': 3, 'dc': 1}
        )

    def test_unknown_publisher_is_distinct_from_all_publishers(self):
        result = release_catalog.filter_release_catalog(
            [announced_issue(), announced_issue(2, publisher=None)],
            publisher='unknown'
        )

        self.assertEqual(result['total'], 1)
        self.assertIsNone(result['items'][0]['publisher'])
        self.assertIn('unknown', [p['key'] for p in result['publishers']])


class ReleaseCatalogDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.cursor = self.connection.cursor(KapowarrCursor)
        self.cursor.executescript(DB_SCHEMA)
        self.addCleanup(self.connection.close)
        db_patch = patch.object(
            release_catalog, 'get_db', return_value=self.cursor
        )
        db_patch.start()
        self.addCleanup(db_patch.stop)
        identity_db_patch = patch.object(
            metadata_ids, 'get_db', return_value=self.cursor
        )
        identity_db_patch.start()
        self.addCleanup(identity_db_patch.stop)

    def _load(self, discovered):
        with patch.object(
            release_catalog, 'get_release_discovery',
            AsyncMock(return_value=discovered)
        ):
            return asyncio.run(release_catalog.get_release_catalog(
                '2026-11-01', '2026-11-30'
            ))

    def _add_library_issue(self):
        self.cursor.executescript("""
            INSERT INTO root_folders(id, folder) VALUES (1, '/comics/');
            INSERT INTO volumes(
                id, comicvine_id, title, year, publisher, root_folder, monitored
            ) VALUES (1, 900, 'X-Orcists', 2026, 'Marvel', 1, 1);
            INSERT INTO issues(
                id, volume_id, comicvine_id, issue_number,
                calculated_issue_number, title, date, store_date, monitored
            ) VALUES (
                7, 1, 901, '1', 1.0, 'Demons beware',
                '2027-01-01', '2026-11-18', 1
            );
        """)

    def test_publisher_issue_is_found_without_a_canonical_volume(self):
        records = self._load([announced_issue()])
        result = release_catalog.filter_release_catalog(
            records, query='X-Orcists #1'
        )

        self.assertEqual(result['total'], 1)
        self.assertIsNone(result['items'][0]['local_volume_id'])
        self.assertEqual(result['items'][0]['providers'], ['marvel'])
        self.assertEqual(
            self.cursor.execute('SELECT COUNT(*) FROM volumes;').fetchone()[0],
            0
        )

    def test_library_issues_are_searchable_even_without_discovery(self):
        self._add_library_issue()

        records = self._load([])
        result = release_catalog.filter_release_catalog(
            records, query='Demons beware', publisher='Marvel Comics'
        )

        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['local_issue_id'], 7)
        self.assertEqual(result['items'][0]['release_date'], '2026-11-18')
        self.assertEqual(
            result['items'][0]['local_status'],
            'missing_monitored')

    def test_discovered_issue_and_local_issue_are_not_duplicated(self):
        self._add_library_issue()
        records = self._load([announced_issue(
            local_status='missing_monitored',
            local_volume_id=1,
            local_issue_id=7
        )])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['issue_title'], 'Demons beware')
        self.assertEqual(records[0]['providers'], ['marvel', 'library'])
        self.assertTrue(
            release_catalog.matches_release_query(records[0], 'comicvine:901')
        )

    def test_publisher_metadata_persists_without_creating_a_fake_library_entry(
        self):
        with patch.object(
            release_discovery, 'get_db', return_value=self.cursor
        ), patch.object(
            release_discovery, 'fetch_discovery_sources',
            AsyncMock(return_value={'marvel': [announced_issue()]})
        ):
            first = asyncio.run(release_catalog.get_release_catalog(
                '2026-11-01', '2026-11-30'
            ))

        with patch.object(
            release_discovery, 'get_db', return_value=self.cursor
        ), patch.object(
            release_discovery, 'fetch_discovery_sources',
            AsyncMock(return_value={})
        ):
            second = asyncio.run(release_catalog.get_release_catalog(
                '2026-11-01', '2026-11-30'
            ))

        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)
        self.assertTrue(
            release_catalog.matches_release_query(second[0], MARVEL_URL)
        )
        self.assertEqual(
            tuple(self.cursor.execute("""
                SELECT provider, external_id, release_date FROM release_discovery;
            """).fetchone()),
            ('marvel', '132266', '2026-11-18')
        )
        self.assertEqual(
            self.cursor.execute('SELECT COUNT(*) FROM volumes;').fetchone()[0],
            0
        )


if __name__ == '__main__':
    unittest.main()
