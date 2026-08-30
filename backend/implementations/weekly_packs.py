# -*- coding: utf-8 -*-

import re
from json import loads
from typing import Any, Dict, Iterable, List, Union
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from backend.base.file_extraction import extract_filename_data
from backend.base.helpers import AsyncSession
from backend.implementations.response_cache import ResponseCache

WEEK_DATE_REGEX = re.compile(
    r'(?P<year>\d{4})[.\-](?P<month>\d{2})[.\-](?P<day>\d{2})'
)
SIZE_REGEX = re.compile(
    r'\((?P<size>\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB))\)',
    re.IGNORECASE
)

INDIVIDUAL_SECTIONS = {
    'DC COMICS': 'DC Comics',
    'MARVEL COMICS': 'Marvel Comics',
    'IMAGE COMICS': 'Image Comics',
    'INDIE COMICS': None
}
ARCHIVE_SECTIONS = {'JPG', 'WEBP'}
WEEKLY_PACKS_URL = 'https://getcomics.org/wp-json/wp/v2/posts'
WEEKLY_PACK_ACTIONS = {'download', 'monitor_and_download'}


def _clean_text(value: str) -> str:
    return ' '.join(value.replace('\xa0', ' ').split()).strip(' :')


def _get_title_before_link(container: Tag, link: Tag) -> str:
    parts: List[str] = []
    for child in container.children:
        if child is link:
            break
        if isinstance(child, Tag):
            if link in child.descendants:
                break
            parts.append(child.get_text(' ', strip=True))
        else:
            parts.append(str(child))
    title = _clean_text(' '.join(parts))
    if title:
        return title

    return _clean_text(
        container.get_text(' ', strip=True).split(
            link.get_text(' ', strip=True), 1
        )[0]
    )


def _is_getcomics_url(url: str, article: bool = False) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    if (parsed.hostname or '').lower() not in (
        'getcomics.org',
        'www.getcomics.org'
    ):
        return False
    if article and parsed.path.startswith('/dls/'):
        return False
    return True


def _tag_url(link: Tag) -> str:
    value = link.get('href')
    return value if isinstance(value, str) else ''


def _section_siblings(heading: Tag) -> Iterable[Tag]:
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag) and sibling.name == 'h3':
            break
        if isinstance(sibling, Tag):
            yield sibling


def _parse_week_date(post: Dict[str, Any], title: str) -> str:
    match = WEEK_DATE_REGEX.search(title)
    if match:
        return '-'.join((
            match.group('year'),
            match.group('month'),
            match.group('day')
        ))

    published = str(post.get('date', ''))[:10]
    if WEEK_DATE_REGEX.fullmatch(published):
        return published
    raise ValueError('Weekly Pack post has no valid date')


def _archive_publisher(title: str) -> Union[str, None]:
    upper_title = title.upper()
    for prefix, publisher in (
        ('DC ', 'DC Comics'),
        ('MARVEL ', 'Marvel Comics'),
        ('IMAGE ', 'Image Comics'),
        ('INDIE ', 'Indie Comics')
    ):
        if prefix in upper_title:
            return publisher
    return None


def _parse_archives(heading: Tag, archive_format: str) -> List[dict]:
    archives = []
    for sibling in _section_siblings(heading):
        for entry in sibling.find_all('li'):
            valid_links = [
                link
                for link in entry.find_all('a', href=True)
                if _is_getcomics_url(_tag_url(link))
            ]
            links = [
                {
                    'label': _clean_text(link.get_text(' ', strip=True)),
                    'url': _tag_url(link)
                }
                for link in valid_links
            ]
            if not links:
                continue
            title = _get_title_before_link(entry, valid_links[0])
            size_match = SIZE_REGEX.search(entry.get_text(' ', strip=True))
            archives.append({
                'title': title,
                'format': archive_format,
                'publisher': _archive_publisher(title),
                'size': size_match.group('size') if size_match else None,
                'links': links
            })
    return archives


