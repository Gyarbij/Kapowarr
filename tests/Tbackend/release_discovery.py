import asyncio
import sqlite3
import unittest
from datetime import date, timedelta
from html import escape
from json import dumps
from unittest.mock import patch

from aiohttp import ClientError

from backend.implementations import release_discovery
from backend.implementations.release_discovery import (
    PENDING_WATCH_RETENTION,
    SETTLED_WEEK_TTL,
    ReleaseDiscoveryProvider,
    WeeklyDiscoveryProvider,
    enrich_discovery_records,
    fetch_discovery_sources,
    merge_dated_discovery_records,
    merge_discovery_records,
    release_weeks,
)
from backend.implementations.release_discovery_sources import (
    parse_comixnow_weekly,
    parse_dc_catalog,
    parse_locg_releases,
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

    def test_marvel_embedded_data_gives_date_per_release(self):
        releases = {
            'total': 4,
            'content': [
                {
                    'headline': 'Black Panther/Namor: Doomed (2026) #3',
                    'isVariant': 0,
                    'releaseDate': '2026-11-18',
                    'image': {'filename': 'https://cdn.example/3.jpg'},
                    'link': {'link': 'https://www.marvel.com/comics/issue/135820/black_panthernamor_doomed_2026_3'}
                },
                {
                    'headline': 'Black Panther/Namor: Doomed (2026) #3',
                    'isVariant': 1,
                    'releaseDate': '2026-11-18',
                    'link': {'link': 'https://www.marvel.com/comics/issue/135821/black_panthernamor_doomed_2026_3_variant'}
                },
                {
                    'headline': 'Marvel Spotlight #32 Facsimile Edition (2026) #32',
                    'isVariant': 0,
                    'releaseDate': '2026-11-18',
                    'link': {'link': 'https://www.marvel.com/comics/issue/139287/marvel_spotlight_32_facsimile_edition_2026_32'}
                },
                {
                    'headline': 'Punisher (2026) #11',
                    'isVariant': 0,
                    'releaseDate': '2026-12-16',
                    'link': {'link': 'https://www.marvel.com/comics/issue/133290/punisher_2026_11'}
                }
            ]
        }
        html = (
            "<script>window['__CONFIG__']={\"page\":{\"allComicsReleases\":"
            + dumps(releases)
            + '},"viewport":{}};</script>'
        )

        records = parse_marvel_calendar(html)

        self.assertEqual(
            [
                (
                    record['external_id'],
                    record['series_title'],
                    record['series_year'],
                    record['calculated_issue_number'],
                    record['release_date']
                )
                for record in records
            ],
            [
                # The part of the title before the slash is kept, and the
                # variant cover and the reprint are left out
                ('135820', 'Black Panther Namor: Doomed', 2026, 3.0, '2026-11-18'),
                ('133290', 'Punisher', 2026, 11.0, '2026-12-16')
            ]
        )
        self.assertEqual(records[0]['cover_url'], 'https://cdn.example/3.jpg')

    def test_marvel_release_in_unexpected_shape_does_not_lose_the_others(self):
        releases = {
            'total': '2',
            'content': [
                {
                    'headline': 'Dnx (2026) #5',
                    'releaseDate': '2026-11-18',
                    'image': 'https://cdn.example/5.jpg',
                    'link': 'https://www.marvel.com/comics/issue/133252/dnx_2026_5'
                },
                'not a release',
                {
                    'headline': 'Punisher (2026) #10',
                    'releaseDate': '2026-11-18',
                    'image': ['https://cdn.example/10.jpg'],
                    'link': {'link': 'https://www.marvel.com/comics/issue/133289/punisher_2026_10'}
                }
            ]
        }

        records = parse_marvel_calendar(
            '<script>{"allComicsReleases":' + dumps(releases) + '}</script>'
        )

        self.assertEqual(
            [(record['series_title'], record['cover_url']) for record in records],
            [('Punisher', None)]
        )

    def test_lunar_gives_publisher_and_leaves_out_collectibles(self):
        response = {
            'success': True,
            'products': [
                {'Code': '1026IM0101', 'Title': 'TERMINAL #7 CVR A PORTACIO', 'Instore': '12/02/2026'},
                {'Code': '1026DE0202', 'Title': 'BEN 10 #7 CVR A ROBERT CAREY', 'Instore': '12/02/2026'},
                # A signed copy of an issue of another publisher
                {'Code': '1026DE0303', 'Title': 'DF ABSOLUTE BATMAN #15 COVER A CGC GRADED', 'Instore': '12/02/2026'},
                {'Code': '1026IM0404', 'Title': 'TERMINAL #1 2ND PTG', 'Instore': '12/02/2026'},
                # A code that multiple publishers share
                {'Code': '1026MP0505', 'Title': 'CROW FLESH AND BLOOD #3 CVR A', 'Instore': '12/02/2026'},
                # Only a variant cover of an issue that came out before
                {'Code': '0926DE8435', 'Title': 'BEN 10 #1 CVR ZK LUCAS LEE DYNAMITE COM EXCLUSIVE VAR', 'Instore': '12/02/2026'}
            ]
        }

        records = parse_lunar_releases(response)

        self.assertEqual(
            [
                (record['publisher'], record['series_title'])
                for record in records
            ],
            [
                ('Dynamite Entertainment', 'BEN 10'),
                ('Image Comics', 'TERMINAL')
            ]
        )

    def test_parses_comixnow_new_issues_with_series_year(self):
        html = """
            <title>DC Weekly — November 25, 2026 | ComixNOW!</title>
            <h1>DC Weekly</h1>
            <article class="release-card">
                <a class="release-cover-link" href="/comics/batman-superman-world-s-finest-2022/batman-superman-world-s-finest-57/">
                    <img class="release-cover" src="https://cdn2.comixnow.com/57.jpg">
                </a>
                <span class="release-kind">Ongoing</span>
                <h3 class="release-title">
                    <a href="/comics/batman-superman-world-s-finest-2022/batman-superman-world-s-finest-57/">
                        BATMAN/SUPERMAN: WORLD’S FINEST #57
                    </a>
                </h3>
            </article>
            <article class="release-card">
                <span class="release-kind">Facsimile</span>
                <h3 class="release-title">
                    <a href="/comics/detective-comics-1937/detective-comics-168-facsimile/">
                        DETECTIVE COMICS #168 FACSIMILE EDITION
                    </a>
                </h3>
            </article>
            <article class="release-card">
                <span class="release-kind">Book</span>
                <h3 class="release-title">
                    <a href="/books/9781799509653">JSA COMPENDIUM TWO - TP</a>
                </h3>
            </article>
        """

        records = parse_comixnow_weekly(html)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['provider'], 'comixnow')
        self.assertEqual(records[0]['publisher'], 'DC Comics')
        self.assertEqual(
            records[0]['series_title'], "BATMAN SUPERMAN: WORLD'S FINEST"
        )
        # The year in the link is of the series according to DC, which isn't
        # the year of the volume for every series
        self.assertIsNone(records[0]['series_year'])
        self.assertEqual(records[0]['calculated_issue_number'], 57.0)
        # From the page, as the page of another week can be given
        self.assertEqual(records[0]['release_date'], '2026-11-25')
        self.assertEqual(
            records[0]['cover_url'], 'https://cdn2.comixnow.com/57.jpg'
        )

    LOCG_LIST = """
        <li class="issue" data-comic="1096934" data-parent="0">
            <div class="cover">
                <a href="/comic/1096934/absolute-flash-21">
                    <img class="lazy" src="data:image/gif;base64,R0lGOD"
                        data-src="https://s3.amazonaws.com/covers/1096934.jpg">
                </a>
            </div>
            <div class="publisher color-offset"> DC Comics </div>
            <div class="title color-primary">
                <a href="/comic/1096934/absolute-flash-21">Absolute Flash #21</a>
            </div>
            <div class="details">
                <span class="date" data-date="1794978000"> Nov 18th, 2026 </span>
            </div>
        </li>
        <li class="issue" data-comic="1096935" data-parent="1096934">
            <div class="publisher"> DC Comics </div>
            <div class="title">
                <a href="/comic/1096935/absolute-flash-21-b">Absolute Flash #21  Cover B Variant</a>
            </div>
            <div class="details">
                <span class="date" data-date="1794978000"> Nov 18th, 2026 </span>
            </div>
        </li>
    """

    def test_parses_league_of_comic_geeks_releases(self):
        records = parse_locg_releases(dumps({
            'count': 2,
            'list': self.LOCG_LIST
        }))

        # The variant cover is left out
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['provider'], 'locg')
        self.assertEqual(records[0]['external_id'], '1096934')
        self.assertEqual(records[0]['publisher'], 'DC Comics')
        self.assertEqual(records[0]['series_title'], 'Absolute Flash')
        self.assertEqual(records[0]['calculated_issue_number'], 21.0)
        self.assertEqual(records[0]['release_date'], '2026-11-18')
        self.assertEqual(
            records[0]['external_url'],
            'https://leagueofcomicgeeks.com/comic/1096934/absolute-flash-21'
        )
        self.assertEqual(
            records[0]['cover_url'],
            'https://s3.amazonaws.com/covers/1096934.jpg'
        )

    def test_league_of_comic_geeks_response_through_browser(self):
        # A browser, so FlareSolverr too, puts the response inside of a page
        page = (
            '<html><head></head><body><pre>'
            + escape(dumps({'count': 2, 'list': self.LOCG_LIST}))
            + '</pre></body></html>'
        )

        self.assertEqual(
            parse_locg_releases(page),
            parse_locg_releases(dumps({'count': 2, 'list': self.LOCG_LIST}))
        )

    def test_league_of_comic_geeks_block_page_is_not_an_empty_week(self):
        with self.assertRaises(ValueError):
            parse_locg_releases(
                '<html><head><title>Just a moment...</title></head>'
                '<body>Enable JavaScript and cookies to continue</body></html>'
            )
        self.assertEqual(
            parse_locg_releases(dumps({'count': 0, 'list': ''})),
            []
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

    def test_publisher_is_recognised_however_a_source_writes_it(self):
        library_rows = [{
            **self.library_rows[0],
            'publisher': 'Dynamite Entertainment',
            'alt_title': 'The Main Man'
        }]
        records = [
            # Written differently by the source, and matching the other title
            {**self.records[0], 'publisher': 'Dynamite', 'series_title': 'MAIN MAN'},
            {**self.records[0], 'publisher': 'Dynamic Forces'}
        ]

        enriched = enrich_discovery_records(records, library_rows)

        self.assertEqual(
            [record['local_status'] for record in enriched],
            ['missing_monitored', 'not_in_library']
        )

    def test_sources_writing_a_publisher_differently_are_merged(self):
        merged = merge_discovery_records([
            {
                **self.records[0],
                'provider': 'lunar',
                'publisher': 'BOOM! Studios'
            },
            {
                **self.records[0],
                'provider': 'locg',
                'publisher': 'Boom Studios',
                'series_year': 2026
            }
        ])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['providers'], ['locg', 'lunar'])
        self.assertEqual(merged[0]['series_year'], 2026)

    def test_date_of_the_most_trusted_source_is_used(self):
        def release(provider, release_date):
            return {
                **self.records[0],
                'provider': provider,
                'release_date': release_date
            }

        merged = merge_discovery_records([
            # Made available a week after it came out
            release('getcomics', '2026-07-29'),
            release('lunar', '2026-07-22'),
            # A variant cover that came out later
            release('lunar', '2026-08-05'),
            release('locg', '2026-07-21')
        ])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['release_date'], '2026-07-22')
        self.assertEqual(merged[0]['release_date_provider'], 'lunar')
        self.assertTrue(merged[0]['available'])


