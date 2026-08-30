# -*- coding: utf-8 -*-

from abc import ABC, abstractmethod
from asyncio import gather
from time import time
from typing import Dict, List, Union

from backend.base.logging import LOGGER
from backend.implementations.matching import match_title
from backend.implementations.response_cache import ResponseCache
from backend.internals.db import get_db


class ReleaseDiscoveryProvider(ABC):
    key: str
    ttl: int

    @abstractmethod
    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        ...


def _publisher_family(publisher: Union[str, None]) -> str:
    value = (publisher or '').lower()
    if 'marvel' in value:
        return 'marvel'
    if value == 'dc' or value.startswith('dc '):
        return 'dc'
    if 'image' in value:
        return 'image'
    return ''.join(character for character in value if character.isalnum())


def _compatible_years(first: Union[int, None], second: Union[int, None]) -> bool:
    return first is None or second is None or first == second


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
    for record in records:
        existing = next((
            candidate
            for candidate in merged
            if candidate['calculated_issue_number']
                == record.get('calculated_issue_number')
            and _publisher_family(candidate.get('publisher'))
                == _publisher_family(record.get('publisher'))
            and match_title(
                candidate['series_title'],
                record.get('series_title', '')
            )
            and _compatible_years(
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
                'download_url': (
                    record.get('external_url')
                    if record['provider'] == 'getcomics'
                    else None
                )
            }
            merged.append(item)
            continue

        existing['providers'] = sorted(set(
            existing['providers'] + [record['provider']]
        ))
        existing['provenance'].append(provenance)
        existing['available'] = (
            existing.get('available', False)
            or record.get('available', False)
        )
        for field in ('series_year', 'release_date', 'cover_url'):
            if existing.get(field) is None and record.get(field) is not None:
                existing[field] = record[field]
        if record['provider'] == 'getcomics':
            existing['download_url'] = record.get('external_url')

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

    enriched = []
    for record in records:
        candidate_volumes = [
            volume
            for volume in volumes.values()
            if (
                match_title(volume['title'], record['series_title'])
                or (
                    volume['alt_title']
                    and match_title(
                        volume['alt_title'],
                        record['series_title']
                    )
                )
            )
            and _publisher_family(volume['publisher'])
                == _publisher_family(record.get('publisher'))
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


async def fetch_discovery_sources(
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
    providers=None,
    cache_factory=ResponseCache
) -> Dict[str, List[dict]]:
    if providers is None:
        from backend.implementations.release_discovery_sources import (
            DCCatalogProvider,
            GetComicsWeeklyProvider,
            LunarReleaseProvider,
            MarvelCalendarProvider,
        )
        providers = [
            MarvelCalendarProvider(),
            DCCatalogProvider(),
            LunarReleaseProvider(),
            GetComicsWeeklyProvider()
        ]

    async def fetch_provider(provider):
        cache = cache_factory(f'discovery-{provider.key}')
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
                    record_key, volume_id, first_seen, last_checked
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(record_key) DO UPDATE SET
                    volume_id = excluded.volume_id,
                    last_checked = excluded.last_checked;
            """, (
                record['record_key'],
                record['local_volume_id'],
                now,
                now
            ))
        else:
            cursor.execute(
                'DELETE FROM pending_release_watches WHERE record_key = ?;',
                (record['record_key'],)
            )
    cursor.connection.commit()


async def get_release_discovery(
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
    providers=None
) -> List[dict]:
    provider_records = await fetch_discovery_sources(
        start_date,
        end_date,
        force_refresh,
        providers
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
