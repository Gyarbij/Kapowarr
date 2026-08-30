# -*- coding: utf-8 -*-

"""
Metron metadata source.
Metron (https://metron.cloud/) is a free, community-driven comic database.
"""

from typing import Any, Dict, List, Optional, Sequence, Union

from backend.base.custom_exceptions import CredentialInvalid, VolumeNotMatched
from backend.base.definitions import (
    IssueMetadata,
    NewReleaseMetadata,
    PublisherMetadata,
    VolumeMetadata,
)
from backend.base.file_extraction import extract_issue_number
from backend.base.helpers import AsyncSession, force_range, normalise_string
from backend.base.logging import LOGGER
from backend.implementations.metadata_sources.base import (
    MetadataSource,
    MetadataSourceType,
    register_source,
)
from backend.internals.settings import Settings

METRON_API_URL = "https://metron.cloud/api"


@register_source(MetadataSourceType.METRON)
class MetronSource(MetadataSource):
    """
    Metron metadata source.
    
    Metron is a free, community-driven comic book database.
    It provides volume, issue, and publisher information.
    Requires username/password authentication (free account).
    
    API Documentation: https://metron.cloud/docs/
    """
    
    source_type = MetadataSourceType.METRON
    source_name = "Metron"
    requires_api_key = True  # Requires username/password
    
    def __init__(self):
        self._settings = Settings()
        self._auth = None
        self._load_credentials()
    
    def _load_credentials(self):
        """Load Metron credentials from settings."""
        # Metron uses Basic Auth with username/password
        username = self._settings.sv.metron_username
        password = self._settings.sv.metron_password
        if username and password:
            import base64
            credentials = f"{username}:{password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            self._auth = f"Basic {encoded}"
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'Kapowarr/1.0'
        }
        if self._auth:
            headers['Authorization'] = self._auth
        return headers
    
    def test_key(self) -> bool:
        """Test if Metron credentials are valid."""
        if not self._auth:
            return False
        
        try:
            from urllib.parse import urlencode
            from urllib.request import Request, urlopen

            query = urlencode({'page_size': 1})
            request = Request(
                f"{METRON_API_URL}/series/?{query}",
                headers=self._get_headers(),
                method='GET'
            )
            with urlopen(request, timeout=10) as response:
                return response.status == 200
        except Exception as e:
            LOGGER.warning(f"Metron key test failed: {e}")
            return False
    
    async def _call_api(
        self,
        session: AsyncSession,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make an API call to Metron.
        
        Args:
            session: The async session to use.
            endpoint: API endpoint (e.g., '/series/').
            params: Query parameters.
        
        Returns:
            JSON response as a dictionary.
        """
        if not self._auth:
            raise CredentialInvalid

        url = f"{METRON_API_URL}{endpoint}"
        
        async with session.get(
            url,
            headers=self._get_headers(),
            params=params or {}
        ) as response:
            if response.status == 401:
                raise CredentialInvalid
            if response.status == 429:
                LOGGER.warning("Metron rate limit reached")
            response.raise_for_status()
            return await response.json()
    
    def _format_volume(self, series_data: Dict[str, Any]) -> VolumeMetadata:
        """Format Metron series data to VolumeMetadata."""
        # Metron calls volumes "series"
        publisher = series_data.get('publisher', {})
        
        result: VolumeMetadata = {
            'comicvine_id': series_data['id'],  # Using Metron ID
            'title': normalise_string(series_data.get('name', '')),
            'year': series_data.get('year_began'),
            'volume_number': series_data.get('volume', 1) or 1,
            'cover_link': series_data.get('image') or '',
            'cover': None,
            'description': series_data.get('desc') or '',
            'site_url': f"https://metron.cloud/series/{series_data['id']}/",
            'aliases': [],
            'publisher': publisher.get('name') if publisher else None,
            'issue_count': series_data.get('issue_count', 0),
            'translated': False,
            'already_added': None,
            'issues': None
        }
        return result
    
    def _format_issue(
        self,
        issue_data: Dict[str, Any],
        series_id: int
    ) -> IssueMetadata:
        """Format Metron issue data to IssueMetadata."""
        issue_number = issue_data.get('number', '0')
        calculated = force_range(extract_issue_number(str(issue_number)))[0]
        if calculated is None:
            calculated = 0.0
        
        result: IssueMetadata = {
            'comicvine_id': issue_data['id'],  # Using Metron ID
            'volume_id': series_id,
            'issue_number': str(issue_number).replace('/', '-').strip(),
            'calculated_issue_number': calculated,
            'title': normalise_string(issue_data.get('name') or '') or None,
            'date': issue_data.get('cover_date'),
            'store_date': issue_data.get('store_date'),
            'description': issue_data.get('desc') or ''
        }
        return result
    
    async def search_volumes(
        self,
        query: str,
        allow_rate_limit_reached: bool = False
    ) -> List[VolumeMetadata]:
        """Search for series on Metron."""
        LOGGER.debug(f"Metron: Searching for '{query}'")
        
        results: List[VolumeMetadata] = []
        async with AsyncSession() as session:
            try:
                data = await self._call_api(
                    session,
                    '/series/',
                    {'name': query, 'page_size': 50}
                )
                
                for series in data.get('results', []):
                    results.append(self._format_volume(series))
                
            except PermissionError:
                LOGGER.error("Invalid Metron credentials")
                if not allow_rate_limit_reached:
                    raise
            except Exception as e:
                LOGGER.warning(f"Metron search failed: {e}")
                if not allow_rate_limit_reached:
                    raise
        
        LOGGER.debug(f"Metron: Found {len(results)} results")
        return results
    
    async def fetch_volume(
        self,
        source_id: Union[str, int]
    ) -> VolumeMetadata:
        """Fetch a single series from Metron."""
        LOGGER.debug(f"Metron: Fetching series {source_id}")
        
        async with AsyncSession() as session:
            try:
                data = await self._call_api(
                    session,
                    f'/series/{source_id}/'
                )
                return self._format_volume(data)
            except Exception as e:
                LOGGER.error(f"Metron fetch failed: {e}")
                raise VolumeNotMatched
    
    async def fetch_volumes(
        self,
        source_ids: Sequence[Union[str, int]]
    ) -> List[VolumeMetadata]:
        """Fetch multiple series from Metron."""
        results: List[VolumeMetadata] = []
        
        for source_id in source_ids:
            try:
                result = await self.fetch_volume(source_id)
                results.append(result)
            except VolumeNotMatched:
                continue
        
        return results
    
    async def fetch_issues(
        self,
        volume_source_ids: Sequence[Union[str, int]]
    ) -> List[IssueMetadata]:
        """Fetch issues for series from Metron."""
        LOGGER.debug(f"Metron: Fetching issues for {len(volume_source_ids)} series")
        
        all_issues: List[IssueMetadata] = []
        
        async with AsyncSession() as session:
            for series_id in volume_source_ids:
                try:
                    # Metron paginates issues
                    page = 1
                    while True:
                        data = await self._call_api(
                            session,
                            '/issue/',
                            {'series_id': series_id, 'page': page, 'page_size': 100}
                        )
                        
                        issues = data.get('results', [])
                        if not issues:
                            break
                        
                        for issue in issues:
                            all_issues.append(
                                self._format_issue(issue, int(series_id))
                            )
                        
                        # Check if there are more pages
                        if not data.get('next'):
                            break
                        page += 1
                        
                except Exception as e:
                    LOGGER.warning(f"Metron issue fetch failed for series {series_id}: {e}")
                    continue
        
        LOGGER.debug(f"Metron: Found {len(all_issues)} issues")
        return all_issues
    
    async def _get_paginated_results(
        self,
        session: AsyncSession,
        endpoint: str,
        params: Dict[str, Any],
        limit: int
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        page = 1
        while len(results) < limit:
            page_size = min(100, limit - len(results))
            data = await self._call_api(
                session,
                endpoint,
                {**params, 'page': page, 'page_size': page_size}
            )
            page_results = data.get('results', [])
            if not page_results:
                break
            results.extend(page_results)
            if not data.get('next') or len(page_results) < page_size:
                break
            page += 1
        return results

    async def get_new_releases(
        self,
        start_date: str,
        end_date: str,
        limit: int = 100
    ) -> List[NewReleaseMetadata]:
        """Get new releases from Metron."""
        LOGGER.debug(f"Metron: Fetching releases from {start_date} to {end_date}")
        
        releases: List[NewReleaseMetadata] = []
        
        async with AsyncSession() as session:
            try:
                data = await self._get_paginated_results(
                    session,
                    '/issue/',
                    {
                        'store_date_range_after': start_date,
                        'store_date_range_before': end_date
                    },
                    limit
                )
                
                for issue in data:
                    series = issue.get('series', {})
                    release: NewReleaseMetadata = {
                        'issue_cv_id': issue['id'],
                        'volume_cv_id': series.get('id', 0),
                        'volume_title': normalise_string(series.get('name', 'Unknown')),
                        'issue_number': str(issue.get('number', '0')).replace('/', '-').strip(),
                        'calculated_issue_number': force_range(
                            extract_issue_number(str(issue.get('number', '0')))
                        )[0] or 0.0,
                        'store_date': issue.get('store_date'),
                        'cover_date': issue.get('cover_date'),
                        'cover_url': issue.get('image'),
                        'publisher': series.get('publisher', {}).get('name') if series.get('publisher') else None,
                        'in_library': False,
                        'volume_id': None
                    }
                    releases.append(release)
                    
            except Exception:
                LOGGER.warning("Metron releases fetch failed")
                raise
        
        LOGGER.debug(f"Metron: Found {len(releases)} releases")
        return releases
    
    async def get_publishers(
        self,
        limit: int = 50
    ) -> List[PublisherMetadata]:
        """Get publishers from Metron."""
        LOGGER.debug("Metron: Fetching publishers")
        
        publishers: List[PublisherMetadata] = []
        
        async with AsyncSession() as session:
            try:
                data = await self._get_paginated_results(
                    session,
                    '/publisher/',
                    {},
                    limit
                )
                
                for pub in data:
                    publisher: PublisherMetadata = {
                        'comicvine_id': pub['id'],
                        'name': pub['name'],
                        'site_url': f"https://metron.cloud/publisher/{pub['id']}/",
                        'volume_count': 0  # Not available in list response
                    }
                    publishers.append(publisher)
                    
            except Exception:
                LOGGER.warning("Metron publishers fetch failed")
                raise
        
        LOGGER.debug(f"Metron: Found {len(publishers)} publishers")
        return publishers
    
    async def search_publisher_volumes(
        self,
        publisher_id: int,
        limit: int = 100
    ) -> List[VolumeMetadata]:
        """Get series from a publisher on Metron."""
        LOGGER.debug(f"Metron: Fetching series for publisher {publisher_id}")
        
        results: List[VolumeMetadata] = []
        
        async with AsyncSession() as session:
            try:
                data = await self._get_paginated_results(
                    session,
                    '/series/',
                    {'publisher_id': publisher_id},
                    limit
                )
                
                for series in data:
                    results.append(self._format_volume(series))
                    
            except Exception:
                LOGGER.warning("Metron publisher volumes fetch failed")
                raise
        
        LOGGER.debug(f"Metron: Found {len(results)} series")
        return results
