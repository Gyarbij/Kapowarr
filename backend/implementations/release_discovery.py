# -*- coding: utf-8 -*-

import re
from abc import ABC, abstractmethod
from asyncio import Semaphore, gather
from datetime import date, timedelta
from time import time
from typing import Any, Dict, List, Tuple, Union

from aiohttp import ClientError

from backend.base.logging import LOGGER
from backend.implementations.matching import _clean_match_title
from backend.implementations.response_cache import ResponseCache
from backend.internals.db import get_db

DISCOVERY_DAYS_BACK = 60
"How many days back releases are kept track of"

DISCOVERY_DAYS_AHEAD = 60
"How many days ahead releases are fetched, before they come out"

SETTLED_WEEK_AGE = 14
"After how many days the releases of a week are not expected to change anymore"

SETTLED_WEEK_TTL = 7 * 24 * 60 * 60
"For how many seconds the releases of a settled week are cached"

PENDING_WATCH_RETENTION = 30 * 24 * 60 * 60
"""
After how many seconds a pending release is forgotten, when it isn't seen
anymore in the releases that are kept track of.
"""

GENERIC_PUBLISHER_WORDS = frozenset((
    'comic', 'comics', 'entertainment', 'publishing', 'publications',
    'studio', 'studios', 'press', 'books', 'media', 'productions',
    'group', 'inc', 'llc', 'ltd'
))
"Words in the name of a publisher that sources include or leave out at will"

publisher_word_regex = re.compile(r'[a-z0-9]+')

RELEASE_DATE_AUTHORITY = (
    # The publisher itself
    'marvel', 'dc',
    # The schedule of the publisher or its distributor
    'comixnow', 'lunar',
    # Gathered from others
    'locg',
    # When it was made available, which can be later than when it came out
    'getcomics'
)
"""
The providers in order of how much their date for when an issue comes out is
trusted, for when providers have a different date for the same issue.
"""


def get_discovery_range(
    today: Union[date, None] = None
) -> Tuple[str, str]:
    """Get the range of dates for which releases are kept track of.

    Args:
        today (Union[date, None], optional): The date to base the range on.
            Defaults to None.

    Returns:
        Tuple[str, str]: The first and the last date of the range.
    """
    today = today or date.today()
    return (
        (today - timedelta(days=DISCOVERY_DAYS_BACK)).isoformat(),
        (today + timedelta(days=DISCOVERY_DAYS_AHEAD)).isoformat()
    )