def _parse_items(
    heading: Tag,
    section: str,
    week_date: str,
    post_id: Union[int, str]
) -> List[dict]:
    items = []
    seen_urls = set()
    subpublisher: Union[str, None] = None
    section_publisher = INDIVIDUAL_SECTIONS[section]

    for sibling in _section_siblings(heading):
        if section == 'INDIE COMICS' and sibling.name == 'p':
            label = _clean_text(sibling.get_text(' ', strip=True))
            if label:
                subpublisher = label
            continue

        for entry in sibling.find_all('li'):
            article_link = next((
                link
                for link in entry.find_all('a', href=True)
                if 'download' in link.get_text(' ', strip=True).lower()
                and _is_getcomics_url(_tag_url(link), article=True)
            ), None)
            if article_link is None:
                continue
            article_url = _tag_url(article_link)
            if article_url in seen_urls:
                continue

            title = _get_title_before_link(entry, article_link)
            if not title:
                continue
            parsed = extract_filename_data(
                title,
                assume_volume_number=False,
                fix_year=True
            )
            seen_urls.add(article_url)
            items.append({
                'record_key': article_url,
                'pack_id': post_id,
                'provider': 'getcomics',
                'publisher': section_publisher or subpublisher or 'Indie Comics',
                'subpublisher': subpublisher,
                'display_title': title,
                'series_title': parsed['series'],
                'issue_number': parsed['issue_number'],
                'calculated_issue_number': parsed['issue_number'],
                'issue_year': int(week_date[:4]),
                'release_date': week_date,
                'external_url': article_url
            })
    return items


def parse_weekly_pack(post: Dict[str, Any]) -> dict:
    """Parse one GetComics WordPress Weekly Pack post."""
    title_data = post.get('title') or {}
    content_data = post.get('content') or {}
    title = _clean_text(str(title_data.get('rendered', '')))
    html = str(content_data.get('rendered', ''))
    post_url = str(post.get('link', ''))
    if post_url and not _is_getcomics_url(post_url, article=True):
        raise ValueError('Weekly Pack post URL is not on GetComics')

    week_date = _parse_week_date(post, title)
    soup = BeautifulSoup(html, 'html.parser')
    archives: List[dict] = []
    items: List[dict] = []
    sections = []

    for heading in soup.find_all('h3'):
        section = _clean_text(heading.get_text(' ', strip=True)).upper()
        if section in ARCHIVE_SECTIONS:
            sections.append(section)
            archives.extend(_parse_archives(heading, section))
        elif section in INDIVIDUAL_SECTIONS:
            sections.append(section)
            items.extend(_parse_items(
                heading,
                section,
                week_date,
                post.get('id', post_url)
            ))

    return {
        'id': post.get('id'),
        'title': title,
        'week_date': week_date,
        'external_url': post_url,
        'source_updated_at': post.get('modified') or post.get('date'),
        'sections': sections,
        'has_aggregate_archives': bool(archives),
        'archives': archives,
        'items': items
    }


async def get_weekly_packs(
    weeks: int = 8,
    force_refresh: bool = False
) -> List[dict]:
    if weeks < 1 or weeks > 50:
        raise ValueError('Weekly Pack range must be between 1 and 50 weeks')

    async def fetch():
        async with AsyncSession() as session:
            payload = await session.get_text(
                WEEKLY_PACKS_URL,
                params={
                    'search': 'Weekly Pack',
                    'per_page': weeks,
                    '_fields': 'id,date,modified,link,title,content'
                }
            )
        posts = loads(payload)
        return [
            parse_weekly_pack(post)
            for post in posts
            if 'weekly pack' in str(
                (post.get('title') or {}).get('rendered', '')
            ).lower()
        ]

    return await ResponseCache('getcomics').get(
        'weekly_packs',
        str(weeks),
        60 * 60,
        fetch,
        force_refresh
    )


