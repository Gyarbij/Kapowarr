# -*- coding: utf-8 -*-

"""
Abstract base class and protocol for metadata sources.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Optional, Sequence, Type, Union

from backend.base.definitions import (FilenameData, IssueMetadata,
                                      NewReleaseMetadata, PublisherMetadata,
                                      VolumeMetadata)


class MetadataSourceType(str, Enum):
    """Supported metadata source types."""
    COMICVINE = "comicvine"
    METRON = "metron"


class MetadataSource(ABC):
    """
    Abstract base class for metadata sources.
    
    All metadata sources must implement these methods to provide
    comic volume and issue information.
    """
    
    # Source identification
    source_type: MetadataSourceType
    source_name: str
    requires_api_key: bool = True
    
    @abstractmethod
    def test_key(self) -> bool:
        """Test if the API key is valid.
        
        Returns:
            bool: True if key is valid, False otherwise.
        """
        ...
    
    @abstractmethod
    async def search_volumes(
        self,
        query: str,
        allow_rate_limit_reached: bool = False
    ) -> List[VolumeMetadata]:
        """Search for volumes matching a query.
        
        Args:
            query: Search query string.
            allow_rate_limit_reached: If True, return partial results
                when rate limit is hit.
        
        Returns:
            List of matching volume metadata.
        """
        ...
    
    @abstractmethod
    async def fetch_volume(
        self,
        source_id: Union[str, int]
    ) -> VolumeMetadata:
        """Fetch detailed metadata for a single volume.
        
        Args:
            source_id: The source-specific ID of the volume.
        
        Returns:
            Volume metadata.
        """
        ...
    
    @abstractmethod
    async def fetch_volumes(
        self,
        source_ids: Sequence[Union[str, int]]
    ) -> List[VolumeMetadata]:
        """Fetch metadata for multiple volumes.
        
        Args:
            source_ids: Sequence of source-specific volume IDs.
        
        Returns:
            List of volume metadata.
        """
        ...
    
    @abstractmethod
    async def fetch_issues(
        self,
        volume_source_ids: Sequence[Union[str, int]]
    ) -> List[IssueMetadata]:
        """Fetch all issues for the given volumes.
        
        Args:
            volume_source_ids: Sequence of volume IDs to fetch issues for.
        
        Returns:
            List of issue metadata for all requested volumes.
        """
        ...
    
    # Optional methods with default implementations
    
    async def get_new_releases(
        self,
        start_date: str,
        end_date: str,
        limit: int = 100
    ) -> List[NewReleaseMetadata]:
        """Get new releases within a date range.
        
        Args:
            start_date: Start of date range (YYYY-MM-DD).
            end_date: End of date range (YYYY-MM-DD).
            limit: Maximum number of results.
        
        Returns:
            List of new release metadata.
        """
        # Default: not supported
        return []
    
    async def get_upcoming_releases(
        self,
        days_ahead: int = 30
    ) -> List[NewReleaseMetadata]:
        """Get upcoming releases for the next N days.
        
        Args:
            days_ahead: Number of days ahead to look.
        
        Returns:
            List of upcoming release metadata.
        """
        from datetime import datetime, timedelta
        
        today = datetime.now().strftime('%Y-%m-%d')
        future = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        return await self.get_new_releases(today, future)
    
    async def get_recent_releases(
        self,
        days_back: int = 7
    ) -> List[NewReleaseMetadata]:
        """Get releases from the past N days.
        
        Args:
            days_back: Number of days back to look.
        
        Returns:
            List of recent release metadata.
        """
        from datetime import datetime, timedelta
        
        today = datetime.now().strftime('%Y-%m-%d')
        past = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        return await self.get_new_releases(past, today)
    
    async def get_publishers(
        self,
        limit: int = 50
    ) -> List[PublisherMetadata]:
        """Get list of publishers.
        
        Args:
            limit: Maximum number of results.
        
        Returns:
            List of publisher metadata.
        """
        # Default: not supported
        return []
    
    async def search_publisher_volumes(
        self,
        publisher_id: int,
        limit: int = 100
    ) -> List[VolumeMetadata]:
        """Get volumes from a specific publisher.
        
        Args:
            publisher_id: The source-specific publisher ID.
            limit: Maximum number of results.
        
        Returns:
            List of volume metadata from the publisher.
        """
        # Default: not supported
        return []
    
    async def filenames_to_volumes(
        self,
        filenames: List[FilenameData]
    ) -> Dict[FilenameData, Optional[VolumeMetadata]]:
        """Match filenames to volumes.
        
        Args:
            filenames: List of parsed filename data.
        
        Returns:
            Dictionary mapping filenames to their matched volumes (or None).
        """
        # Default implementation: search for each filename
        results: Dict[FilenameData, Optional[VolumeMetadata]] = {}
        for filename in filenames:
            try:
                search_results = await self.search_volumes(
                    filename.series,
                    allow_rate_limit_reached=True
                )
                if search_results:
                    # Just take first result as default behavior
                    results[filename] = search_results[0]
                else:
                    results[filename] = None
            except Exception:
                results[filename] = None
        return results


# Registry of metadata sources
_source_registry: Dict[MetadataSourceType, Type[MetadataSource]] = {}


def register_source(source_type: MetadataSourceType):
    """Decorator to register a metadata source class."""
    def decorator(cls: Type[MetadataSource]):
        _source_registry[source_type] = cls
        return cls
    return decorator


def get_metadata_source(
    source_type: MetadataSourceType = MetadataSourceType.COMICVINE
) -> MetadataSource:
    """Get an instance of the specified metadata source.
    
    Args:
        source_type: The type of metadata source to get.
    
    Returns:
        An instance of the metadata source.
    
    Raises:
        ValueError: If the source type is not registered.
    """
    if source_type not in _source_registry:
        raise ValueError(f"Unknown metadata source type: {source_type}")
    return _source_registry[source_type]()


def get_available_sources() -> List[Dict[str, any]]:
    """Get information about all available metadata sources.
    
    Returns:
        List of dicts with source info (type, name, requires_api_key).
    """
    sources = []
    for source_type, source_cls in _source_registry.items():
        sources.append({
            'type': source_type.value,
            'name': getattr(source_cls, 'source_name', source_type.value),
            'requires_api_key': getattr(source_cls, 'requires_api_key', True)
        })
    return sources
