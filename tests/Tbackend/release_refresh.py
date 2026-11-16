import sqlite3
import unittest
from unittest.mock import patch

from backend.base.custom_exceptions import InvalidComicVineApiKey
from backend.implementations import release_refresh
from backend.implementations.release_refresh import (
    PENDING_RELEASE_MAX_AGE,
    RETRY_INTERVAL,
    find_changed_publisher_volumes,
    find_pending_weekly_pack_volumes,
    find_volumes_with_new_releases,
    refresh_release_volumes,
)

NOW = 2_000_000_000
HOUR = 60 * 60
DAY = 24 * HOUR


class ReleaseRefreshTestCase(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.cursor = self.connection.cursor()
        self.cursor.executescript("""
            CREATE TABLE volumes(
                id INTEGER PRIMARY KEY,
                comicvine_id INTEGER NOT NULL,
                last_cv_fetch INTEGER DEFAULT 0
            );
            CREATE TABLE issues(
                id INTEGER PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                comicvine_id INTEGER NOT NULL UNIQUE,
                store_date VARCHAR(10)
            );
            CREATE TABLE release_cache(
                metadata_source VARCHAR(30) NOT NULL,
                issue_cv_id INTEGER NOT NULL,
                volume_cv_id INTEGER NOT NULL,
                store_date VARCHAR(10),
                fetched_at INTEGER NOT NULL
            );
            CREATE TABLE pending_release_watches(
                record_key TEXT PRIMARY KEY,
                volume_id INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_checked INTEGER NOT NULL
            );
        """)
        patches = (
            patch.object(
                release_refresh, 'get_db', return_value=self.cursor
            ),
            patch.object(release_refresh, 'time', return_value=NOW)
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.connection.close)

    def add_volume(self, volume_id, refreshed_ago, issues=()):
        """Add a volume with ComicVine ID `volume_id * 10`. Each issue is a
        tuple of its ComicVine ID and store date."""
        self.cursor.execute(
            'INSERT INTO volumes VALUES (?, ?, ?);',
            (volume_id, volume_id * 10, NOW - refreshed_ago)
        )
        self.cursor.executemany(
            """
            INSERT INTO issues(volume_id, comicvine_id, store_date)
            VALUES (?, ?, ?);
            """,
            ((volume_id, *issue) for issue in issues)
        )

    def add_release(
        self,
        volume_id,
        issue_cv_id,
        store_date,
        fetched_ago,
        source='comicvine'
    ):
        self.cursor.execute(
            'INSERT INTO release_cache VALUES (?, ?, ?, ?, ?);',
            (source, issue_cv_id, volume_id * 10, store_date,
             NOW - fetched_ago)
        )

    def add_watch(self, volume_id, announced_ago):
        self.cursor.execute(
            'INSERT INTO pending_release_watches VALUES (?, ?, ?, ?);',
            (f'key:{volume_id}', volume_id, NOW - announced_ago, NOW)
        )


class ReleaseCacheTest(ReleaseRefreshTestCase):
    def test_release_missing_from_volume_is_found(self):
        self.add_volume(1, refreshed_ago=5 * HOUR, issues=[(100, '2026-11-04')])
        self.add_release(1, 101, '2026-11-18', fetched_ago=HOUR)

        self.assertEqual(find_volumes_with_new_releases(), {1})

    def test_volume_that_has_the_release_is_left_alone(self):
        self.add_volume(1, refreshed_ago=5 * HOUR, issues=[(101, '2026-11-18')])
        self.add_release(1, 101, '2026-11-18', fetched_ago=HOUR)

        self.assertEqual(find_volumes_with_new_releases(), set())

    def test_changed_store_date_is_found(self):
        self.add_volume(1, refreshed_ago=5 * HOUR, issues=[(101, None)])
        self.add_volume(2, refreshed_ago=5 * HOUR, issues=[(201, '2026-11-11')])
        self.add_release(1, 101, '2026-11-18', fetched_ago=HOUR)
        self.add_release(2, 201, '2026-11-18', fetched_ago=HOUR)

        self.assertEqual(find_volumes_with_new_releases(), {1, 2})

    def test_release_fetched_before_last_refresh_is_ignored(self):
        # Refreshed since, and still no such issue: the cache is outdated.
        # Refreshing again for it would repeat on every check.
        self.add_volume(1, refreshed_ago=HOUR)
        self.add_release(1, 101, '2026-11-18', fetched_ago=5 * HOUR)

        self.assertEqual(find_volumes_with_new_releases(), set())

    def test_refresh_right_after_fetching_release_is_not_repeated(self):
        # The fetch time of the release got rounded up to after the refresh
        self.add_volume(1, refreshed_ago=0.2)
        self.add_release(1, 101, '2026-11-18', fetched_ago=0)

        self.assertEqual(find_volumes_with_new_releases(), set())

    def test_releases_of_other_sources_and_volumes_are_ignored(self):
        self.add_volume(1, refreshed_ago=5 * HOUR)
        # IDs of Metron don't refer to the same volumes
        self.add_release(1, 101, '2026-11-18', fetched_ago=HOUR, source='metron')
        # Volume that is not in the library
        self.add_release(9, 901, '2026-11-18', fetched_ago=HOUR)

        self.assertEqual(find_volumes_with_new_releases(), set())


class PendingReleaseTest(ReleaseRefreshTestCase):
    def test_new_announcement_refreshes_right_away(self):
        self.add_volume(1, refreshed_ago=HOUR)
        self.add_watch(1, announced_ago=10 * 60)

        self.assertEqual(find_volumes_with_new_releases(), {1})

    def test_refresh_right_after_announcement_is_not_repeated(self):
        # The time of the announcement got rounded up to after the refresh
        self.add_volume(1, refreshed_ago=0.2)
        self.add_watch(1, announced_ago=0)

        self.assertEqual(find_volumes_with_new_releases(), set())

    def test_still_pending_volume_waits_for_retry_interval(self):
        self.add_volume(1, refreshed_ago=RETRY_INTERVAL - HOUR)
        self.add_volume(2, refreshed_ago=RETRY_INTERVAL + HOUR)
        self.add_watch(1, announced_ago=20 * HOUR)
        self.add_watch(2, announced_ago=20 * HOUR)

        self.assertEqual(find_volumes_with_new_releases(), {2})

    def test_older_announcement_is_retried_less_often(self):
        # Announced 8 days ago, so only retried every 2 days
        self.add_volume(1, refreshed_ago=DAY)
        self.add_volume(2, refreshed_ago=2 * DAY + HOUR)
        self.add_watch(1, announced_ago=8 * DAY)
        self.add_watch(2, announced_ago=8 * DAY)

        self.assertEqual(find_volumes_with_new_releases(), {2})

    def test_old_announcement_is_left_to_library_update(self):
        self.add_volume(1, refreshed_ago=20 * DAY)
        self.add_watch(1, announced_ago=PENDING_RELEASE_MAX_AGE + DAY)

        self.assertEqual(find_volumes_with_new_releases(), set())


class WeeklyPackTest(ReleaseRefreshTestCase):
    @staticmethod
    def pack(week_date, *items):
        return {
            'week_date': week_date,
            'items': [
                {'local_status': status, 'local_volume_id': volume_id}
                for status, volume_id in items
            ]
        }

    def test_pending_items_follow_the_same_retry_rules(self):
        self.add_volume(1, refreshed_ago=60 * HOUR)
        self.add_volume(2, refreshed_ago=30 * HOUR)
        self.add_volume(3, refreshed_ago=60 * HOUR)
        self.add_volume(4, refreshed_ago=60 * HOUR)
        packs = [
            # NOW is 2033-05-18, so this pack is a week old and volumes are
            # refreshed for it at most every 42 hours
            self.pack(
                '2033-05-11',
                ('metadata_pending', 1),
                ('metadata_pending', 2),
                ('missing_monitored', 3),
                ('not_in_library', None)
            ),
            # Too old to still refresh for
            self.pack('2033-01-05', ('metadata_pending', 4))
        ]

        self.assertEqual(find_pending_weekly_pack_volumes(packs), {1})


class PublisherVolumesTest(ReleaseRefreshTestCase):
    def test_changed_issue_count_is_found(self):
        self.add_volume(1, refreshed_ago=5 * HOUR, issues=[(100, None)])
        self.add_volume(2, refreshed_ago=5 * HOUR, issues=[(200, None)])
        self.add_volume(3, refreshed_ago=10 * 60, issues=[(300, None)])
        listing = [
            {'already_added': 1, 'issue_count': 2},
            {'already_added': 2, 'issue_count': 1},
            # Refreshed after the listing was fetched
            {'already_added': 3, 'issue_count': 2},
            {'already_added': None, 'issue_count': 12}
        ]

        self.assertEqual(
            find_changed_publisher_volumes(listing, NOW - HOUR),
            {1}
        )

    def test_listing_without_fetch_time_is_ignored(self):
        self.add_volume(1, refreshed_ago=5 * HOUR)

        self.assertEqual(
            find_changed_publisher_volumes(
                [{'already_added': 1, 'issue_count': 2}],
                None
            ),
            set()
        )


class RefreshTest(unittest.TestCase):
    @patch.object(release_refresh, 'refresh_and_scan')
    def test_refresh_ignores_skip_window(self, refresh_and_scan):
        def stop_check():
            return False

        result = refresh_release_volumes({5, 2}, stop_check)

        self.assertEqual(result, [2, 5])
        refresh_and_scan.assert_called_once_with(
            update_websocket=True,
            allow_skipping=False,
            volume_ids=[2, 5],
            stop_check=stop_check
        )

    @patch.object(release_refresh, 'refresh_and_scan')
    def test_nothing_to_refresh_does_not_contact_metadata_source(
        self,
        refresh_and_scan
    ):
        self.assertEqual(refresh_release_volumes(set()), [])
        refresh_and_scan.assert_not_called()

    @patch.object(
        release_refresh, 'refresh_and_scan',
        side_effect=InvalidComicVineApiKey
    )
    def test_missing_api_key_refreshes_nothing(self, _refresh_and_scan):
        self.assertEqual(refresh_release_volumes({1}), [])


if __name__ == '__main__':
    unittest.main()
