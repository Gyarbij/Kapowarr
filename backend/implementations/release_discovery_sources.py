# -*- coding: utf-8 -*-

import re
from asyncio import gather
from datetime import date, datetime, timedelta
from json import loads
from typing import Dict, List, Union
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from backend.base.file_extraction import extract_filename_data
from backend.base.helpers import AsyncSession
from backend.implementations.release_discovery import ReleaseDiscoveryProvider
from backend.implementations.weekly_packs import get_weekly_packs

MARVEL_CALENDAR_URL = 'https://www.marvel.com/comics/calendar'
DC_CATALOG_URL = 'https://www.dc.com/comics'
LUNAR_RELEASE_URL = (
    'https://www.lunardistribution.com/home/instoreproducts'
)
PUBLISHER_BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0 Safari/537.36'
    )
}

MARVEL_DATE_REGEX = re.compile(
    r'ON\s+SALE:\s*(?P<date>\d{1,2}/\d{1,2}/\d{4})',
    re.IGNORECASE
)
MARVEL_ISSUE_REGEX = re.compile(r'/comics/issue/(?P<id>\d+)/')
DC_PRODUCT_REGEX = re.compile(r'^\d{4}DC', re.IGNORECASE)


def _iso_date(value: str) -> str:
    return datetime.strptime(value, '%m/%d/%Y').date().isoformat()


def _valid_external_url(url: str, hostname: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in ('http', 'https')
        and (parsed.hostname or '').lower() in (hostname, f'www.{hostname}')
    )


def _record(
    provider: str,
    external_id: str,
    external_url: str,
    publisher: str,
    title: str,
    release_date: Union[str, None],
    cover_url: Union[str, None] = None
) -> Union[dict, None]:
    parsed = extract_filename_data(
        title,
        assume_volume_number=False,
        fix_year=True
    )
    issue_number = parsed['issue_number']
    if issue_number is None or isinstance(issue_number, tuple):
        return None
    return {
        'record_key': f'{provider}:{external_id}',
        'provider': provider,
        'external_id': external_id,
        'external_url': external_url,
        'publisher': publisher,
        'series_title': parsed['series'],
        'series_year': parsed['year'],
        'issue_number': issue_number,
        'calculated_issue_number': issue_number,
        'issue_year': (
            int(release_date[:4]) if release_date is not None else None
        ),
        'release_date': release_date,
        'cover_url': cover_url,
        'source_updated_at': None,
        'available': False
    }


