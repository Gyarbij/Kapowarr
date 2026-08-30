# Releases And Publishers

The Releases page shows recent releases, upcoming releases, and upcoming issues
for monitored volumes in your library.

## Filtering And Sorting

Use one of the 7, 14, 30, 90, 180, or 365 day ranges, or choose Custom Dates.
Custom ranges can cover up to 365 days. Releases can be sorted by date, title,
or publisher. The selected release type, range, sort order, custom dates, and
"Hide In Library" setting are restored when the page is opened again.

The page initially renders 100 results. Use "Load More" to show the next 100
without making another metadata request.

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

The Volumes page has Recently Added and Recently Released filters through 365
days, together with Recently Added and Recently Released sorting. Use these to
review a library that has not been updated for an extended period.

Manually selecting Update All refreshes every applicable volume. The automatic
scheduled Update All may skip volumes that were refreshed recently. Search All
then searches monitored, missing issues using the latest stored metadata.