class PendingReleaseWatchTest(unittest.TestCase):
    NOW = 2_000_000_000

    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.cursor = self.connection.cursor()
        self.cursor.execute("""
            CREATE TABLE pending_release_watches(
                record_key TEXT PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_checked INTEGER NOT NULL,
                release_date VARCHAR(10)
            );
        """)
        self.cursor.executemany(
            'INSERT INTO pending_release_watches VALUES (?, ?, ?, ?, ?);',
            (
                # Pending since a day, and then for another date
                ('lobo:7', 3, self.NOW - 86400, self.NOW - 3600, '2033-06-01'),
                ('lobo:6', 3, self.NOW - 86400, self.NOW - 3600, '2033-05-11'),
                # Not among the releases anymore since a long time
                ('gone:1', 4, 1000, self.NOW - PENDING_WATCH_RETENTION - 60, None),
                # Not among the releases that are looked at this time
                ('other:1', 5, 1000, self.NOW - 3600, None)
            )
        )
        self.addCleanup(self.connection.close)

    def test_pending_releases_are_kept_with_the_date_they_come_out(self):
        records = [
            {
                'record_key': 'lobo:7', 'local_status': 'metadata_pending',
                'local_volume_id': 3, 'release_date': '2033-06-08'
            },
            {
                'record_key': 'lobo:8', 'local_status': 'metadata_pending',
                'local_volume_id': 3, 'release_date': '2033-07-06'
            },
            # The issue is in the metadata by now
            {
                'record_key': 'lobo:6', 'local_status': 'missing_monitored',
                'local_volume_id': 3, 'release_date': '2033-05-11'
            }
        ]

        with patch.object(
            release_discovery, 'get_db', return_value=self.cursor
        ), patch.object(release_discovery, 'time', return_value=self.NOW):
            release_discovery._update_pending_watches(records)

        self.assertEqual(
            self.cursor.execute("""
                SELECT record_key, first_seen, release_date
                FROM pending_release_watches
                ORDER BY record_key;
            """).fetchall(),
            [
                # Rescheduled, and still pending since the same moment
                ('lobo:7', self.NOW - 86400, '2033-06-08'),
                ('lobo:8', self.NOW, '2033-07-06'),
                ('other:1', 1000, None)
            ]
        )


