import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from backend.base.custom_exceptions import (InvalidComicVineApiKey,
                                            InvalidKeyValue, VolumeNotMatched)
from backend.base.definitions import MonitorScheme, SpecialVersion
from backend.implementations import metadata_ids, release_refresh, volumes
from backend.implementations.release_discovery import enrich_discovery_records
from backend.internals.db import DB_SCHEMA, KapowarrCursor


def issue_metadata(cv_id=None, number=1, **overrides):
    return {
        'comicvine_id': cv_id, 'volume_id': 500 if cv_id else None,
        'issue_number': str(number), 'calculated_issue_number': float(number),
        'title': 'Confirmed title' if cv_id else None,
        'date': '2026-11-18', 'store_date': '2026-11-18',
        'description': '',
        **overrides
    }


def series_metadata(cv_id=500, **overrides):
    return {
        'comicvine_id': cv_id, 'title': 'X-Orcists', 'year': 2026,
        'publisher': 'Marvel', 'aliases': [], 'volume_number': 1,
        'cover': None, 'cover_link': '',
        'site_url': f'https://comicvine.gamespot.com/volume/4050-{cv_id}/',
        'description': 'Canonical metadata', 'issue_count': 1,
        'issues': [issue_metadata(700)], 'translated': False,
        'already_added': None, **overrides
    }


class MetadataIdentityTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.cursor = self.connection.cursor(KapowarrCursor)
        self.cursor.executescript(DB_SCHEMA)
        self.cursor.execute(
            "INSERT INTO root_folders(id, folder) VALUES (1, '/comics/');"
        )
        self.cursor.execute("""
            INSERT INTO volumes(
                id, title, year, publisher, root_folder, folder,
                monitored, monitor_new_issues
            ) VALUES (1, 'X Orcists', 2026, 'Marvel Comics', 1, '/comics/kept', 0, 0);
        """)
        self.connection.commit()
        self.addCleanup(self.connection.close)
        for module in (metadata_ids, volumes):
            db_patch = patch.object(module, 'get_db', return_value=self.cursor)
            db_patch.start()
            self.addCleanup(db_patch.stop)
        self.identity = {
            'source': 'marvel',
            'source_id': '132266',
            'source_url': 'https://www.marvel.com/comics/issue/132266/x-orcists_2026_1'}

    def _publisher_issue(self, number=1):
        return metadata_ids.upsert_issue_metadata(
            1, issue_metadata(
                number=number, source_ids=[{
                    **self.identity,
                    'source_id': str(132265 + number)
                }]
            ), monitored=False
        )

    def _client(self, candidates=None, metadata=None):
        class Client(metadata_ids.ComicVine):
            def __init__(self):
                self.search_mock = AsyncMock()
                self.fetch_mock = AsyncMock()
                self.fetch_volumes_mock = AsyncMock()
                self.fetch_issues_mock = AsyncMock()

            async def search_volumes(
                self, query, allow_rate_limit_reached=False):
                return await self.search_mock(query)

            async def fetch_volume(self, source_id):
                return await self.fetch_mock(source_id)

            async def fetch_volumes(self, source_ids):
                return await self.fetch_volumes_mock(source_ids)

            async def fetch_issues(self, source_ids):
                return await self.fetch_issues_mock(source_ids)

        client = Client()
        client.search_mock.return_value = (
            candidates if candidates is not None else [series_metadata()]
        )
        client.fetch_mock.return_value = metadata or series_metadata()
        return client

    def test_numeric_publisher_id_does_not_collide_with_comicvine(self):
        issue_id = self._publisher_issue()
        self.cursor.execute("""
            INSERT INTO volumes(id, comicvine_id, title, root_folder)
            VALUES (2, 132266, 'Another series', 1);
        """)
        other_issue_id = self.cursor.execute("""
            INSERT INTO issues(
                volume_id, comicvine_id, issue_number, calculated_issue_number
            ) VALUES (2, 132266, '1', 1.0);
        """).lastrowid
        metadata_ids.bind_source_id(
            'issue', other_issue_id, metadata_ids.comicvine_identity(132266)
        )
        self.assertIsNone(self.cursor.execute(
            'SELECT comicvine_id FROM issues WHERE id = ?;', (issue_id,)
        ).fetchone()[0])
        self.assertEqual(self.cursor.execute(
            "SELECT issue_id FROM issue_source_ids WHERE source='marvel' AND source_id='132266';"
        ).fetchone()[0], issue_id)

    def test_reconciliation_preserves_local_ids_files_and_monitoring(self):
        issue_id = self._publisher_issue()
        self.cursor.execute(
            "INSERT INTO files(id, filepath, size) VALUES (1, '/comics/kept/1.cbz', 10);"
        )
        self.cursor.execute(
            'INSERT INTO issues_files(issue_id, file_id) VALUES (?, 1);', (issue_id,))
        self.cursor.execute("""
            INSERT INTO activity_history(
                created_at, category, event_type, summary, volume_id, issue_id
            ) VALUES (1000, 'file', 'file_matched', 'Kept history', 1, ?);
        """, (issue_id,))
        self.connection.commit()

        first = metadata_ids.reconcile_comicvine(1, client=self._client())
        second = metadata_ids.reconcile_comicvine(1, client=self._client())

        self.assertEqual(
            (first['status'], second['status']), ('linked', 'linked'))
        self.assertEqual(tuple(self.cursor.execute(
            'SELECT id, comicvine_id, folder, monitored FROM volumes;'
        ).fetchone()), (1, 500, '/comics/kept', 0))
        self.assertEqual(tuple(self.cursor.execute(
            'SELECT id, comicvine_id, monitored FROM issues;'
        ).fetchone()), (issue_id, 700, 0))
        self.assertEqual(self.cursor.execute(
            'SELECT issue_id FROM issues_files;'
        ).fetchone()[0], issue_id)
        self.assertEqual(self.cursor.execute(
            "SELECT issue_id FROM activity_history WHERE summary = 'Kept history';"
        ).fetchone()[0], issue_id)
        self.assertEqual(self.cursor.execute("""
            SELECT COUNT(*) FROM activity_history
            WHERE summary = 'Linked ComicVine and publisher metadata';
        """).fetchone()[0], 1)
        self.assertEqual({
            tuple(row) for row in self.cursor.execute(
                'SELECT source, source_id FROM issue_source_ids;'
            )
        }, {('marvel', '132266'), ('comicvine', '700')})

    def test_ambiguous_series_are_not_automatically_linked(self):
        self._publisher_issue()
        client = self._client(candidates=[
            series_metadata(500), series_metadata(501)
        ])

        result = metadata_ids.reconcile_comicvine(1, client=client)

        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(self.cursor.execute(
            'SELECT comicvine_id FROM volumes WHERE id = 1;'
        ).fetchone()[0])
        client.fetch_mock.assert_not_awaited()

    def test_existing_library_owner_is_reported_as_a_conflict(self):
        self._publisher_issue()
        self.cursor.execute("""
            INSERT INTO volumes(id, comicvine_id, title, root_folder)
            VALUES (2, 500, 'Existing owner', 1);
        """)
        self.connection.commit()

        result = metadata_ids.reconcile_comicvine(1, client=self._client())

        self.assertEqual(result['status'], 'conflict')
        self.assertIsNone(self.cursor.execute(
            'SELECT comicvine_id FROM volumes WHERE id = 1;'
        ).fetchone()[0])

    def test_old_issue_with_the_same_number_is_not_a_match(self):
        self._publisher_issue()
        result = metadata_ids.reconcile_comicvine(1, client=self._client(
            metadata=series_metadata(issues=[
                issue_metadata(700, date='2000-01-01', store_date='2000-01-01')
            ])
        ))
        self.assertEqual(result['status'], 'ambiguous')

    def test_publisher_issue_not_yet_on_comicvine_is_retained(self):
        first_id = self._publisher_issue()
        second_id = self._publisher_issue(2)
        result = metadata_ids.reconcile_comicvine(1, client=self._client())
        self.assertEqual(result['status'], 'linked')
        self.assertEqual([
            tuple(row) for row in self.cursor.execute(
                'SELECT id, comicvine_id FROM issues ORDER BY id;'
            )
        ], [(first_id, 700), (second_id, None)])

    def test_a_source_id_cannot_be_reassigned_to_another_local_issue(self):
        first_id = self._publisher_issue()
        second_id = self._publisher_issue(2)
        with self.assertRaises(InvalidKeyValue):
            metadata_ids.bind_source_id('issue', second_id, self.identity)
        self.assertEqual(self.cursor.execute("""
            SELECT issue_id FROM issue_source_ids
            WHERE source='marvel' AND source_id='132266';
        """).fetchone()[0], first_id)

    def test_publisher_id_matches_even_after_a_canonical_title_change(self):
        issue_id = self._publisher_issue()
        enriched = enrich_discovery_records([{
            'record_key': 'marvel:132266', 'series_title': 'Publisher title',
            'publisher': 'Marvel Comics', 'series_year': 2026,
            'calculated_issue_number': 1.0, 'release_date': '2026-11-18',
            'provenance': [{
                'provider': 'marvel', 'external_id': '132266'
            }]
        }], [{
            'volume_id': 1, 'title': 'Canonical title', 'volume_year': 2026,
            'publisher': 'Marvel', 'volume_monitored': 1,
            'issue_id': issue_id, 'issue_comicvine_id': None,
            'calculated_issue_number': 1.0, 'issue_monitored': 1,
            'downloaded': 0, 'source_ids': [self.identity]
        }])
        self.assertEqual(enriched[0]['local_issue_id'], issue_id)
        self.assertEqual(enriched[0]['metadata_origin'], 'publisher')

    def test_new_publisher_issues_are_added_only_for_opted_in_series(self):
        self._publisher_issue()
        self.cursor.execute("""
            INSERT INTO release_discovery(
                provider, record_key, external_id, publisher, series_title,
                issue_number, release_date, available, fetched_at
            ) VALUES ('marvel', 'marvel:next', '132267', 'Marvel', 'X Orcists',
                2.0, '2026-12-16', 0, 1000);
        """)
        record = {
            'local_volume_id': 1, 'local_issue_id': None,
            'local_status': 'metadata_pending', 'provenance': [{
                'provider': 'marvel', 'external_id': '132267'
            }]
        }
        self.assertFalse(metadata_ids.sync_discovery_ids([record]))
        self.cursor.execute("""
            INSERT INTO metadata_reconciliation(volume_id, checked_at, status)
            VALUES (1, 0, 'pending');
        """)
        self.assertTrue(metadata_ids.sync_discovery_ids([record]))
        self.assertEqual(tuple(self.cursor.execute("""
            SELECT comicvine_id, monitored, store_date FROM issues
            WHERE calculated_issue_number = 2;
        """).fetchone()), (None, 0, '2026-12-16'))
        self.assertEqual(self.cursor.execute("""
            SELECT COUNT(*) FROM issue_source_ids
            WHERE source='marvel' AND source_id='132267';
        """).fetchone()[0], 1)

    def test_new_series_defaults_to_first_issue_year_and_honors_add_options(
        self):
        self.cursor.execute("""
            INSERT INTO release_discovery(
                provider, record_key, external_id, publisher, series_title,
                issue_number, release_date, fetched_at
            ) VALUES ('marvel', 'marvel:new', 'new', 'Marvel', 'New Run',
                1.0, '2026-11-18', 1000);
        """)
        with patch.object(volumes.Library, 'add_metadata', return_value=2) as add:
            result = metadata_ids.add_publisher_issue(
                'marvel',
                'new',
                1,
                monitor_scheme=MonitorScheme.NONE,
                volume_folder='Custom Run',
                special_version=SpecialVersion.ONE_SHOT)
        self.assertEqual(result, 2)
        metadata, *options = add.call_args.args
        self.assertEqual(metadata['year'], 2026)
        self.assertEqual(
            options, [
                1, True, MonitorScheme.NONE, True, 'Custom Run', SpecialVersion.ONE_SHOT])

        self.cursor.execute('UPDATE release_discovery SET issue_number = 9;')
        self.assertIsNone(
            metadata_ids.get_publisher_add_record(
                'marvel', 'new')['suggested_year'])
        self.cursor.execute('UPDATE release_discovery SET series_year = 2020;')
        self.assertEqual(
            metadata_ids.get_publisher_add_record(
                'marvel', 'new')['suggested_year'], 2020)
        with patch.object(volumes.Library, 'add_metadata', return_value=2) as add:
            metadata_ids.add_publisher_issue('marvel', 'new', 1, year=2021)
        self.assertEqual(add.call_args.args[0]['year'], 2021)

    def test_adding_comicvine_metadata_reuses_a_publisher_volume(self):
        issue_id = self._publisher_issue()
        client = self._client()
        with patch.object(
            volumes, 'ComicVine', return_value=client
        ), patch.object(
            volumes, 'RootFolders'
        ), patch.object(volumes, 'commit', side_effect=self.connection.commit):
            added = volumes.Library.add(500, 1, monitored=True)

        self.assertEqual(added, 1)
        self.assertEqual(self.cursor.execute(
            'SELECT COUNT(*) FROM volumes;'
        ).fetchone()[0], 1)
        self.assertEqual(self.cursor.execute(
            'SELECT id FROM issues WHERE comicvine_id = 700;'
        ).fetchone()[0], issue_id)
        client.fetch_mock.assert_awaited_once()

    def test_confirmed_identity_can_update_a_corrected_issue_number(self):
        issue_id = self._publisher_issue()
        metadata_ids.reconcile_comicvine(1, client=self._client())

        updated_id = metadata_ids.upsert_issue_metadata(
            1, issue_metadata(700, number=1.5), monitored=True
        )

        self.assertEqual(updated_id, issue_id)
        self.assertEqual(
            self.cursor.execute(
                'SELECT calculated_issue_number FROM issues WHERE id = ?;',
                (issue_id,
        )).fetchone()[0],
            1.5)

    def test_a_different_run_with_the_same_number_is_not_relabelled(self):
        self._publisher_issue()
        with self.assertRaises(InvalidKeyValue):
            metadata_ids.upsert_issue_metadata(
                1, issue_metadata(
                    date='2000-01-01', store_date='2000-01-01',
                    source_ids=[{**self.identity, 'source_id': 'other-run'}]
                ), monitored=False
            )
        self.assertIsNone(self.cursor.execute("""
            SELECT issue_id FROM issue_source_ids WHERE source_id='other-run';
        """).fetchone())

    def test_failed_identity_binding_rolls_back_a_discovery_issue(self):
        self._publisher_issue()
        self.cursor.execute("""
            INSERT INTO metadata_reconciliation(volume_id, checked_at, status)
            VALUES (1, 0, 'pending');
        """)
        self.cursor.execute("""
            INSERT INTO release_discovery(
                provider, record_key, external_id, publisher, series_title,
                issue_number, release_date, available, fetched_at
            ) VALUES ('marvel', 'marvel:next', '132267', 'Marvel', 'X Orcists',
                2.0, '2026-12-16', 0, 1000);
        """)
        self.connection.commit()
        with patch.object(
            metadata_ids, 'bind_source_id',
            side_effect=InvalidKeyValue('source_id', 'conflicting owner')
        ), self.assertRaises(InvalidKeyValue):
            metadata_ids.sync_discovery_ids([{
                'local_volume_id': 1, 'local_issue_id': None,
                'local_status': 'metadata_pending', 'provenance': [{
                    'provider': 'marvel', 'external_id': '132267'
                }]
            }])
        self.assertEqual(self.cursor.execute(
            'SELECT COUNT(*) FROM issues;'
        ).fetchone()[0], 1)

    def test_comicvine_omission_does_not_delete_a_publisher_linked_issue(self):
        issue_id = self._publisher_issue()
        metadata_ids.reconcile_comicvine(1, client=self._client())
        client = self._client()
        client.fetch_volumes_mock.return_value = [
            series_metadata(issue_count=0, issues=[])
        ]
        client.fetch_issues_mock.return_value = []
        with patch.object(
            volumes, 'ComicVine', return_value=client
        ), patch.object(
            volumes, 'Settings'
        ) as settings, patch.object(
            volumes, 'commit', side_effect=self.connection.commit
        ), patch.object(
            volumes, '_get_activity_counts', return_value=(1, 0)
        ), patch.object(
            volumes, 'determine_special_version', return_value=None
        ), patch.object(volumes, 'scan_files'):
            settings.return_value.sv.refresh_skip_window = 24
            volumes.refresh_and_scan(volume_id=1, allow_skipping=False)

        self.assertEqual(self.cursor.execute(
            'SELECT id FROM issues;'
        ).fetchone()[0], issue_id)
        self.assertEqual(self.cursor.execute(
            'SELECT status FROM metadata_reconciliation WHERE volume_id=1;'
        ).fetchone()[0], 'conflict')
        recheck = metadata_ids.reconcile_comicvine(
            1, client=self._client(
                metadata=series_metadata(
                    issue_count=0, issues=[])))
        self.assertEqual(recheck['status'], 'conflict')

    def test_publisher_date_changes_update_the_existing_native_issue(self):
        issue_id = self._publisher_issue()
        self.cursor.execute("""
            INSERT INTO release_discovery(
                provider, record_key, external_id, publisher, series_title,
                issue_number, release_date, available, fetched_at
            ) VALUES ('marvel', 'marvel:current', '132266', 'Marvel', 'X Orcists',
                1.0, '2026-12-16', 0, 1000);
        """)
        self.connection.commit()
        changed = metadata_ids.sync_discovery_ids([{
            'local_volume_id': 1, 'local_issue_id': issue_id,
            'local_status': 'missing_unmonitored',
            'release_date_provider': 'marvel',
            'provenance': [{'provider': 'marvel', 'external_id': '132266'}]
        }])
        self.assertTrue(changed)
        self.assertEqual(tuple(self.cursor.execute("""
            SELECT id, comicvine_id, store_date, monitored FROM issues;
        """).fetchone()), (issue_id, None, '2026-12-16', 0))

    def test_native_entries_use_reconciliation_not_repeated_comicvine_refreshes(
        self):
        self._publisher_issue()
        self.cursor.execute("""
            INSERT INTO metadata_reconciliation(volume_id, checked_at, status)
            VALUES (1, 0, 'pending');
        """)
        self.cursor.execute("""
            INSERT INTO pending_release_watches(
                record_key, volume_id, first_seen, last_checked, release_date
            ) VALUES (
                'marvel:current', 1,
                CAST(strftime('%s', 'now') AS INTEGER),
                CAST(strftime('%s', 'now') AS INTEGER), '2026-11-18'
            );
        """)
        with patch.object(release_refresh, 'get_db', return_value=self.cursor):
            self.assertEqual(
                release_refresh.find_volumes_with_new_releases(), set())
        self.assertEqual(metadata_ids.pending_reconciliation_volumes(), [1])

    def test_missing_credentials_do_not_retry_every_publisher_volume(self):
        with patch.object(
            metadata_ids, 'pending_reconciliation_volumes', return_value=[1, 2]
        ), patch.object(
            metadata_ids, 'reconcile_comicvine',
            side_effect=InvalidComicVineApiKey
        ) as reconcile:
            attempted = metadata_ids.refresh_publisher_volumes()
        self.assertEqual(attempted, [1])
        reconcile.assert_called_once_with(1)
        self.assertEqual(self.cursor.execute(
            'SELECT status FROM metadata_reconciliation WHERE volume_id=1;'
        ).fetchone()[0], 'error')

    def test_unverified_match_does_not_block_other_publisher_volumes(self):
        for error, status in (
            (InvalidKeyValue('source_id', 'conflicting owner'), 'conflict'),
            (VolumeNotMatched(), 'pending'),
        ):
            with self.subTest(status=status), patch.object(
                metadata_ids, 'pending_reconciliation_volumes',
                return_value=[1, 2]
            ), patch.object(
                metadata_ids, 'reconcile_comicvine', side_effect=[error, {}]
            ) as reconcile:
                self.assertEqual(
                    metadata_ids.refresh_publisher_volumes(), [1, 2]
                )
                reconcile.assert_any_call(2)
                self.assertEqual(self.cursor.execute(
                    'SELECT status FROM metadata_reconciliation '
                    'WHERE volume_id=1;'
                ).fetchone()[0], status)


if __name__ == '__main__':
    unittest.main()
