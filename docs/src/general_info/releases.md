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

## Browsing Publishers And Searching Issues

Publishers opens in the **Issues** view, combining publisher/distributor
announcements with issues already in your library. It does not depend on
ComicVine's series search. A newly released issue can therefore be found even
when ComicVine has not listed its series yet.

Search a title, an issue reference such as `X-Orcists #1`, an issue title,
a source-qualified issue ID such as `marvel:132266` or `comicvine:700`, or an
exact publisher issue URL. Punctuation differences such as `X-Orcists` and
`X Orcists` do not prevent a match. Issue numbers are matched exactly, so
`#1` does not match `#10`.

The publisher sidebar keeps your search in the selected publisher. Clearing
the search does not clear the publisher; use **All publishers** to search
across publishers again. Release windows cover the past 30 days, this week,
the next 60 days, or the full prefetched 60 days before and after today.
Library status, sorting and Load more also preserve the publisher and search.
The current filters are saved in the page URL and restored on reload.

Issue cards show the release date, contributing sources and local library
status. **View source** opens the publisher/distributor record.
**Open volume** opens a matched library volume. **Find library series** opens
the existing add-volume flow using the series title, not a publisher-specific
issue ID.

The **Series** view retains canonical series browsing and adding. Choose a
publisher to browse its recently updated catalogue, or search a series title
for a targeted lookup. Issue browsing remains available if canonical
credentials are missing or the series catalogue is unavailable.

Add Volume also shows a separate **Publisher and library issues** result
section. It supports the same issue references and URLs, even if the
ComicVine search returns no series or reports an invalid API key.
**Add from publisher** creates a managed volume and issue from a stored
publisher/distributor record. Choose a root folder and monitoring settings,
and optionally start the normal download search immediately. Finding a
listing alone does not add or download anything.

## Publisher And ComicVine Identities

Local volume and issue IDs identify your library entries. Source IDs are
separate, namespaced identities: for example, `marvel:132266` is not
`comicvine:132266`. A publisher-backed issue can be managed, monitored,
file-matched and searched for downloads while its ComicVine ID is absent.
Publisher feeds that only expose issue IDs keep them at issue level; issue
IDs are never passed off as series IDs.

When ComicVine catches up, reconciliation attaches its IDs to the existing
entries. Local IDs, folders, file links, monitoring, queue references and
activity history are preserved. Repeating an addition does not create another
entry. Adding a matching ComicVine series later also reuses a uniquely
verified publisher-backed entry instead of making a second volume.

The Refresh Release Discovery task checks unresolved publisher-backed series,
normally spacing retries by six hours. New publisher issues and
**Refresh & Scan** can trigger an earlier retry. On a
volume page, expand **Metadata IDs and reconciliation** to see both ID sets
and trigger a check, optionally specifying a ComicVine volume ID.

Automatic linking requires a unique title/alias, publisher and compatible
series-year/issue-date match. Ambiguous candidates and IDs already owned by
another library volume are reported for review; separate library volumes are
not silently merged. Existing source IDs are also used for matching after a
canonical title changes.

Reconciliation does not rename existing folders. Naming templates that require
a ComicVine ID use the normal `Unknown` value until that ID is available.

Adding from a publisher opts that series into publisher metadata updates.
New issues from compatible listings can then be added before ComicVine has
them, using the series' existing Monitor New Issues setting. Already matched
canonical issues can gain corroborating publisher IDs without enabling
automatic publisher-only additions to every series.

## Supplemental Discovery

Choose 'Discovery' to combine dated publisher/distributor listings with actual
GetComics availability. Records show every source that contributed to the
match, whether the issue is available, and its local status. The sources are
supplemental to canonical catalogue results. Publisher-backed entries can
operate before ComicVine is available and reconcile with it later.

| Source | Covers | Notes |
| --- | --- | --- |
| Marvel Calendar | Marvel | The official calendar. |
| DC Catalog | DC | Recent releases from the official catalog. |
| ComixNOW DC Weekly | DC | The weekly schedule of DC, months ahead. |
| Lunar Releases | DC, Image, Dynamite, Titan, Mad Cave, Bad Idea, Zenescope, Black Mask | The in-store dates of the distributor. |
| League of Comic Geeks | All publishers | Only used when [FlareSolverr](../settings/general.md#flaresolverr-base-url) is set up, as the site only lets browsers through. |
| GetComics Weekly Packs | All publishers | What is actually available to download. |

Releases are fetched before they come out: 60 days ahead, next to the past
60 days. Use Custom Dates to look at the upcoming ones. They are fetched per
week, so that only the weeks of which the releases could have changed are
fetched again. Reprints, like facsimile editions and second printings, are
left out as they would be taken for a new issue of the series. So are signed
copies and variant covers that come out later than the issue itself.

When sources have a different date for the same issue, the date of the source
that knows best is used: the publisher itself first, then its schedule or
distributor, then League of Comic Geeks, and GetComics last as that is when
the issue was made available.

League of Comic Geeks is slow to fetch from, so only the Refresh Release
Discovery task does that. The page shows what the task has fetched.

'Metadata Pending' means one exact local volume was found, but the announced
issue is not yet in its canonical metadata. Kapowarr refreshes that volume
right away, and again later if the metadata source doesn't list the issue yet;
more often as the date that it comes out gets closer. When Refresh Release
Discovery brings an issue in that is out, it's searched for in the same run if
it's monitored. Publisher-backed library entries do not have to wait for a ComicVine issue
to exist. Ambiguous and unknown matches remain review-only; unmonitored
issues are not automatically searched.

Each supplemental provider fails independently, and so does each week of a
provider. Previously fetched records remain available if a source is blocked
or temporarily offline.

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