class ReleaseDiscoveryOrchestrationTest(unittest.TestCase):
    class Provider(ReleaseDiscoveryProvider):
        ttl = 60

        def __init__(self, key, result=None, error=None, available=True):
            self.key = key
            self.result = result
            self.error = error
            self.available = available

        def is_available(self):
            return self.available

        async def fetch(self, _start_date, _end_date):
            if self.error:
                raise self.error
            return self.result

    class WeeklyProvider(WeeklyDiscoveryProvider):
        key = 'weekly'
        ttl = 60

        def __init__(self, failing_weeks=(), error=RuntimeError, empty=False):
            self.failing_weeks = failing_weeks
            self.error = error
            self.empty = empty
            self.fetched_weeks = []

        async def fetch_week(self, week_start, week_end):
            self.fetched_weeks.append(week_start.isoformat())
            if week_start.isoformat() in self.failing_weeks:
                raise self.error('blocked')
            if self.empty:
                return []
            return [{
                'provider': self.key,
                'record_key': f'{week_start}..{week_end}'
            }]

    class Cache:
        "Remembers what was fetched, like the real one within its TTL"
        responses: dict = {}

        def __init__(self, namespace):
            self.namespace = namespace
            self.requests = []
            ReleaseDiscoveryOrchestrationTest.caches.append(self)

        async def get(self, resource, key, ttl, fetcher, force_refresh):
            cache_key = (self.namespace, resource, key)
            if force_refresh or cache_key not in self.responses:
                self.responses[cache_key] = await fetcher()
            response = self.responses[cache_key]
            self.requests.append((
                resource, key,
                ttl(response) if callable(ttl) else ttl,
                force_refresh
            ))
            return response

    caches: list = []

    def setUp(self):
        self.caches.clear()
        self.Cache.responses = {}

    def _fetch(self, providers, start='2026-08-01', end='2026-08-31', **kwargs):
        return asyncio.run(fetch_discovery_sources(
            start,
            end,
            providers=providers,
            cache_factory=self.Cache,
            **kwargs
        ))

    def test_provider_failure_does_not_erase_successful_source(self):
        records = [{'provider': 'lunar', 'record_key': 'one'}]

        result = self._fetch([
            self.Provider('marvel', error=RuntimeError('blocked')),
            self.Provider('lunar', result=records)
        ])

        self.assertEqual(result, {'lunar': records})

    def test_weeks_run_from_sunday_and_cover_the_whole_range(self):
        self.assertEqual(
            [
                (start.isoformat(), end.isoformat())
                for start, end in release_weeks('2026-11-18', '2026-12-01')
            ],
            [
                ('2026-11-15', '2026-11-21'),
                ('2026-11-22', '2026-11-28'),
                ('2026-11-29', '2026-12-05')
            ]
        )

    def test_weekly_provider_only_fetches_weeks_that_are_not_cached(self):
        provider = self.WeeklyProvider()

        # The range moves up a week, like it does as time passes
        first = self._fetch([provider], '2026-08-02', '2026-08-15')
        second = self._fetch([provider], '2026-08-09', '2026-08-22')

        self.assertEqual(
            provider.fetched_weeks,
            ['2026-08-02', '2026-08-09', '2026-08-16']
        )
        self.assertEqual(
            [record['record_key'] for record in first['weekly']],
            ['2026-08-02..2026-08-08', '2026-08-09..2026-08-15']
        )
        self.assertEqual(len(second['weekly']), 2)

    def test_settled_weeks_are_kept_longer_and_not_refetched_on_demand(self):
        today = date.today()
        provider = self.WeeklyProvider()

        self._fetch(
            [provider],
            (today - timedelta(days=40)).isoformat(),
            (today + timedelta(days=10)).isoformat(),
            force_refresh=True
        )

        requests = self.caches[0].requests
        past = [r for r in requests if r[1] < (today - timedelta(days=28)).isoformat()]
        upcoming = [r for r in requests if r[1] >= today.isoformat()]
        self.assertTrue(past and upcoming)
        self.assertTrue(all(
            (ttl, forced) == (SETTLED_WEEK_TTL, False)
            for _, _, ttl, forced in past
        ))
        self.assertTrue(all(
            (ttl, forced) == (provider.ttl, True)
            for _, _, ttl, forced in upcoming
        ))

    def test_past_week_without_releases_is_not_kept_for_long(self):
        today = date.today()
        provider = self.WeeklyProvider(empty=True)

        self._fetch(
            [provider],
            (today - timedelta(days=40)).isoformat(),
            (today - timedelta(days=30)).isoformat()
        )

        # The source probably didn't give the releases, so it's asked again
        self.assertTrue(self.caches[0].requests)
        self.assertTrue(all(
            ttl == provider.ttl
            for _, _, ttl, _ in self.caches[0].requests
        ))

    def test_unreachable_source_is_not_tried_for_every_week(self):
        provider = self.WeeklyProvider(
            failing_weeks=('2026-08-02', '2026-08-09', '2026-08-16'),
            error=ClientError
        )
        provider.concurrency = 1

        result = self._fetch([provider], '2026-08-02', '2026-10-31')

        self.assertEqual(result, {})
        self.assertEqual(provider.fetched_weeks, ['2026-08-02'])

    def test_failing_week_does_not_erase_the_other_weeks(self):
        with self.assertLogs('Kapowarr', level='WARNING'):
            result = self._fetch(
                [self.WeeklyProvider(failing_weeks=('2026-08-09',))],
                '2026-08-02', '2026-08-22'
            )

        self.assertEqual(
            [record['record_key'] for record in result['weekly']],
            ['2026-08-02..2026-08-08', '2026-08-16..2026-08-22']
        )
        # A provider of which every week fails, has failed
        self.assertEqual(
            self._fetch(
                [self.WeeklyProvider(failing_weeks=('2026-09-06',))],
                '2026-09-06', '2026-09-12'
            ),
            {}
        )

    def test_slow_and_unavailable_providers_are_left_out(self):
        slow = self.Provider('slow', result=[{'record_key': 'slow'}])
        slow.background_only = True
        providers = [
            slow,
            self.Provider('fast', result=[{'record_key': 'fast'}]),
            self.Provider('unset', result=[], available=False)
        ]

        # While a request waits for it
        self.assertEqual(list(self._fetch(providers)), ['fast'])
        # By the release task
        self.assertEqual(
            list(self._fetch(providers, background=True)),
            ['slow', 'fast']
        )


if __name__ == '__main__':
    unittest.main()
