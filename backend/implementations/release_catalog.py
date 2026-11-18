# -*- coding: utf-8 -*-

import re
from collections import Counter
from typing import Dict, List
from urllib.parse import unquote, urlsplit

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.helpers import normalise_query_string
from backend.implementations.release_discovery import (_publisher_family,
                                                       get_release_discovery)
from backend.internals.db import get_db

CATALOG_SORTS = frozenset((
    'date-desc', 'date-asc', 'title-asc', 'number-asc', 'number-desc'
))
CATALOG_STATUSES = frozenset((
    'downloaded', 'missing_monitored', 'missing_unmonitored',
    'metadata_pending', 'not_in_library', 'ambiguous'
))

issue_reference_regex = re.compile(
    r'(?:#|\bissues?\s+)(-?\d+(?:\.\d+)?)\b', re.IGNORECASE
)
trailing_issue_regex = re.compile(r'\s+(\d{1,3}(?:\.\d+)?)$')
series_year_regex = re.compile(r'\(((?:19|20)\d{2})\)')


def _search_text(value: str) -> str:
    return ''.join(
        character
        for character in normalise_query_string(value).casefold()
        if character.isalnum()
    )


def validate_release_query(query: str) -> None:
    if query.lower().startswith(('https://', 'http://')):
        try:
            parsed = urlsplit(query)
            if not parsed.hostname:
                raise ValueError('Missing hostname')
        except ValueError as error:
            raise InvalidKeyValue('query', query) from error


def matches_release_query(record: dict, query: str) -> bool:
    """Search issue records without treating publisher IDs as library IDs."""
    query = query.strip()
    if not query:
        return True

    if query.lower().startswith(('https://', 'http://')):
        validate_release_query(query)
        parsed = urlsplit(query)
        expected = (parsed.netloc.lower(), unquote(parsed.path).rstrip('/'))
        urls = [
            source.get('external_url') or ''
            for source in record.get('provenance', ())
        ]
        urls.append(record.get('external_url') or '')
        return any(
            (urlsplit(url).netloc.lower(),
             unquote(urlsplit(url).path).rstrip('/')) == expected
            for url in urls if url
        )

    year = series_year_regex.search(query)
    if year:
        record_year = record.get('series_year') or record.get('issue_year')
        if record_year is not None and record_year != int(year.group(1)):
            return False
        query = series_year_regex.sub('', query)

    issue = issue_reference_regex.search(
        query) or trailing_issue_regex.search(query)
    if issue:
        if record.get('calculated_issue_number') != float(issue.group(1)):
            return False
        query = query[:issue.start()] + query[issue.end():]

    haystack = _search_text(' '.join((
        record.get('series_title') or '',
        record.get('issue_title') or '',
        record.get('publisher') or ''
    )))
    return all(
        _search_text(term) in haystack
        for term in query.split()
    )


async def get_release_catalog(
    start_date: str,
    end_date: str,
    force_refresh: bool = False
) -> List[dict]:
    """Combine publisher announcements with existing library issues."""
    discovered = await get_release_discovery(
        start_date, end_date, force_refresh=force_refresh
    )
    rows = get_db().execute("""
        SELECT
            i.id, i.issue_number, i.calculated_issue_number,
            i.title AS issue_title, COALESCE(i.store_date, i.date) AS release_date,
            v.id AS volume_id, v.title AS series_title, v.year AS series_year,
            v.publisher, v.monitored AS volume_monitored,
            i.monitored AS issue_monitored,
            EXISTS (
                SELECT 1 FROM issues_files WHERE issue_id = i.id
            ) AS downloaded
        FROM issues i
        INNER JOIN volumes v ON v.id = i.volume_id
        WHERE COALESCE(i.store_date, i.date) BETWEEN ? AND ?;
    """, (start_date, end_date)).fetchalldict()
    local_issues = {row['id']: row for row in rows}

    records = []
    linked_issues = set()
    for record in discovered:
        item = {**record}
        local = local_issues.get(record.get('local_issue_id'))
        if local is not None:
            item['issue_title'] = local['issue_title']
            item['providers'] = [
                *item['providers'], 'library'
            ]
            linked_issues.add(local['id'])
        records.append(item)

    for row in rows:
        if row['id'] in linked_issues:
            continue
        status = (
            'downloaded' if row['downloaded'] else
            'missing_monitored'
            if row['volume_monitored'] and row['issue_monitored'] else
            'missing_unmonitored'
        )
        records.append({
            'record_key': f'library:{row["id"]}',
            'series_title': row['series_title'],
            'series_year': row['series_year'],
            'publisher': row['publisher'],
            'issue_number': row['issue_number'],
            'calculated_issue_number': row['calculated_issue_number'],
            'issue_title': row['issue_title'],
            'release_date': row['release_date'],
            'providers': ['library'],
            'provenance': [],
            'local_status': status,
            'local_volume_id': row['volume_id'],
            'local_issue_id': row['id'],
            'available': False
        })
    return records


def filter_release_catalog(
    records: List[dict],
    *,
    query: str = '',
    publisher: str = '',
    local_status: str = '',
    sort: str = 'date-desc',
    page: int = 1,
    per_page: int = 48
) -> dict:
    counts = Counter(_publisher_family(item.get('publisher')) or 'unknown'
                     for item in records)
    names: Dict[str, str] = {}
    for item in records:
        names.setdefault(
            _publisher_family(item.get('publisher')) or 'unknown',
            item.get('publisher') or 'Unknown publisher'
        )
    publishers = sorted((
        {'key': key, 'name': names[key], 'issue_count': count}
        for key, count in counts.items()
    ), key=lambda item: item['name'].casefold())

    publisher_key = _publisher_family(publisher)
    items = [
        item for item in records
        if (not publisher or
            (_publisher_family(item.get('publisher')) or 'unknown') == publisher_key)
        and (not local_status or item['local_status'] == local_status)
        and matches_release_query(item, query)
    ]
    items.sort(key=lambda item: (
        item['series_title'].casefold(),
        item.get('calculated_issue_number') or 0
    ))
    if sort.startswith('date-'):
        items.sort(
            key=lambda item: item.get('release_date') or '',
            reverse=sort == 'date-desc'
        )
    elif sort.startswith('number-'):
        items.sort(
            key=lambda item: item.get('calculated_issue_number') or 0,
            reverse=sort == 'number-desc'
        )

    offset = (page - 1) * per_page
    return {
        'items': items[offset:offset + per_page],
        'publishers': publishers,
        'total': len(items),
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (len(items) + per_page - 1) // per_page)
    }
