# -*- coding: utf-8 -*-

import re
from datetime import date, datetime, timedelta, timezone
from json import JSONDecoder, loads
from typing import Any, Dict, List, Union
from urllib.parse import urljoin, urlparse

from aiohttp import ClientError
from bs4 import BeautifulSoup

from backend.base.file_extraction import extract_filename_data
from backend.base.helpers import AsyncSession
from backend.base.logging import LOGGER
from backend.implementations.release_discovery import (
    ReleaseDiscoveryProvider,
    WeeklyDiscoveryProvider,
)
from backend.implementations.weekly_packs import get_weekly_packs
from backend.internals.settings import Settings

MARVEL_CALENDAR_URL = 'https://www.marvel.com/comics/calendar'
DC_CATALOG_URL = 'https://www.dc.com/comics'
LUNAR_RELEASE_URL = (
    'https://www.lunardistribution.com/home/instoreproducts'
)
COMIXNOW_URL = 'https://comixnow.com'
LOCG_URL = 'https://leagueofcomicgeeks.com'
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
LUNAR_CODE_REGEX = re.compile(r'^\d{4}(?P<publisher>[A-Z]{2})\d')
# Signed and graded copies of issues that came out before, mostly of other
# publishers and sold by Dynamic Forces ('DF')
LUNAR_COLLECTIBLE_REGEX = re.compile(
    r'^DF\s|\b(?:CGC|SIGNED|REMARKED)\b',
    re.IGNORECASE
)
COMIXNOW_DATE_REGEX = re.compile(
    r'DC Weekly\W+(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})'
)
# Covers other than the main one come out later than the issue itself at
# times, and would give the issue a later date
LUNAR_VARIANT_COVER_REGEX = re.compile(r'\bCVR\s+(?!A\b)[A-Z]{1,2}\b')
REPRINT_REGEX = re.compile(
    r'\bfacsimile\b|\breprint\b'
    r'|\b(?:\d+(?:st|nd|rd|th)|second|third|fourth|fifth)\s+'
    r'(?:ptg|print(?:ing)?)\b',
    re.IGNORECASE
)

# The publisher is the two letters in the product code. Only the codes that
# are of a single publisher are here. E.g. 'MP' is shared by multiple small
# publishers, so the publisher of those products is not known.
LUNAR_PUBLISHERS = {
    'DC': 'DC Comics',
    'IM': 'Image Comics',
    'DE': 'Dynamite Entertainment',
    'TN': 'Titan Comics',
    'MA': 'Mad Cave Studios',
    'BD': 'Bad Idea',
    'ZE': 'Zenescope Entertainment',
    'BM': 'Black Mask Studios'
}

# What ComixNOW lists that isn't a new issue of a series
COMIXNOW_SKIPPED_KINDS = ('book', 'facsimile', 'reprint')


def _iso_date(value: str) -> str:
    return datetime.strptime(value, '%m/%d/%Y').date().isoformat()


def _valid_external_url(url: str, hostname: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in ('http', 'https')
        and (parsed.hostname or '').lower() in (hostname, f'www.{hostname}')
    )


async def _fetch_page(
    session: AsyncSession,
    url: str,
    params: Any = None,
    missing_ok: bool = False
) -> str:
    """Fetch a page of a source.

    Args:
        session (AsyncSession): The session to make the request with.

        url (str): The URL of the page.

        params (Any, optional): The parameters of the URL.
            Defaults to None.

        missing_ok (bool, optional): Give an empty string when the source
            doesn't have the page, instead of that being a failure.
            Defaults to False.

    Raises:
        ClientError: The source didn't give the page. Raising makes that the
            last successful response is kept, instead of an error page being
            taken for there not being any releases.

    Returns:
        str: The body of the response.
    """
    async with session.get(
        url,
        params=params,
        headers=PUBLISHER_BROWSER_HEADERS
    ) as response:
        if missing_ok and response.status == 404:
            return ''
        if not response.ok:
            raise ClientError(
                f'{urlparse(url).hostname} responded with {response.status}'
            )
        return await response.text()


