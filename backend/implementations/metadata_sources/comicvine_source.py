# -*- coding: utf-8 -*-

"""
ComicVine metadata source adapter.
Wraps the existing ComicVine implementation to conform to the MetadataSource protocol.
"""

from typing import Dict, List, Optional, Sequence, Union

from backend.base.definitions import (FilenameData, IssueMetadata,
                                      NewReleaseMetadata, PublisherMetadata,
                                      VolumeMetadata)
from backend.implementations.comicvine import ComicVine
from backend.implementations.metadata_sources.base import (MetadataSource,
                                                           MetadataSourceType,
                                                           register_source)


@register_source(MetadataSourceType.COMICVINE)
class ComicVineSource(MetadataSource):
    """
    ComicVine metadata source.
    
    Wraps the existing ComicVine class to provide the MetadataSource interface.
    ComicVine is a comprehensive comic database with volumes, issues, characters,
    and more. Requires a free API key from https://comicvine.gamespot.com/api/
    """
    
    source_type = MetadataSourceType.COMICVINE
    source_name = "ComicVine"
    requires_api_key = True
    
    def __init__(self):
        self._cv = ComicVine()
    
    def test_key(self) -> bool:
        """Test if the ComicVine API key is valid."""
        return self._cv.test_key()
    
    async def search_volumes(
        self,
        query: str,
        allow_rate_limit_reached: bool = False
    ) -> List[VolumeMetadata]:
        """Search for volumes on ComicVine."""
        return await self._cv.search_volumes(query, allow_rate_limit_reached)
    
    async def fetch_volume(
        self,
        source_id: Union[str, int]
    ) -> VolumeMetadata:
        """Fetch a single volume from ComicVine."""
        return await self._cv.fetch_volume(source_id)
    
    async def fetch_volumes(
        self,
        source_ids: Sequence[Union[str, int]]
    ) -> List[VolumeMetadata]:
        """Fetch multiple volumes from ComicVine."""
        return await self._cv.fetch_volumes(source_ids)
    
    async def fetch_issues(
        self,
        volume_source_ids: Sequence[Union[str, int]]
    ) -> List[IssueMetadata]:
        """Fetch issues for volumes from ComicVine."""
        return await self._cv.fetch_issues(volume_source_ids)
    
    async def get_new_releases(
        self,
        start_date: str,
        end_date: str,
        limit: int = 100
    ) -> List[NewReleaseMetadata]:
        """Get new releases from ComicVine."""
        return await self._cv.get_new_releases(start_date, end_date, limit)
    
    async def get_upcoming_releases(
        self,
        days_ahead: int = 30
    ) -> List[NewReleaseMetadata]:
        """Get upcoming releases from ComicVine."""
        return await self._cv.get_upcoming_releases(days_ahead)
    
    async def get_recent_releases(
        self,
        days_back: int = 7
    ) -> List[NewReleaseMetadata]:
        """Get recent releases from ComicVine."""
        return await self._cv.get_recent_releases(days_back)
    
    async def get_publishers(
        self,
        limit: int = 50
    ) -> List[PublisherMetadata]:
        """Get publishers from ComicVine."""
        return await self._cv.get_publishers(limit)
    
    async def search_publisher_volumes(
        self,
        publisher_id: int,
        limit: int = 100
    ) -> List[VolumeMetadata]:
        """Get volumes from a publisher on ComicVine."""
        return await self._cv.search_publisher_volumes(publisher_id, limit)
    
    async def filenames_to_volumes(
        self,
        filenames: List[FilenameData]
    ) -> Dict[FilenameData, Optional[VolumeMetadata]]:
        """Match filenames to ComicVine volumes."""
        return await self._cv.filenames_to_cvs(filenames)
