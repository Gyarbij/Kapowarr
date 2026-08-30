import asyncio
import unittest
from json import dumps

from backend.implementations.release_discovery import (
    enrich_discovery_records,
    fetch_discovery_sources,
    merge_dated_discovery_records,
    merge_discovery_records,
)
from backend.implementations.release_discovery_sources import (
    parse_dc_catalog,
    parse_lunar_releases,
    parse_marvel_calendar,
)


class ReleaseDiscoveryParserTest(unittest.TestCase):
    def test_parses_marvel_calendar_issue_links(self):
        html = """
            <h2>ON SALE: 08/26/2026</h2>
            <a href="/comics/issue/136667/black_cat_2025_13"></a>
            <a href="/comics/issue/136667/black_cat_2025_13">
                BLACK CAT (2025) #13
            </a>
            <a href="/comics/creators/1/example">Creator</a>
        """

        records = parse_marvel_calendar(html)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['external_id'], '136667')
        self.assertEqual(records[0]['series_title'], 'BLACK CAT')
        self.assertEqual(records[0]['series_year'], 2025)
        self.assertEqual(records[0]['release_date'], '2026-08-26')

    def test_marvel_multi_week_results_keep_their_own_dates(self):
        html = """
            <div class="FeaturedGrid__Container">
                <div class="FeaturedGrid__CalendarHeader">
                    ON SALE: 08/26/2026
                </div>
                <div class="Card">
                    <a href="/comics/issue/1/alpha_2026_1">
                        ALPHA (2026) #1
                    </a>
                </div>
                <div class="FeaturedGrid__CalendarHeader">
                    ON SALE: 08/19/2026
                </div>
                <div class="Card">
                    <a href="/comics/issue/2/beta_2026_2">
                        BETA (2026) #2
                    </a>
                </div>
            </div>
        """

        records = parse_marvel_calendar(html)

        self.assertEqual(
            [record['release_date'] for record in records],
            ['2026-08-26', '2026-08-19']
        )

    def test_lunar_keeps_dc_and_deduplicates_cover_variants(self):
        response = {
            'success': True,
            'products': [
                {
                    'Code': '0626DC0018',
                    'Title': 'ABSOLUTE CATWOMAN #3 (OF 6) CVR A BENGAL',
                    'Instore': '08/26/2026',
                    'ImageUrl': 'https://media.example/a.jpg'
                },
                {
                    'Code': '0626DC0019',
                    'Title': 'ABSOLUTE CATWOMAN #3 (OF 6) CVR B EOM',
                    'Instore': '08/26/2026',
                    'ImageUrl': 'https://media.example/b.jpg'
                },
                {
                    'Code': '0626RB1087',
                    'Title': '2000 AD PROG #2495',
                    'Instore': '08/26/2026',
                    'ImageUrl': 'https://media.example/c.jpg'
                }
            ]
        }

        records = parse_lunar_releases(response)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['publisher'], 'DC Comics')
        self.assertEqual(records[0]['calculated_issue_number'], 3.0)
        self.assertEqual(records[0]['external_id'], '0626DC0018')

    def test_parses_dc_catalog_as_undated_confirmation(self):
        html = """
            <a href="/comics/absolute-catwoman-2026/3">
                <img alt="ABSOLUTE CATWOMAN (2026-) #3"
                     src="https://static.dc.com/catwoman.jpg">
            </a>
        """

        records = parse_dc_catalog(html)

        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]['release_date'])
        self.assertEqual(records[0]['series_year'], 2026)

    def test_parses_dc_structured_on_sale_records(self):
        comics = dumps([{
            '__typename': 'DC_ComicBook',
            'gepContentId': 'dc-catwoman-3',
            'title': {
                'en_US': {'full': 'ABSOLUTE CATWOMAN (2026-) #3'}
            },
            'pageAlias': {
                'pagePath': '/comics/absolute-catwoman-2026/3'
            },
            'onSaleDate': '2026-08-26T00:00:00.000Z',
            'featuredImage': {
                'imageUrl': 'https://static.dc.com/catwoman.jpg'
            }
        }])
        page_data = dumps({
            'props': {
                'pageProps': {
                    'dataByMapping': [{'value': comics}]
                }
            }
        })

        records = parse_dc_catalog(
            f'<script id="__NEXT_DATA__" type="application/json">'
            f'{page_data}</script>'
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['release_date'], '2026-08-26')
        self.assertEqual(records[0]['external_id'], 'dc-catwoman-3')
        self.assertEqual(
            records[0]['cover_url'],
            'https://static.dc.com/catwoman.jpg'
        )

    def test_undated_catalog_record_only_enriches_dated_records(self):
        records = parse_dc_catalog("""
            <a href="/comics/absolute-catwoman-2026/3">
                <img alt="ABSOLUTE CATWOMAN (2026-) #3"
                     src="https://static.dc.com/catwoman.jpg">
            </a>
        """)

        self.assertEqual(merge_dated_discovery_records(records), [])

    def test_merges_compatible_provenance_without_losing_availability(self):
        lunar = parse_lunar_releases({
            'success': True,
            'products': [{
                'Code': '0626DC0018',
                'Title': 'ABSOLUTE CATWOMAN #3 CVR A BENGAL',
                'Instore': '08/26/2026',
                'ImageUrl': 'https://media.example/a.jpg'
            }]
        })[0]
        getcomics = {
            **lunar,
            'provider': 'getcomics',
            'record_key': 'https://getcomics.org/dc/absolute-catwoman-3-2026/',
            'external_id': 'absolute-catwoman-3-2026',
            'external_url': 'https://getcomics.org/dc/absolute-catwoman-3-2026/',
            'available': True
        }

        merged = merge_discovery_records([lunar, getcomics])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['providers'], ['getcomics', 'lunar'])
        self.assertTrue(merged[0]['available'])
        self.assertEqual(
            merged[0]['download_url'],
            'https://getcomics.org/dc/absolute-catwoman-3-2026/'
        )


class ReleaseDiscoveryReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                'record_key': 'lobo-5',
                'provider': 'getcomics',
                'providers': ['getcomics'],
                'publisher': 'DC Comics',
                'series_title': 'Lobo',
                'series_year': None,
                'calculated_issue_number': 5.0,
                'issue_year': 2026,
                'release_date': '2026-07-22',
                'available': True
            },
            {
                'record_key': 'lobo-6',
                'provider': 'getcomics',
                'providers': ['getcomics'],
                'publisher': 'DC Comics',
                'series_title': 'Lobo',
                'series_year': None,
                'calculated_issue_number': 6.0,
                'issue_year': 2026,
                'release_date': '2026-08-26',
                'available': True
            }
        ]
        self.library_rows = [{
            'volume_id': 3643,
            'title': 'Lobo',
            'alt_title': None,
            'volume_year': 2026,
            'publisher': 'DC Comics',
            'volume_monitored': 1,
            'issue_id': 305,
            'calculated_issue_number': 5.0,
            'issue_date': '2026-07-22',
            'issue_monitored': 1,
            'downloaded': 0
        }]

    def test_exact_issue_and_metadata_pending_are_distinct(self):
        enriched = enrich_discovery_records(
            self.records,
            self.library_rows
        )

        self.assertEqual(enriched[0]['local_status'], 'missing_monitored')
        self.assertEqual(enriched[0]['local_issue_id'], 305)
        self.assertEqual(enriched[1]['local_status'], 'metadata_pending')
        self.assertEqual(enriched[1]['local_volume_id'], 3643)
        self.assertIsNone(enriched[1]['local_issue_id'])

    def test_existing_file_is_downloaded(self):
        self.library_rows[0]['downloaded'] = 1

        enriched = enrich_discovery_records(
            self.records[:1],
            self.library_rows
        )

        self.assertEqual(enriched[0]['local_status'], 'downloaded')

    def test_multiple_compatible_volumes_remain_ambiguous(self):
        duplicate = {
            **self.library_rows[0],
            'volume_id': 4000,
            'issue_id': 405
        }

        enriched = enrich_discovery_records(
            self.records[:1],
            [*self.library_rows, duplicate]
        )

        self.assertEqual(enriched[0]['local_status'], 'ambiguous')
        self.assertIsNone(enriched[0]['local_issue_id'])

    def test_unnumbered_special_is_review_only(self):
        special = {
            **self.records[0],
            'record_key': 'lobo-special',
            'calculated_issue_number': None
        }

        enriched = enrich_discovery_records([special], self.library_rows)

        self.assertEqual(enriched[0]['local_status'], 'metadata_pending')
        self.assertIsNone(enriched[0]['local_issue_id'])


class ReleaseDiscoveryOrchestrationTest(unittest.TestCase):
    def test_provider_failure_does_not_erase_successful_source(self):
        class Provider:
            ttl = 60

            def __init__(self, key, result=None, error=None):
                self.key = key
                self.result = result
                self.error = error

            async def fetch(self, _start_date, _end_date):
                if self.error:
                    raise self.error
                return self.result

        class Cache:
            def __init__(self, _namespace):
                pass

            async def get(
                self,
                _resource,
                _key,
                _ttl,
                fetcher,
                _force_refresh
            ):
                return await fetcher()

        records = [{'provider': 'lunar', 'record_key': 'one'}]
        result = asyncio.run(fetch_discovery_sources(
            '2026-08-01',
            '2026-08-31',
            providers=[
                Provider('marvel', error=RuntimeError('blocked')),
                Provider('lunar', result=records)
            ],
            cache_factory=Cache
        ))

        self.assertEqual(result, {'lunar': records})


if __name__ == '__main__':
    unittest.main()