def _record(
    provider: str,
    external_id: str,
    external_url: str,
    publisher: str,
    title: str,
    release_date: Union[str, None],
    cover_url: Union[str, None] = None
) -> Union[dict, None]:
    if REPRINT_REGEX.search(title):
        # A reprint has the number of an issue that came out before. It isn't
        # a new issue of the series, and would be taken for one.
        return None

    parsed = extract_filename_data(
        # Everything before a slash would be taken for a folder
        title.replace('/', ' - '),
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


def _marvel_structured_releases(html: str) -> Union[Dict[str, Any], None]:
    """Get the releases that the Marvel calendar has embedded in the page as
    data. That has the date per release and whether it is a variant cover.

    Returns:
        Union[Dict[str, Any], None]: The total amount of releases under
            `total` and the ones in the page under `content`, or `None` if
            the page doesn't have the data.
    """
    marker = html.find('"allComicsReleases"')
    start = html.find('{', marker) if marker != -1 else -1
    if start == -1:
        return None
    try:
        releases, _ = JSONDecoder().raw_decode(html[start:])
    except ValueError:
        return None
    if not (
        isinstance(releases, dict)
        and isinstance(releases.get('content'), list)
    ):
        return None
    return releases


def parse_marvel_calendar(html: str) -> List[dict]:
    records = []
    seen = set()

    structured = _marvel_structured_releases(html)
    for release in (structured or {}).get('content', ()):
        if not isinstance(release, dict) or release.get('isVariant'):
            continue
        link = release.get('link')
        image = release.get('image')
        external_url = str(
            link.get('link') or '' if isinstance(link, dict) else ''
        )
        cover_url = str(
            image.get('filename') or '' if isinstance(image, dict) else ''
        )
        match = MARVEL_ISSUE_REGEX.search(external_url)
        release_date = str(release.get('releaseDate') or '')[:10]
        if (
            match is None
            or match.group('id') in seen
            or not release_date
            or not _valid_external_url(external_url, 'marvel.com')
        ):
            continue
        try:
            date.fromisoformat(release_date)
        except ValueError:
            continue
        record = _record(
            'marvel',
            match.group('id'),
            external_url,
            'Marvel Comics',
            str(release.get('headline') or ''),
            release_date,
            cover_url if cover_url.startswith('http') else None
        )
        if record is not None:
            records.append(record)
            seen.add(match.group('id'))

    if records:
        return records

    soup = BeautifulSoup(html, 'html.parser')

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
        code_match = LUNAR_CODE_REGEX.match(code.upper())
        publisher = LUNAR_PUBLISHERS.get(
            code_match.group('publisher') if code_match else ''
        )
        if publisher is None or not title or not release_value:
            continue
        if (
            LUNAR_COLLECTIBLE_REGEX.search(title)
            or LUNAR_VARIANT_COVER_REGEX.search(title)
        ):
            continue
        try:
            release_date = _iso_date(release_value)
        except ValueError:
            continue
        record = _record(
            'lunar',
            code,
            'https://www.lunardistribution.com/',
            publisher,
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


def parse_comixnow_weekly(html: str) -> List[dict]:
    """Get the new issues from a 'DC Weekly' page of ComixNOW.

    The date of the releases is taken from the page. When there is no page for
    the week that was asked for, ComixNOW gives the page of another week.
    """
    soup = BeautifulSoup(html, 'html.parser')
    date_match = COMIXNOW_DATE_REGEX.search(
        ' '.join(
            element.get_text(' ', strip=True)
            for element in (soup.title, soup.find('h1'))
            if element is not None
        )
    )
    if date_match is None:
        return []
    try:
        release_date = datetime.strptime(
            date_match.group('date'), '%B %d, %Y'
        ).date().isoformat()
    except ValueError:
        return []

    records = []
    seen = set()
    for card in soup.select('article.release-card'):
        kind = card.select_one('.release-kind')
        link = card.select_one('.release-title a[href]')
        if link is None or (
            kind is not None
            and kind.get_text(strip=True).lower() in COMIXNOW_SKIPPED_KINDS
        ):
            continue

        href_value = link.get('href')
        href = href_value if isinstance(href_value, str) else ''
        external_url = urljoin(COMIXNOW_URL, href)
        external_id = href.strip('/')
        if (
            # Collected editions are under '/books/'
            not href.startswith('/comics/')
            or external_id in seen
            or not _valid_external_url(external_url, 'comixnow.com')
        ):
            continue

        cover = card.select_one('img.release-cover[src]')
        cover_value = cover.get('src') if cover is not None else None
        record = _record(
            'comixnow',
            external_id,
            external_url,
            'DC Comics',
            ' '.join(link.get_text(' ', strip=True).split()),
            release_date,
            cover_value if isinstance(cover_value, str) else None
        )
        if record is None:
            continue
        # The link also has the year in which the series started. It's not
        # used, as it differs from the year of the volume in the library for
        # series that went back to their original numbering. Releases are
        # only matched to a volume of the same year, so those wouldn't match.
        records.append(record)
        seen.add(external_id)
    return records


def _locg_release_list(body: str) -> str:
    """Get the HTML with the list of releases out of a response of League of
    Comic Geeks. It's a field of a JSON response. When the response came
    through a browser (FlareSolverr), then that JSON is inside of a page.

    Raises:
        ValueError: The response is not a list of releases, e.g. because it's
            a page that blocks the request.

    Returns:
        str: The HTML with the list of releases.
    """
    candidates = [body]
    if not body.lstrip().startswith('{'):
        page_content = BeautifulSoup(body, 'html.parser').find('pre')
        if page_content is not None:
            candidates.append(page_content.get_text())

    for candidate in candidates:
        try:
            response = loads(candidate)
        except ValueError:
            continue
        if isinstance(response, dict) and isinstance(
            response.get('list'), str
        ):
            return response['list']

    raise ValueError('Response is not a list of releases')


def parse_locg_releases(body: str) -> List[dict]:
    soup = BeautifulSoup(_locg_release_list(body), 'html.parser')
    records = []
    seen = set()
    for entry in soup.select('li.issue[data-comic]'):
        external_id = str(entry.get('data-comic') or '')
        link = entry.select_one('.title a[href]')
        publisher = entry.select_one('.publisher')
        date_element = entry.select_one('.date[data-date]')
        if (
            not external_id
            or external_id in seen
            # Variant covers refer to the comic that they're a variant of
            or str(entry.get('data-parent') or '0') != '0'
            or link is None
            or publisher is None
            or date_element is None
        ):
            continue

        try:
            release_date = datetime.fromtimestamp(
                int(str(date_element.get('data-date'))),
                tz=timezone.utc
            ).date().isoformat()
        except (ValueError, OverflowError, OSError):
            continue

        href_value = link.get('href')
        external_url = urljoin(
            LOCG_URL,
            href_value if isinstance(href_value, str) else ''
        )
        if not _valid_external_url(external_url, 'leagueofcomicgeeks.com'):
            continue

        cover = entry.select_one('.cover img')
        cover_value = (
            cover.get('data-src') or cover.get('src')
            if cover is not None
            else None
        )
        record = _record(
            'locg',
            external_id,
            external_url,
            ' '.join(publisher.get_text(' ', strip=True).split()),
            ' '.join(link.get_text(' ', strip=True).split()),
            release_date,
            cover_value
            if isinstance(cover_value, str)
            and cover_value.startswith('http')
            else None
        )
        if record is not None:
            records.append(record)
            seen.add(external_id)
    return records


class MarvelCalendarProvider(WeeklyDiscoveryProvider):
    key = 'marvel'
    ttl = 6 * 60 * 60

    async def fetch_week(self, week_start: date, week_end: date) -> List[dict]:
        # The calendar gives a limited amount of releases per request, and
        # the latest ones first. So it has to be asked one week at a time.
        # Variant covers are not asked for, as they count towards that amount.
        async with AsyncSession() as session:
            html = await _fetch_page(
                session,
                MARVEL_CALENDAR_URL,
                {
                    'dateStart': week_start.isoformat(),
                    'dateEnd': week_end.isoformat()
                },
                # What the calendar gives for a week without releases
                missing_ok=True
            )
        if not html:
            return []

        structured = _marvel_structured_releases(html)
        if structured is None and not MARVEL_DATE_REGEX.search(html):
            # Not the calendar, e.g. a page that blocks the request. That
            # is not the same as a week without releases.
            raise ValueError('Response is not the Marvel calendar')

        total = (structured or {}).get('total')
        if (
            structured is not None
            and isinstance(total, int)
            and total > len(structured['content'])
        ):
            LOGGER.warning(
                'The Marvel calendar gave %d of the %d releases '
                'for the week of %s',
                len(structured['content']),
                total,
                week_start.isoformat()
            )
        return parse_marvel_calendar(html)


class DCCatalogProvider(ReleaseDiscoveryProvider):
    key = 'dc'
    ttl = 12 * 60 * 60

    async def fetch(self, start_date: str, end_date: str) -> List[dict]:
        async with AsyncSession() as session:
            html = await _fetch_page(session, DC_CATALOG_URL)
        return parse_dc_catalog(html)


class ComixNowProvider(WeeklyDiscoveryProvider):
    key = 'comixnow'
    ttl = 12 * 60 * 60

    async def fetch_week(self, week_start: date, week_end: date) -> List[dict]:
        release_day = week_start + timedelta(days=3)
        async with AsyncSession() as session:
            html = await _fetch_page(
                session,
                f'{COMIXNOW_URL}/dc-weekly/dc-comics-available-on-'
                f'{release_day.strftime("%B").lower()}-{release_day.day}'
                f'-{release_day.year}/'
            )
        return [
            record
            for record in parse_comixnow_weekly(html)
            # Not the page of another week
            if week_start.isoformat()
                <= record['release_date']
                <= week_end.isoformat()
        ]


class LunarReleaseProvider(WeeklyDiscoveryProvider):
    key = 'lunar'
    ttl = 12 * 60 * 60

    MAX_RELEASE_DAYS = 4
    "The maximum amount of days in a week of which the releases are fetched"

    async def _fetch_date(
        self,
        session: AsyncSession,
        value: date
    ) -> Dict[str, Any]:
        request_date = f'{value.month}/{value.day}/{value.year}'
        async with session.post(LUNAR_RELEASE_URL, json=request_date) as response:
            if not response.ok:
                raise ClientError(
                    f'Lunar responded with {response.status}'
                )
            payload = loads(await response.text())
        return payload if isinstance(payload, dict) else {}

    async def fetch_week(self, week_start: date, week_end: date) -> List[dict]:
        # Lunar only gives the releases of the exact day that is asked for,
        # together with the release days before and after it. Most releases
        # are on Wednesday, so start there and follow those to the other
        # release days of the week.
        records: List[dict] = []
        pending = [week_start + timedelta(days=3)]
        fetched = set()
        async with AsyncSession() as session:
            while pending and len(fetched) < self.MAX_RELEASE_DAYS:
                release_day = pending.pop()
                if (
                    release_day in fetched
                    or not week_start <= release_day <= week_end
                ):
                    continue
                fetched.add(release_day)

                payload = await self._fetch_date(session, release_day)
                records.extend(parse_lunar_releases(payload))

                if not payload.get('success') and len(fetched) == 1:
                    # Nothing on Wednesday. The other common day is Tuesday.
                    pending.append(week_start + timedelta(days=2))
                for neighbour in ('previous', 'next'):
                    try:
                        pending.append(datetime.strptime(
                            str(payload.get(neighbour)), '%m/%d/%Y'
                        ).date())
                    except ValueError:
                        continue
        return records


class LeagueOfComicGeeksProvider(WeeklyDiscoveryProvider):
    key = 'locg'
    ttl = 12 * 60 * 60
    concurrency = 1
    # Every request has to be handled by FlareSolverr, which takes a while
    background_only = True

    def is_available(self) -> bool:
        # The site only lets browsers through
        return bool(Settings().sv.flaresolverr_base_url)

    async def fetch_week(self, week_start: date, week_end: date) -> List[dict]:
        release_day = week_start + timedelta(days=3)
        async with AsyncSession() as session:
            body = await _fetch_page(
                session,
                f'{LOCG_URL}/comic/get_comics',
                [
                    ('list', 'releases'),
                    ('view', 'thumbs'),
                    # Only comics, so no collected editions
                    ('format[]', '1'),
                    ('date_type', 'week'),
                    ('date', release_day.strftime('%m/%d/%Y')),
                    ('order', 'alpha-asc')
                ]
            )
        return parse_locg_releases(body)


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