def _discovery_item(item: dict, source_updated_at: str) -> dict:
    issue_number = item.get('calculated_issue_number')
    if not isinstance(issue_number, (int, float)):
        issue_number = None
    return {
        **item,
        'calculated_issue_number': issue_number,
        'external_id': item['record_key'],
        'series_year': None,
        'cover_url': None,
        'source_updated_at': source_updated_at,
        'available': True
    }


async def get_enriched_weekly_packs(
    weeks: int = 8,
    force_refresh: bool = False,
    library_rows=None
) -> List[dict]:
    from backend.implementations.release_discovery import (
        enrich_discovery_records, get_discovery_library_rows)

    packs = await get_weekly_packs(weeks, force_refresh)
    rows = (
        library_rows
        if library_rows is not None
        else get_discovery_library_rows()
    )
    for pack in packs:
        discovery_items = [
            _discovery_item(item, pack['source_updated_at'])
            for item in pack['items']
        ]
        pack['items'] = enrich_discovery_records(discovery_items, rows)
    return packs


def _monitor_weekly_item(volume_id: int, issue_id: int) -> None:
    from backend.implementations.volumes import Issue, Volume

    volume = Volume(volume_id, check_existence=True)
    issue = Issue(issue_id, check_existence=True)
    if issue.get_data().volume_id != volume_id:
        raise ValueError('Weekly Pack issue does not belong to matched volume')
    volume.update({'monitored': True})
    issue.update({'monitored': True})


async def queue_weekly_pack_items(
    record_keys: List[str],
    weeks: int = 8,
    action: str = 'download',
    download_handler=None,
    library_rows=None
) -> List[dict]:
    from backend.features.download_queue import DownloadHandler

    if action not in WEEKLY_PACK_ACTIONS:
        raise ValueError('Unknown Weekly Pack action')

    packs = await get_enriched_weekly_packs(
        weeks,
        library_rows=library_rows
    )
    item_map = {
        item['record_key']: item
        for pack in packs
        for item in pack['items']
    }
    handler = download_handler or DownloadHandler()
    results = []
    seen = set()
    for record_key in record_keys:
        if record_key in seen:
            results.append({
                'record_key': record_key,
                'status': 'duplicate',
                'fail_reason': 'Item was selected more than once'
            })
            continue
        seen.add(record_key)

        item = item_map.get(record_key)
        if item is None:
            results.append({
                'record_key': record_key,
                'status': 'rejected',
                'local_status': 'not_found',
                'fail_reason': 'Item is not in the cached Weekly Packs'
            })
            continue
        required_status = (
            'missing_unmonitored'
            if action == 'monitor_and_download'
            else 'missing_monitored'
        )
        if (
            item['local_status'] != required_status
            or item['local_volume_id'] is None
            or item['local_issue_id'] is None
            or not _is_getcomics_url(item['external_url'], article=True)
        ):
            results.append({
                'record_key': record_key,
                'status': 'rejected',
                'local_status': item['local_status'],
                'fail_reason': item['match_reason']
            })
            continue

        local_status = item['local_status']
        if action == 'monitor_and_download':
            _monitor_weekly_item(
                item['local_volume_id'],
                item['local_issue_id']
            )
            local_status = 'missing_monitored'

        if handler.link_in_queue(item['external_url']):
            results.append({
                'record_key': record_key,
                'status': 'already_queued',
                'local_status': local_status,
                'fail_reason': None
            })
            continue

        added, failure = await handler.add(
            item['external_url'],
            item['local_volume_id'],
            item['local_issue_id'],
            False
        )
        if added:
            status = 'queued'
        elif failure is None:
            status = 'already_queued'
        else:
            status = 'failed'
        results.append({
            'record_key': record_key,
            'status': status,
            'local_status': local_status,
            'queue_entries': len(added),
            'fail_reason': failure.value if failure is not None else None
        })

    return results
