# -*- coding: utf-8 -*-

"""
Metadata sources for comic information.
Supports multiple backends like ComicVine, Metron, etc.
"""

from backend.implementations.metadata_sources.base import (
    MetadataSource, MetadataSourceType,
    get_available_sources, get_metadata_source)
# Import sources to register them
from backend.implementations.metadata_sources.comicvine_source import \
    ComicVineSource
from backend.implementations.metadata_sources.metron_source import MetronSource

__all__ = [
    'MetadataSource',
    'MetadataSourceType',
    'get_metadata_source',
    'get_available_sources',
    'ComicVineSource',
    'MetronSource'
]
