import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from backend.implementations.metadata_cache import MetadataCache
from backend.implementations.metadata_sources.base import MetadataSourceType
from backend.internals.db import DB_SCHEMA


class FakeSource:
    def __init__(self, source_type=MetadataSourceType.COMICVINE):
        self.source_type = source_type
        self.release_calls = []
        self.publisher_calls = 0
        self.publisher_volume_calls = 0
        self.release_delay = 0
        self.fail_releases = False

    async def get_new_releases(self, start_date, end_date, limit=100):
        if self.release_delay:
            await asyncio.sleep(self.release_delay)
        if self.fail_releases:
            raise RuntimeError('source unavailable')
        self.release_calls.append((start_date, end_date, limit))
        issue_id = len(self.release_calls)
        return [{
            'issue_cv_id': issue_id,
            'volume_cv_id': issue_id,
            'volume_title': f'Volume {issue_id}',
            'issue_number': str(issue_id),
            'calculated_issue_number': float(issue_id),
            'store_date': start_date,
            'cover_date': start_date,
            'cover_url': None,
            'publisher': 'Publisher',
            'in_library': False,
            'volume_id': None
        }]

    async def get_publishers(self, limit=100):
        self.publisher_calls += 1
        return [{
            'comicvine_id': 1,
            'name': 'Publisher',
            'site_url': 'https://example.com',
            'volume_count': 1
        }]

    async def search_publisher_volumes(self, publisher_id, limit=100):
        self.publisher_volume_calls += 1
        return [{
            'comicvine_id': 1,
            'title': 'Volume 1',
            'year': 2026,
            'volume_number': 1,
            'cover_link': '',
            'cover': None,
            'description': '',
            'site_url': 'https://example.com/volume/1',
            'aliases': [],
            'publisher': 'Publisher',
            'issue_count': 1,
            'translated': False,
            'already_added': None,
            'issues': None
        }]


class MetadataCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript(DB_SCHEMA)
        self.get_db_patch = patch(
            'backend.implementations.metadata_cache.get_db',
            side_effect=self.connection.cursor
        )
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.connection.close()

    async def test_release_windows_reuse_coverage_and_fetch_only_gap(self):
        source = FakeSource()
        cache = MetadataCache(source)

        await cache.get_releases('2026-08-01', '2026-08-10')
        await cache.get_releases('2026-08-03', '2026-08-05')
        await cache.get_releases('2026-08-03', '2026-08-15')

        self.assertEqual(source.release_calls, [
            ('2026-08-01', '2026-08-10', cache.RELEASE_FETCH_LIMIT),
            ('2026-08-11', '2026-08-15', cache.RELEASE_FETCH_LIMIT)
        ])

    async def test_rejects_invalid_release_range(self):
        cache = MetadataCache(FakeSource())

        with self.assertRaises(ValueError):
            await cache.get_releases('2026-08-10', '2026-08-01')

    async def test_successful_refresh_prunes_old_cache_rows(self):
        source = FakeSource()
        cache = MetadataCache(source)
        self.connection.execute("""
            INSERT INTO metadata_response_cache(
                metadata_source, resource, cache_key,
                payload, fetched_at, expires_at
            ) VALUES ('comicvine', 'old', 'old', '[]', 0, 0);
        """)
        self.connection.commit()

        await cache.get_publishers(force_refresh=True)

        old_entry = self.connection.execute("""
            SELECT 1 FROM metadata_response_cache
            WHERE resource = 'old';
        """).fetchone()
        self.assertIsNone(old_entry)

    async def test_release_ids_are_isolated_by_source(self):
        comicvine = MetadataCache(FakeSource())
        metron = MetadataCache(FakeSource(MetadataSourceType.METRON))

        await comicvine.get_releases('2026-08-01', '2026-08-02')
        await metron.get_releases('2026-08-01', '2026-08-02')

        count = self.connection.execute(
            'SELECT COUNT(*) FROM release_cache WHERE issue_cv_id = 1;'
        ).fetchone()[0]
        self.assertEqual(count, 2)

    async def test_publishers_are_cached_and_force_refreshable(self):
        source = FakeSource()
        cache = MetadataCache(source)

        await cache.get_publishers()
        await cache.get_publishers()
        await cache.get_publishers(force_refresh=True)

        self.assertEqual(source.publisher_calls, 2)

    async def test_publisher_limits_share_one_cached_source_response(self):
        source = FakeSource()
        cache = MetadataCache(source)

        await cache.get_publishers(limit=100)
        await cache.get_publishers(limit=1000)

        self.assertEqual(source.publisher_calls, 1)

    async def test_concurrent_release_requests_are_coalesced(self):
        source = FakeSource()
        source.release_delay = 0.01
        cache = MetadataCache(source)

        await asyncio.gather(
            cache.get_releases('2026-08-01', '2026-08-10'),
            cache.get_releases('2026-08-01', '2026-08-10')
        )

        self.assertEqual(len(source.release_calls), 1)

    async def test_concurrent_force_refreshes_are_coalesced(self):
        source = FakeSource()
        source.release_delay = 0.01
        cache = MetadataCache(source)

        await asyncio.gather(
            cache.get_releases(
                '2026-08-01', '2026-08-10', force_refresh=True
            ),
            cache.get_releases(
                '2026-08-01', '2026-08-10', force_refresh=True
            )
        )

        self.assertEqual(len(source.release_calls), 1)

    async def test_stale_releases_survive_source_failure(self):
        source = FakeSource()
        cache = MetadataCache(source)
        await cache.get_releases('2026-08-01', '2026-08-10')
        self.connection.execute(
            'UPDATE release_cache_windows SET expires_at = 0;'
        )
        self.connection.commit()
        source.fail_releases = True

        releases = await cache.get_releases(
            '2026-08-01', '2026-08-10'
        )

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['volume_title'], 'Volume 1')
        self.assertEqual(releases[0]['metadata_source'], 'comicvine')

    async def test_stale_empty_release_range_survives_source_failure(self):
        source = FakeSource()

        async def empty_releases(*_args, **_kwargs):
            return []

        source.get_new_releases = empty_releases
        cache = MetadataCache(source)
        await cache.get_releases('2026-08-01', '2026-08-10')
        self.connection.execute(
            'UPDATE release_cache_windows SET expires_at = 0;'
        )
        self.connection.commit()

        async def failed_releases(*_args, **_kwargs):
            raise RuntimeError('source unavailable')

        source.get_new_releases = failed_releases
        releases = await cache.get_releases(
            '2026-08-01', '2026-08-10'
        )

        self.assertEqual(releases, [])

    async def test_library_membership_is_computed_when_cache_is_read(self):
        source = FakeSource()
        cache = MetadataCache(source)
        await cache.get_releases('2026-08-01', '2026-08-10')
        self.connection.execute(
            'INSERT INTO root_folders(id, folder) VALUES (1, ?);',
            ('/tmp/library',)
        )
        self.connection.execute("""
            INSERT INTO volumes(
                id, comicvine_id, title, root_folder
            ) VALUES (7, 1, 'Volume 1', 1);
        """)
        self.connection.commit()

        releases = await cache.get_releases(
            '2026-08-01', '2026-08-10'
        )

        self.assertTrue(releases[0]['in_library'])
        self.assertEqual(releases[0]['volume_id'], 7)

    async def test_publisher_volume_membership_updates_on_cache_hit(self):
        source = FakeSource()
        cache = MetadataCache(source)
        first_result = await cache.get_publisher_volumes(1)
        self.assertIsNone(first_result[0]['already_added'])
        self.connection.execute(
            'INSERT INTO root_folders(id, folder) VALUES (1, ?);',
            ('/tmp/library',)
        )
        self.connection.execute("""
            INSERT INTO volumes(
                id, comicvine_id, title, root_folder
            ) VALUES (7, 1, 'Volume 1', 1);
        """)
        self.connection.commit()

        second_result = await cache.get_publisher_volumes(1)

        self.assertEqual(second_result[0]['already_added'], 7)
        self.assertEqual(source.publisher_volume_calls, 1)


if __name__ == '__main__':
    unittest.main()
