# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import date, timedelta
from time import time
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Union, cast

from backend.base.definitions import (
    NewReleaseMetadata,
    PublisherMetadata,
    VolumeMetadata,
)
from backend.base.logging import LOGGER
from backend.implementations.response_cache import ResponseCache
from backend.internals.db import get_db

if TYPE_CHECKING:
    from backend.implementations.metadata_sources.base import MetadataSource


class MetadataCache(ResponseCache):
    RELEASE_TTL = 6 * 60 * 60
    PUBLISHER_TTL = 24 * 60 * 60
    PUBLISHER_VOLUMES_TTL = 12 * 60 * 60
    RELEASE_FETCH_LIMIT = 5000
    PUBLISHER_FETCH_LIMIT = 1000
    RETENTION = 30 * 24 * 60 * 60

    def __init__(self, source: MetadataSource) -> None:
        self.source = source
        super().__init__(source.source_type.value, lambda: get_db())

    @staticmethod
    def _as_date(value: str) -> date:
        return date.fromisoformat(value)

    def _release_gaps(
        self,
        start_date: str,
        end_date: str,
        now: Union[int, None]
    ) -> List[Tuple[str, str]]:
        requested_start = self._as_date(start_date)
        requested_end = self._as_date(end_date)
        cursor = get_db()
        expiry_clause = 'AND expires_at > ?' if now is not None else ''
        params: Tuple[Any, ...] = (
            (self.source_key, now, start_date, end_date)
            if now is not None
            else (self.source_key, start_date, end_date)
        )
        windows = cursor.execute(f"""
            SELECT start_date, end_date
            FROM release_cache_windows
            WHERE metadata_source = ?
                {expiry_clause}
                AND end_date >= ?
                AND start_date <= ?
            ORDER BY start_date, end_date;
        """, params).fetchall()

        gaps: List[Tuple[str, str]] = []
        next_date = requested_start
        for row in windows:
            window_start = max(self._as_date(row[0]), requested_start)
            window_end = min(self._as_date(row[1]), requested_end)
            if window_start > next_date:
                gaps.append((
                    next_date.isoformat(),
                    (window_start - timedelta(days=1)).isoformat()
                ))
            next_date = max(next_date, window_end + timedelta(days=1))
            if next_date > requested_end:
                break

        if next_date <= requested_end:
            gaps.append((next_date.isoformat(), requested_end.isoformat()))
        return gaps

    def _store_release_window(
        self,
        start_date: str,
        end_date: str,
        releases: List[NewReleaseMetadata],
        now: int
    ) -> None:
        cursor = get_db()
        cursor.execute("""
            DELETE FROM release_cache
            WHERE metadata_source = ?
                AND COALESCE(store_date, cover_date) BETWEEN ? AND ?;
        """, (self.source_key, start_date, end_date))
        cursor.executemany("""
            INSERT INTO release_cache(
                metadata_source, issue_cv_id, volume_cv_id,
                volume_title, issue_number, calculated_issue_number,
                store_date, cover_date, cover_url, publisher, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metadata_source, issue_cv_id) DO UPDATE SET
                volume_cv_id = excluded.volume_cv_id,
                volume_title = excluded.volume_title,
                issue_number = excluded.issue_number,
                calculated_issue_number = excluded.calculated_issue_number,
                store_date = excluded.store_date,
                cover_date = excluded.cover_date,
                cover_url = excluded.cover_url,
                publisher = excluded.publisher,
                fetched_at = excluded.fetched_at;
        """, (
            (
                self.source_key,
                release['issue_cv_id'],
                release['volume_cv_id'],
                release['volume_title'],
                release['issue_number'],
                release['calculated_issue_number'],
                release['store_date'],
                release['cover_date'],
                release['cover_url'],
                release['publisher'],
                now
            )
            for release in releases
        ))
        cursor.execute("""
            DELETE FROM release_cache_windows
            WHERE metadata_source = ?
                AND start_date >= ?
                AND end_date <= ?;
        """, (self.source_key, start_date, end_date))
        cursor.execute("""
            INSERT INTO release_cache_windows(
                metadata_source, start_date, end_date,
                fetched_at, expires_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(metadata_source, start_date, end_date) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at;
        """, (
            self.source_key, start_date, end_date,
            now, now + self.RELEASE_TTL
        ))
        cursor.execute(
            'DELETE FROM release_cache_windows WHERE expires_at < ?;',
            (now - self.RETENTION,)
        )
        cursor.execute(
            'DELETE FROM release_cache WHERE fetched_at < ?;',
            (now - self.RETENTION,)
        )

    def _read_releases(
        self,
        start_date: str,
        end_date: str,
        limit: int
    ) -> List[NewReleaseMetadata]:
        rows = get_db().execute("""
            SELECT
                cache.issue_cv_id,
                cache.volume_cv_id,
                cache.volume_title,
                cache.issue_number,
                cache.calculated_issue_number,
                cache.store_date,
                cache.cover_date,
                cache.cover_url,
                cache.publisher,
                volumes.id AS volume_id
            FROM release_cache AS cache
            LEFT JOIN volumes
                ON volumes.comicvine_id = cache.volume_cv_id
            WHERE cache.metadata_source = ?
                AND COALESCE(cache.store_date, cache.cover_date)
                    BETWEEN ? AND ?
            ORDER BY COALESCE(cache.store_date, cache.cover_date) DESC,
                cache.volume_title, cache.calculated_issue_number
            LIMIT ?;
        """, (self.source_key, start_date, end_date, limit)).fetchall()

        return [
            {
                'issue_cv_id': row[0],
                'volume_cv_id': row[1],
                'volume_title': row[2],
                'issue_number': row[3],
                'calculated_issue_number': row[4],
                'store_date': row[5],
                'cover_date': row[6],
                'cover_url': row[7],
                'publisher': row[8],
                'in_library': row[9] is not None,
                'volume_id': row[9],
                'metadata_source': self.source_key
            }
            for row in rows
        ]

    async def get_releases(
        self,
        start_date: str,
        end_date: str,
        limit: int = 100,
        force_refresh: bool = False
    ) -> List[NewReleaseMetadata]:
        start = self._as_date(start_date)
        end = self._as_date(end_date)
        if end < start or (end - start).days > 365:
            raise ValueError('Release cache range must be 0-365 days')
        if limit < 1:
            raise ValueError('Release cache limit must be positive')
        lock_key = f'{self.source_key}:releases'
        generation = self._generations.get(lock_key, 0)
        lock = await self._acquire_lock(lock_key)
        try:
            now = round(time())
            force_refresh = (
                force_refresh
                and self._generations.get(lock_key, 0) == generation
            )
            gaps = (
                [(start_date, end_date)]
                if force_refresh
                else self._release_gaps(start_date, end_date, now)
            )
            try:
                fetched_windows = []
                for gap_start, gap_end in gaps:
                    releases = await self.source.get_new_releases(
                        gap_start,
                        gap_end,
                        self.RELEASE_FETCH_LIMIT
                    )
                    fetched_windows.append((
                        gap_start, gap_end, releases
                    ))

                connection = get_db().connection
                for gap_start, gap_end, releases in fetched_windows:
                    self._store_release_window(
                        gap_start, gap_end, releases, now
                    )
                connection.commit()
                if gaps:
                    self._generations[lock_key] = generation + 1
            except Exception:
                get_db().connection.rollback()
                stale = self._read_releases(start_date, end_date, limit)
                stale_covers_range = not self._release_gaps(
                    start_date, end_date, None
                )
                if stale_covers_range:
                    LOGGER.warning(
                        'Using stale %s release cache after refresh failure',
                        self.source_key
                    )
                    return stale
                raise
        finally:
            lock.release()

        return self._read_releases(start_date, end_date, limit)

    async def get_publishers(
        self,
        limit: int = 100,
        force_refresh: bool = False
    ) -> List[PublisherMetadata]:
        fetch_limit = self.PUBLISHER_FETCH_LIMIT

        async def fetch_publishers():
            publishers = await self.source.get_publishers(fetch_limit)
            cursor = get_db()
            now = round(time())
            cursor.executemany("""
                INSERT INTO publisher_cache(
                    metadata_source, comicvine_id, name,
                    site_url, volume_count, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(metadata_source, comicvine_id) DO UPDATE SET
                    name = excluded.name,
                    site_url = excluded.site_url,
                    volume_count = excluded.volume_count,
                    fetched_at = excluded.fetched_at;
            """, (
                (
                    self.source_key,
                    publisher['comicvine_id'],
                    publisher['name'],
                    publisher['site_url'],
                    publisher['volume_count'],
                    now
                )
                for publisher in publishers
            ))
            cursor.connection.commit()
            return publishers

        publishers = await self._get_cached_response(
            'publishers',
            'all',
            self.PUBLISHER_TTL,
            fetch_publishers,
            force_refresh
        )
        return publishers[:limit]

    async def get_publisher_volumes(
        self,
        publisher_id: int,
        limit: int = 100,
        force_refresh: bool = False
    ) -> List[VolumeMetadata]:
        fetch_limit = self.PUBLISHER_FETCH_LIMIT
        volumes = await self._get_cached_response(
            'publisher_volumes',
            str(publisher_id),
            self.PUBLISHER_VOLUMES_TTL,
            lambda: self.source.search_publisher_volumes(
                publisher_id, fetch_limit
            ),
            force_refresh
        )
        visible_volumes = volumes[:limit]
        if not visible_volumes:
            return visible_volumes

        added_volumes: Dict[int, int] = {}
        source_ids = [volume['comicvine_id'] for volume in visible_volumes]
        for offset in range(0, len(source_ids), 500):
            batch = source_ids[offset:offset + 500]
            placeholders = ','.join('?' for _ in batch)
            added_volumes.update(dict(get_db().execute(f"""
                SELECT comicvine_id, id
                FROM volumes
                WHERE comicvine_id IN ({placeholders});
            """, batch)))
        return cast(List[VolumeMetadata], [
            {
                **volume,
                'already_added': added_volumes.get(volume['comicvine_id'])
            }
            for volume in visible_volumes
        ])