def release_weeks(start_date: str, end_date: str) -> List[Tuple[date, date]]:
    """Get the weeks that a range of dates covers. A week runs from Sunday up
    to and including Saturday, so that it has exactly one release day.

    Args:
        start_date (str): The first date of the range.
        end_date (str): The last date of the range.

    Returns:
        List[Tuple[date, date]]: The first and last day of each week.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    week_start = start - timedelta(days=(start.weekday() + 1) % 7)
    weeks = []
    while week_start <= end:
        weeks.append((week_start, week_start + timedelta(days=6)))
        week_start += timedelta(days=7)
    return weeks


class ReleaseDiscoveryProvider(ABC):
    key: str
    ttl: int

    background_only = False
    """
    Whether fetching is too slow to do while a request is waiting for it. If
    so, only the release tasks fetch from the provider. Requests still get
    its releases, as the ones that were fetched before are stored.
    """

    def is_available(self) -> bool:
        """Check whether the provider can be used with the current setup.

        Returns:
            bool: Whether releases can be fetched from the provider.
        """
        return True

    @abstractmethod
    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        ...


class WeeklyDiscoveryProvider(ReleaseDiscoveryProvider):
    """A provider that gives the releases of one week at a time. The weeks are
    fetched and cached separately, so that a week is only fetched again when
    its releases could have changed."""

    concurrency = 3
    "How many weeks are fetched at the same time"

    @abstractmethod
    async def fetch_week(self, week_start: date, week_end: date) -> List[dict]:
        """Fetch the releases of a week.

        Args:
            week_start (date): The first day of the week, a Sunday.
            week_end (date): The last day of the week, a Saturday.

        Returns:
            List[dict]: The releases.
        """
        ...

    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        records = []
        for week in release_weeks(start_date, end_date):
            records.extend(await self.fetch_week(*week))
        return records


def _publisher_family(publisher: Union[str, None]) -> str:
    """Reduce the name of a publisher to what identifies it, so that the
    different ways in which sources write the name are equal. E.g. 'Dynamite'
    and 'Dynamite Entertainment', or 'BOOM! Studios' and 'Boom Studios'.

    Args:
        publisher (Union[str, None]): The name of the publisher.

    Returns:
        str: The identifying part of the name.
    """
    value = (publisher or '').lower()
    if 'marvel' in value:
        return 'marvel'
    if value == 'dc' or value.startswith('dc '):
        return 'dc'
    if 'image' in value:
        return 'image'
    words = publisher_word_regex.findall(value)
    return ''.join(
        [word for word in words if word not in GENERIC_PUBLISHER_WORDS]
        or words
    )


def _compatible_years(first: Union[int, None], second: Union[int, None]) -> bool:
    return first is None or second is None or first == second


def _date_authority(provider: Union[str, None]) -> int:
    """Get how much a provider is trusted for the date that an issue comes
    out, compared to the other providers.

    Args:
        provider (Union[str, None]): The key of the provider.

    Returns:
        int: The lower, the more the date of the provider is trusted.
    """
    if provider in RELEASE_DATE_AUTHORITY:
        return RELEASE_DATE_AUTHORITY.index(provider)
    return len(RELEASE_DATE_AUTHORITY)


def _merged_record_key(record: dict) -> str:
    title = ''.join(
        character
        for character in record['series_title'].lower()
        if character.isalnum()
    )
    publisher = _publisher_family(record.get('publisher'))
    number = record.get('calculated_issue_number')
    year = record.get('series_year') or ''
    return f'{publisher}:{title}:{number}:{year}'


def merge_discovery_records(records: List[dict]) -> List[dict]:
    merged: List[dict] = []
    # Records of the same issue have the same number, publisher and title.
    # Looking those up avoids comparing every record with every other record.
    merged_by_issue: Dict[Tuple[Any, str, str], List[dict]] = {}
    for record in records:
        same_issue = merged_by_issue.setdefault((
            record.get('calculated_issue_number'),
            _publisher_family(record.get('publisher')),
            _clean_match_title(record.get('series_title', ''))
        ), [])
        existing = next((
            candidate
            for candidate in same_issue
            if _compatible_years(
                candidate.get('series_year'),
                record.get('series_year')
            )
        ), None)

        provenance = {
            'provider': record['provider'],
            'external_id': record.get('external_id'),
            'external_url': record.get('external_url')
        }
        if existing is None:
            item = {
                **record,
                'record_key': _merged_record_key(record),
                'providers': [record['provider']],
                'provenance': [provenance],
                'release_date_provider': (
                    record['provider']
                    if record.get('release_date') is not None
                    else None
                ),
                'download_url': (
                    record.get('external_url')
                    if record['provider'] == 'getcomics'
                    else None
                )
            }
            merged.append(item)
            same_issue.append(item)
            continue

        existing['providers'] = sorted(set(
            existing['providers'] + [record['provider']]
        ))
        existing['provenance'].append(provenance)
        existing['available'] = (
            existing.get('available', False)
            or record.get('available', False)
        )
        for field in ('series_year', 'cover_url'):
            if existing.get(field) is None and record.get(field) is not None:
                existing[field] = record[field]
        if record['provider'] == 'getcomics':
            existing['download_url'] = record.get('external_url')

        release_date = record.get('release_date')
        if release_date is not None and (
            existing.get('release_date') is None
            # The date of the source that knows best. If a source has
            # multiple dates, then the first one is when the issue came out.
            or (_date_authority(record['provider']), release_date) < (
                _date_authority(existing.get('release_date_provider')),
                existing['release_date']
            )
        ):
            existing['release_date'] = release_date
            existing['release_date_provider'] = record['provider']
            if existing.get('issue_year') is not None:
                existing['issue_year'] = int(release_date[:4])

    for record in merged:
        record['record_key'] = _merged_record_key(record)
    return merged


def merge_dated_discovery_records(records: List[dict]) -> List[dict]:
    return [
        record
        for record in merge_discovery_records(records)
        if record.get('release_date') is not None
    ]


def _issue_year_matches(record: dict, issue_date: Union[str, None]) -> bool:
    expected_year = record.get('issue_year')
    if expected_year is None or not issue_date:
        return True
    try:
        issue_year = int(issue_date[:4])
    except (TypeError, ValueError):
        return True
    return issue_year - 1 <= expected_year <= issue_year + 1


def enrich_discovery_records(
    records: List[dict],
    library_rows: List[dict]
) -> List[dict]:
    volumes = {}
    for row in library_rows:
        volume = volumes.setdefault(row['volume_id'], {
            'id': row['volume_id'],
            'title': row['title'],
            'alt_title': row.get('alt_title'),
            'year': row.get('volume_year'),
            'publisher': row.get('publisher'),
            'monitored': bool(row.get('volume_monitored')),
            'issues': []
        })
        if row.get('issue_id') is not None:
            volume['issues'].append({
                'id': row['issue_id'],
                'number': row['calculated_issue_number'],
                'date': row.get('issue_date'),
                'monitored': bool(row.get('issue_monitored')),
                'downloaded': bool(row.get('downloaded'))
            })

    # Volumes are found by their title, instead of comparing the title of
    # every record with the title of every volume.
    volumes_by_title: Dict[str, List[dict]] = {}
    for volume in volumes.values():
        volume['publisher_family'] = _publisher_family(volume['publisher'])
        for title in {
            _clean_match_title(title)
            for title in (volume['title'], volume['alt_title'])
            if title
        }:
            volumes_by_title.setdefault(title, []).append(volume)

    enriched = []
    for record in records:
        publisher_family = _publisher_family(record.get('publisher'))
        candidate_volumes = [
            volume
            for volume in volumes_by_title.get(
                _clean_match_title(record['series_title']),
                ()
            )
            if volume['publisher_family'] == publisher_family
            and _compatible_years(
                volume['year'],
                record.get('series_year')
            )
        ]
        exact_issues = [
            (volume, issue)
            for volume in candidate_volumes
            for issue in volume['issues']
            if issue['number'] == record.get('calculated_issue_number')
            and _issue_year_matches(record, issue['date'])
        ]

        local_volume_id = None
        local_issue_id = None
        if len(exact_issues) == 1:
            volume, issue = exact_issues[0]
            local_volume_id = volume['id']
            local_issue_id = issue['id']
            if issue['downloaded']:
                local_status = 'downloaded'
                match_reason = 'Issue is already downloaded'
            elif volume['monitored'] and issue['monitored']:
                local_status = 'missing_monitored'
                match_reason = 'Exact monitored issue is missing'
            else:
                local_status = 'missing_unmonitored'
                match_reason = 'Exact issue is not monitored'
        elif len(exact_issues) > 1:
            local_status = 'ambiguous'
            match_reason = 'Multiple local issues match'
        elif len(candidate_volumes) == 1:
            local_volume_id = candidate_volumes[0]['id']
            same_number = any(
                issue['number'] == record.get('calculated_issue_number')
                for issue in candidate_volumes[0]['issues']
            )
            if same_number:
                local_status = 'ambiguous'
                match_reason = 'Issue year conflicts with local metadata'
            else:
                local_status = 'metadata_pending'
                match_reason = 'Issue is not yet in canonical metadata'
        elif candidate_volumes:
            local_status = 'ambiguous'
            match_reason = 'Multiple local volumes match'
        else:
            local_status = 'not_in_library'
            match_reason = 'No exact local volume match'

        enriched.append({
            **record,
            'local_status': local_status,
            'match_reason': match_reason,
            'local_volume_id': local_volume_id,
            'local_issue_id': local_issue_id
        })

    return enriched


async def _fetch_weekly_provider(
    provider: WeeklyDiscoveryProvider,
    cache,
    start_date: str,
    end_date: str,
    force_refresh: bool
) -> List[dict]:
    """Get the releases of a weekly provider, fetching only the weeks that
    are not cached anymore. A week that fails doesn't affect the others.

    Raises:
        Exception: None of the weeks could be fetched.

    Returns:
        List[dict]: The releases of the weeks that are available.
    """
    settled_before = date.today() - timedelta(days=SETTLED_WEEK_AGE)
    semaphore = Semaphore(provider.concurrency)
    unreachable: List[BaseException] = []

    async def fetch_from_provider(
        week_start: date,
        week_end: date
    ) -> List[dict]:
        if unreachable:
            # Every request is retried multiple times before it fails. Going
            # through that for each week would take minutes, and would send
            # a lot of requests to a source that is already having trouble.
            raise unreachable[0]
        try:
            return await provider.fetch_week(week_start, week_end)
        except (ClientError, OSError) as error:
            unreachable.append(error)
            raise

    async def fetch_week(week_start: date, week_end: date) -> List[dict]:
        settled = week_end < settled_before
        async with semaphore:
            return await cache.get(
                'week',
                week_start.isoformat(),
                # A week that has passed without any releases is not
                # likely. It's rather that the source didn't give them.
                lambda records: (
                    SETTLED_WEEK_TTL
                    if settled and records else
                    provider.ttl
                ),
                lambda: fetch_from_provider(week_start, week_end),
                force_refresh and not settled
            )

    responses = await gather(*(
        fetch_week(*week)
        for week in release_weeks(start_date, end_date)
    ), return_exceptions=True)

    failures = [
        response
        for response in responses
        if isinstance(response, BaseException)
    ]
    if failures:
        if len(failures) == len(responses):
            raise failures[0]
        LOGGER.warning(
            'Discovery provider %s failed for %d of %d weeks: %s',
            provider.key,
            len(failures),
            len(responses),
            type(failures[0]).__name__
        )

    return [
        record
        for response in responses
        if not isinstance(response, BaseException)
        for record in response
    ]


async def fetch_discovery_sources(
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
    providers=None,
    cache_factory=ResponseCache,
    background: bool = False
) -> Dict[str, List[dict]]:
    """Fetch the releases from the supplemental sources.

    Args:
        start_date (str): The first date to get the releases of.

        end_date (str): The last date to get the releases of.

        force_refresh (bool, optional): Fetch the releases, even when they
            are still cached.
            Defaults to False.

        providers (optional): The providers to fetch from, instead of all.
            Defaults to None.

        cache_factory (optional): Makes the cache for a provider.
            Defaults to ResponseCache.

        background (bool, optional): Whether nothing is waiting for the
            result, so that slow providers can be fetched from too.
            Defaults to False.

    Returns:
        Dict[str, List[dict]]: The releases per provider that didn't fail.
    """
    if providers is None:
        from backend.implementations.release_discovery_sources import (
            ComixNowProvider,
            DCCatalogProvider,
            GetComicsWeeklyProvider,
            LeagueOfComicGeeksProvider,
            LunarReleaseProvider,
            MarvelCalendarProvider,
        )
        providers = [
            MarvelCalendarProvider(),
            DCCatalogProvider(),
            ComixNowProvider(),
            LunarReleaseProvider(),
            LeagueOfComicGeeksProvider(),
            GetComicsWeeklyProvider()
        ]

    providers = [
        provider
        for provider in providers
        if (background or not provider.background_only)
        and provider.is_available()
    ]

    async def fetch_provider(provider):
        cache = cache_factory(f'discovery-{provider.key}')
        if isinstance(provider, WeeklyDiscoveryProvider):
            return await _fetch_weekly_provider(
                provider, cache, start_date, end_date, force_refresh
            )
        return await cache.get(
            'records',
            f'{start_date}:{end_date}',
            provider.ttl,
            lambda: provider.fetch(start_date, end_date),
            force_refresh
        )

    responses = await gather(*(
        fetch_provider(provider)
        for provider in providers
    ), return_exceptions=True)

    result = {}
    for provider, response in zip(providers, responses):
        if isinstance(response, Exception):
            LOGGER.warning(
                'Discovery provider %s failed: %s',
                provider.key,
                type(response).__name__
            )
            continue
        result[provider.key] = response
    return result


def _store_discovery_records(
    provider_records: Dict[str, List[dict]]
) -> None:
    cursor = get_db()
    fetched_at = round(time())
    for provider, records in provider_records.items():
        cursor.executemany("""
            INSERT INTO release_discovery(
                provider, record_key,
                external_id, external_url,
                publisher, series_title, series_year,
                issue_number, issue_year, release_date,
                cover_url, source_updated_at,
                available, fetched_at
            ) VALUES (
                :provider, :record_key,
                :external_id, :external_url,
                :publisher, :series_title, :series_year,
                :issue_number, :issue_year, :release_date,
                :cover_url, :source_updated_at,
                :available, :fetched_at
            )
            ON CONFLICT(provider, record_key) DO UPDATE SET
                external_id = excluded.external_id,
                external_url = excluded.external_url,
                publisher = excluded.publisher,
                series_title = excluded.series_title,
                series_year = excluded.series_year,
                issue_number = excluded.issue_number,
                issue_year = excluded.issue_year,
                release_date = excluded.release_date,
                cover_url = excluded.cover_url,
                source_updated_at = excluded.source_updated_at,
                available = excluded.available,
                fetched_at = excluded.fetched_at;
        """, ({
            'provider': provider,
            'record_key': record['record_key'],
            'external_id': record.get('external_id'),
            'external_url': record.get('external_url'),
            'publisher': record.get('publisher'),
            'series_title': record['series_title'],
            'series_year': record.get('series_year'),
            'issue_number': record['calculated_issue_number'],
            'issue_year': record.get('issue_year'),
            'release_date': record.get('release_date'),
            'cover_url': record.get('cover_url'),
            'source_updated_at': record.get('source_updated_at'),
            'available': bool(record.get('available')),
            'fetched_at': fetched_at
        } for record in records))
    cursor.connection.commit()


def _read_discovery_records(
    start_date: str,
    end_date: str
) -> List[dict]:
    rows = get_db().execute("""
        SELECT
            provider, record_key,
            external_id, external_url,
            publisher, series_title, series_year,
            issue_number, issue_year, release_date,
            cover_url, source_updated_at,
            available
        FROM release_discovery
        WHERE release_date BETWEEN ? AND ?
            OR (release_date IS NULL AND provider = 'dc')
        ORDER BY release_date DESC, series_title, issue_number;
    """, (start_date, end_date)).fetchalldict()
    return [{
        'provider': row['provider'],
        'record_key': row['record_key'],
        'external_id': row['external_id'],
        'external_url': row['external_url'],
        'publisher': row['publisher'],
        'series_title': row['series_title'],
        'series_year': row['series_year'],
        'issue_number': row['issue_number'],
        'calculated_issue_number': row['issue_number'],
        'issue_year': row['issue_year'],
        'release_date': row['release_date'],
        'cover_url': row['cover_url'],
        'source_updated_at': row['source_updated_at'],
        'available': bool(row['available'])
    } for row in rows]


def get_discovery_library_rows() -> List[dict]:
    return get_db().execute("""
        SELECT
            v.id AS volume_id,
            v.title,
            v.alt_title,
            v.year AS volume_year,
            v.publisher,
            v.monitored AS volume_monitored,
            i.id AS issue_id,
            i.calculated_issue_number,
            i.date AS issue_date,
            i.monitored AS issue_monitored,
            EXISTS (
                SELECT 1 FROM issues_files
                WHERE issue_id = i.id
            ) AS downloaded
        FROM volumes v
        LEFT JOIN issues i ON i.volume_id = v.id;
    """).fetchalldict()


def _update_pending_watches(records: List[dict]) -> None:
    cursor = get_db()
    now = round(time())
    for record in records:
        if (
            record['local_status'] == 'metadata_pending'
            and record['local_volume_id'] is not None
        ):
            cursor.execute("""
                INSERT INTO pending_release_watches(
                    record_key, volume_id, first_seen, last_checked,
                    release_date
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(record_key) DO UPDATE SET
                    volume_id = excluded.volume_id,
                    last_checked = excluded.last_checked,
                    release_date = excluded.release_date;
            """, (
                record['record_key'],
                record['local_volume_id'],
                now,
                now,
                record.get('release_date')
            ))
        else:
            cursor.execute(
                'DELETE FROM pending_release_watches WHERE record_key = ?;',
                (record['record_key'],)
            )

    # Releases that left the range that is kept track of, or that a source
    # doesn't list anymore
    cursor.execute(
        'DELETE FROM pending_release_watches WHERE last_checked < ?;',
        (now - PENDING_WATCH_RETENTION,)
    )
    cursor.connection.commit()


async def get_release_discovery(
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
    providers=None,
    background: bool = False
) -> List[dict]:
    provider_records = await fetch_discovery_sources(
        start_date,
        end_date,
        force_refresh,
        providers,
        background=background
    )
    _store_discovery_records(provider_records)
    merged = merge_dated_discovery_records(
        _read_discovery_records(start_date, end_date)
    )
    enriched = enrich_discovery_records(
        merged,
        get_discovery_library_rows()
    )
    _update_pending_watches(enriched)
    return enriched
