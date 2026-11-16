# Releases And Publishers

The Releases page shows recent releases, upcoming releases, upcoming issues for
monitored volumes in your library, and supplemental release discovery.

## Filtering And Sorting

Use one of the 7, 14, 30, 90, 180, or 365 day ranges, or choose Custom Dates.
Custom ranges can cover up to 365 days. Releases can be sorted by date, title,
or publisher. The selected release type, range, sort order, custom dates, and
"Hide In Library" setting are restored when the page is opened again.

The page initially renders 100 results. Use "Load More" to show the next 100
without making another metadata request.

## Supplemental Discovery

Choose 'Discovery' to combine dated publisher/distributor listings with actual
GetComics availability. Records show every source that contributed to the
match, whether the issue is available, and its local status. Marvel's official
calendar, DC's catalog, Lunar's dated release data, and GetComics Weekly Packs
are supplemental sources; ComicVine or Metron remains canonical.

'Metadata Pending' means one exact local volume was found, but the announced
issue is not yet in its canonical metadata. Kapowarr refreshes that volume
right away, and again later if the metadata source doesn't list the issue yet.
When Refresh Release Discovery brings the issue in, it's searched for in the
same run if it's monitored. Kapowarr does not create a provisional issue.
Ambiguous, unmonitored, and unknown items remain review-only.

Each supplemental provider fails independently. Previously cached records from
other providers remain available if one source is blocked or temporarily
offline.

## Weekly Packs

Weekly Packs is the default Releases view. Use Releases -> Calendar for the
recent, upcoming, library, and supplemental discovery calendar. Weekly Packs
lists individual issue articles from recent GetComics posts, grouped by week
and publisher. Each week can be collapsed independently, and Expand All or
Collapse All changes every visible week.

Filter by publisher, local status, or title. 'Select Missing' selects only
exact monitored local issues without files; 'Queue Selected' revalidates every
item on the server before using the normal issue download path. An exact
unmonitored issue offers Monitor and Download. Downloaded and metadata-pending
matches link to the local volume for review.

Items that are not in the library open Add Volume with their parsed title and
year as search hints. You still choose a result from the configured canonical
ComicVine or Metron source and confirm the normal root-folder and monitoring
settings. Weekly Pack text never creates a volume or issue by itself.

Publisher-wide JPG and WebP archives can be several gigabytes. They are shown
separately as warning-labelled external links and are never queued or extracted
by the Weekly Packs workflow.

## Cached Metadata

Kapowarr stores release and publisher responses in its database. Release ranges
are fresh for 6 hours, publisher lists for 24 hours, and a publisher's volume
list for 12 hours. Overlapping release ranges reuse cached dates and fetch only
the missing part of the requested range. ComicVine and Metron have separate
cache entries.

Changing a local sort or filter does not contact the metadata source. The
Refresh button bypasses the freshness period. Concurrent refreshes for the same
data are combined into one metadata request. If a refresh fails, Kapowarr uses
a previously cached complete response when one is available.

## Catching Up A Library

The Volumes page has independent Status and Date filters. Recently Added and
Recently Released ranges cover up to 365 days and can be combined with missing
status, publisher, description, and text filters.

Manually selecting Update All refreshes every applicable volume. The automatic
scheduled Update All may skip volumes that were refreshed recently. Search
Missing searches monitored gaps using the latest stored metadata. Refresh &
Search Missing refreshes the same filtered volume snapshot first.

## Keeping Volumes Up To Date

Volumes in the library that are behind on a release don't wait for Update All.
When the release data shows a new issue, a changed store date, or an announced
issue that a volume doesn't have yet, that volume is refreshed right away. This
happens when the release tasks run and when the Releases or Weekly Packs page,
or the volumes of a publisher, are opened. In the latter case an Update New
Releases task does it in the background, after which Weekly Packs and the
library and discovery views of the calendar update themselves. See
[the implementation details](implementation_details.md#new-releases) for how
volumes are selected.