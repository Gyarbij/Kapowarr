# New Releases & Discovery Feature Design

## Overview

This document outlines the design for improving how Kapowarr fetches new comics, discovers releases, and supports multiple metadata sources.

## Features

### 1. New Releases Feed
A dashboard/page showing recently released comics from metadata sources (not yet in library).

**Implementation:**
- New API endpoint: `GET /api/releases/new`
- ComicVine endpoint: `/issues` with `filter=store_date:{start}|{end}` sorted by `store_date:desc`
- Cache results for 1 hour to avoid hitting rate limits
- Filter out issues from volumes already in library (optional toggle)
- Group by week/day for display

**Database:**
```sql
CREATE TABLE IF NOT EXISTS release_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_cv_id INTEGER NOT NULL,
    volume_cv_id INTEGER NOT NULL,
    volume_title TEXT NOT NULL,
    issue_number TEXT NOT NULL,
    store_date TEXT,
    cover_date TEXT,
    cover_url TEXT,
    publisher TEXT,
    fetched_at INTEGER NOT NULL
);
CREATE INDEX idx_release_cache_store_date ON release_cache(store_date);
CREATE INDEX idx_release_cache_fetched_at ON release_cache(fetched_at);
```

### 2. Upcoming Releases (Library)
Show upcoming issues for volumes already in your library.

**Implementation:**
- Extend `refresh_and_scan` to store `store_date` for future issues
- New API endpoint: `GET /api/releases/upcoming`
- Query issues table: `WHERE store_date > date('now') ORDER BY store_date ASC`
- Show in dashboard widget and dedicated page

**Database change:**
- Already have `date` column in `issues` table (uses `cover_date` or `store_date` per settings)
- Add `store_date` column separately to always track street date

### 3. Better New Issue Detection
Faster detection of new issues for existing library volumes.

**Implementation:**
- Add `last_cv_update` timestamp to ComicVine volume metadata (track CV's update time)
- Compare local `last_cv_fetch` with CV's `date_last_updated` field
- Prioritize volumes where CV has newer data
- Add "watched publishers" that get checked more frequently
- Scheduled task: `CheckNewIssues` - lightweight check without full refresh

### 4. Publisher-Based Browsing
Browse volumes by publisher (DC, Marvel, Image, etc.).

**Implementation:**
- New API endpoint: `GET /api/publishers`
- New API endpoint: `GET /api/publishers/{id}/volumes`
- ComicVine endpoint: `/publisher/{id}` and `/volumes?filter=publisher:{id}`
- Cache popular publishers (Big 5: Marvel, DC, Image, Dark Horse, IDW)
- UI: Publisher dropdown/filter on search and library pages

### 5. Multi-Source Abstraction (Future)
Support additional metadata sources besides ComicVine.

**Design:**
```python
from abc import ABC, abstractmethod
from typing import List, Protocol

class MetadataSource(Protocol):
    """Protocol for metadata sources"""
    
    @abstractmethod
    async def search_volumes(self, query: str) -> List[VolumeMetadata]:
        ...
    
    @abstractmethod
    async def fetch_volume(self, source_id: str) -> VolumeMetadata:
        ...
    
    @abstractmethod
    async def fetch_issues(self, volume_ids: Sequence[str]) -> List[IssueMetadata]:
        ...
    
    @abstractmethod
    async def get_new_releases(
        self, 
        start_date: str, 
        end_date: str
    ) -> List[IssueMetadata]:
        ...
    
    @abstractmethod
    async def get_publishers(self) -> List[PublisherMetadata]:
        ...

# Potential sources:
# - ComicVine (current)
# - Metron (https://metron.cloud/) - free, comic-focused
# - Grand Comics Database (https://www.comics.org/)
# - League of Comic Geeks (https://leagueofcomicgeeks.com/)
# - Comic Vine alternatives
```

## Implementation Priority

1. **Phase 1: Foundation** ✅ COMPLETE
   - [x] Add TypedDicts for `PublisherMetadata` and `NewReleaseMetadata` in `definitions.py`
   - [x] Add ComicVine methods: `get_new_releases()`, `get_upcoming_releases()`, `get_recent_releases()`, `get_publishers()`, `search_publisher_volumes()`
   - [x] Add `store_date` column to issues table (migration 45)
   - [x] Add release cache table (migration 45)

2. **Phase 2: New Releases Feed** ✅ COMPLETE
   - [x] API endpoints: `/api/releases/new`, `/api/releases/upcoming`, `/api/releases/recent`
   - [x] API endpoints: `/api/publishers`, `/api/publishers/{id}/volumes`
   - [x] Scheduled task for cache refresh (`RefreshReleaseCache`)
   - [x] Basic UI page (`/releases`)

3. **Phase 3: Upcoming Releases** ✅ COMPLETE
   - [x] Add `store_date` to `IssueMetadata` and issue fetching
   - [x] API endpoint: `/api/releases/library/upcoming` for library issues
   - [x] "My Library Upcoming" view in releases page
   - [ ] Dashboard widget (future enhancement)
   - [ ] Calendar view (future enhancement)

4. **Phase 4: Publisher Browsing** ✅ COMPLETE
   - [x] Publisher browse UI page (`/publishers`)
   - [x] Browse volumes by publisher
   - [x] Publisher filter on volumes page (already existed)
   - [x] Navigation link added

5. **Phase 5: Multi-Source** (future)
   - Abstract base class
   - Metron integration
   - Source selection in settings

## ComicVine API Notes

### Relevant Endpoints
- `/issues` - List issues with filters
- `/issue/{id}` - Single issue details  
- `/publishers` - List publishers
- `/publisher/{id}` - Publisher details with volumes

### Useful Filters
- `store_date:{start}|{end}` - Issues by store date range
- `cover_date:{start}|{end}` - Issues by cover date range
- `publisher:{id}` - Volumes by publisher
- `date_last_updated:{date}|{date}` - Recently updated content

### Rate Limits
- 200 requests per resource per hour
- Use batching and caching aggressively