def parse_marvel_calendar(html: str) -> List[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    seen = set()

    def add_link(link, release_date: str, cover_url=None) -> None:
        href_value = link.get('href')
        href = href_value if isinstance(href_value, str) else ''
        match = MARVEL_ISSUE_REGEX.search(href)
        title = ' '.join(link.get_text(' ', strip=True).split())
        if match is None or '#' not in title or match.group('id') in seen:
            return
        external_url = urljoin(MARVEL_CALENDAR_URL, href)
        if not _valid_external_url(external_url, 'marvel.com'):
            return
        record = _record(
            'marvel',
            match.group('id'),
            external_url,
            'Marvel Comics',
            title,
            release_date,
            cover_url
        )
        if record is not None:
            records.append(record)
            seen.add(match.group('id'))

    for container in soup.select('.FeaturedGrid__Container'):
        release_date = None
        for child in container.find_all(recursive=False):
            child_classes = child.get('class') or []
            if 'FeaturedGrid__CalendarHeader' in child_classes:
                date_match = MARVEL_DATE_REGEX.search(
                    child.get_text(' ', strip=True)
                )
                release_date = (
                    _iso_date(date_match.group('date'))
                    if date_match is not None
                    else None
                )
                continue
            if release_date is None:
                continue
            image = child.find('img', src=True)
            image_url = (
                image.get('src')
                if image is not None
                and isinstance(image.get('src'), str)
                else None
            )
            for link in child.find_all('a', href=True):
                add_link(link, release_date, image_url)

    if records:
        return records

    date_match = MARVEL_DATE_REGEX.search(soup.get_text(' ', strip=True))
    if date_match is None:
        return []
    release_date = _iso_date(date_match.group('date'))
    for link in soup.find_all('a', href=True):
        add_link(link, release_date)
    return records


def parse_lunar_releases(response: Dict) -> List[dict]:
    if not response.get('success'):
        return []

    records_by_issue = {}
    products = sorted(
        response.get('products') or [],
        key=lambda product: str(product.get('Code', ''))
    )
    for product in products:
        code = str(product.get('Code', ''))
        title = str(product.get('Title', ''))
        release_value = str(product.get('Instore', ''))
        if not DC_PRODUCT_REGEX.match(code) or not title or not release_value:
            continue
        try:
            release_date = _iso_date(release_value)
        except ValueError:
            continue
        record = _record(
            'lunar',
            code,
            'https://www.lunardistribution.com/',
            'DC Comics',
            title,
            release_date,
            str(product.get('ImageUrl') or '') or None
        )
        if record is None:
            continue
        issue_key = (
            record['series_title'].lower(),
            record['calculated_issue_number'],
            release_date
        )
        records_by_issue.setdefault(issue_key, record)
    return list(records_by_issue.values())


def parse_dc_catalog(html: str) -> List[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    seen = set()

    next_data = soup.find('script', id='__NEXT_DATA__')
    if next_data is not None:
        try:
            page_data = loads(next_data.get_text())
        except (TypeError, ValueError):
            page_data = None

        stack = [page_data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
                continue
            if isinstance(value, list):
                stack.extend(value)
                continue
            if not (
                isinstance(value, str)
                and 'DC_ComicBook' in value
                and 'onSaleDate' in value
            ):
                continue
            try:
                comic_records = loads(value)
            except ValueError:
                continue
            if not isinstance(comic_records, list):
                continue

            for comic in comic_records:
                if not isinstance(comic, dict):
                    continue
                external_id = str(
                    comic.get('gepContentId') or comic.get('id') or ''
                )
                title = str(
                    ((comic.get('title') or {}).get('en_US') or {}).get(
                        'full',
                        ''
                    )
                )
                page_path = str(
                    (comic.get('pageAlias') or {}).get('pagePath') or ''
                )
                on_sale_date = str(comic.get('onSaleDate') or '')[:10]
                cover_url = str(
                    (comic.get('featuredImage') or {}).get('imageUrl') or ''
                ) or None
                external_url = urljoin(DC_CATALOG_URL, page_path)
                if (
                    not external_id
                    or not title
                    or not on_sale_date
                    or external_id in seen
                    or not _valid_external_url(external_url, 'dc.com')
                ):
                    continue
                record = _record(
                    'dc', external_id, external_url,
                    'DC Comics', title, on_sale_date, cover_url
                )
                if record is not None:
                    records.append(record)
                    seen.add(external_id)

    if records:
        return records

    for image in soup.find_all('img', alt=True):
        title = ' '.join(str(image.get('alt', '')).split())
        if '#' not in title:
            continue
        link = image.find_parent('a', href=True)
        if link is None:
            continue
        href_value = link.get('href')
        href = href_value if isinstance(href_value, str) else ''
        external_url = urljoin(DC_CATALOG_URL, href)
        if not _valid_external_url(external_url, 'dc.com'):
            continue
        external_id = href.strip('/') or external_url
        if external_id in seen:
            continue
        cover_value = image.get('src')
        cover_url = cover_value if isinstance(cover_value, str) else None
        record = _record(
            'dc', external_id, external_url,
            'DC Comics', title, None, cover_url
        )
        if record is not None:
            records.append(record)
            seen.add(external_id)
    return records


class MarvelCalendarProvider(ReleaseDiscoveryProvider):
    key = 'marvel'
    ttl = 6 * 60 * 60

    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        async with AsyncSession() as session:
            html = await session.get_text(
                MARVEL_CALENDAR_URL,
                params={
                    'dateStart': start_date,
                    'dateEnd': end_date
                },
                headers=PUBLISHER_BROWSER_HEADERS
            )
        return parse_marvel_calendar(html)


class DCCatalogProvider(ReleaseDiscoveryProvider):
    key = 'dc'
    ttl = 12 * 60 * 60

    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        async with AsyncSession() as session:
            html = await session.get_text(
                DC_CATALOG_URL,
                headers=PUBLISHER_BROWSER_HEADERS
            )
        return parse_dc_catalog(html)


class LunarReleaseProvider(ReleaseDiscoveryProvider):
    key = 'lunar'
    ttl = 12 * 60 * 60

    async def _fetch_date(self, session: AsyncSession, value: date) -> List[dict]:
        request_date = f'{value.month}/{value.day}/{value.year}'
        async with session.post(LUNAR_RELEASE_URL, json=request_date) as response:
            payload = loads(await response.text())
        return parse_lunar_releases(payload)

    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        dates = []
        current = start
        while current <= end:
            if current.weekday() in (1, 2):
                dates.append(current)
            current += timedelta(days=1)

        async with AsyncSession() as session:
            responses = await gather(*(
                self._fetch_date(session, value)
                for value in dates
            ))
        return [record for response in responses for record in response]


class GetComicsWeeklyProvider(ReleaseDiscoveryProvider):
    key = 'getcomics'
    ttl = 60 * 60

    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        weeks = min(50, max(1, ((end - start).days // 7) + 3))
        packs = await get_weekly_packs(weeks)
        records = []
        for pack in packs:
            if not start_date <= pack['week_date'] <= end_date:
                continue
            for item in pack['items']:
                issue_number = item.get('calculated_issue_number')
                if not isinstance(issue_number, (int, float)):
                    issue_number = None
                records.append({
                    **item,
                    'calculated_issue_number': issue_number,
                    'external_id': item['record_key'],
                    'series_year': None,
                    'cover_url': None,
                    'source_updated_at': pack['source_updated_at'],
                    'available': True
                })
        return records
