# -*- coding: utf-8 -*-

"""
Keeping library volumes up to date with new releases.

Release data is fetched for the Releases and Publishers pages and by the
release tasks, separately from the library. This finds the volumes in the
library of which the stored metadata is behind on that release data, so that
just those can be refreshed right away instead of having to wait for the
regular library update to get to them.
"""

from __future__ import annotations

from datetime import datetime
from time import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Set, Union

from backend.base.custom_exceptions import InvalidComicVineApiKey
from backend.base.logging import LOGGER
from backend.implementations.metadata_sources.base import MetadataSourceType
from backend.implementations.volumes import refresh_and_scan
from backend.internals.db import get_db

RETRY_INTERVAL = 6 * 60 * 60
"""
The minimum time in seconds between refreshes of a volume for a release that
is announced elsewhere but that the metadata source doesn't list yet.
"""

PENDING_RELEASE_MAX_AGE = 30 * 24 * 60 * 60
"""
For how many seconds after a release was expected the volume is refreshed for
it. After that, the volume is left to the regular library update.
"""

ROUNDING_MARGIN = 1
"""
Release data is stamped in whole seconds, while the refresh of a volume is
stamped exactly. Release data only counts as newer than the refresh when it is
by more than this margin in seconds, so that a refresh directly after fetching
the release data isn't mistaken for one from before it.
"""


def _volumes_behind_on_release_cache() -> Set[int]:
    """Get the volumes for which the release cache has an issue that is not
    in the library yet or that has a different store date in the library.

    A cached release only counts when it was fetched after the last refresh of
    the volume. If the volume was refreshed since then and still differs, then
    it's the cached release that is outdated.

    The library is tied to ComicVine, so only its releases can be compared.

    Returns:
        Set[int]: The IDs of the volumes.
    """
    return {
        row[0]
        for row in get_db().execute("""
            SELECT DISTINCT v.id
            FROM release_cache rc
            INNER JOIN volumes v
                ON v.comicvine_id = rc.volume_cv_id
            LEFT JOIN issues i
                ON i.comicvine_id = rc.issue_cv_id
            WHERE rc.metadata_source = ?
                AND rc.fetched_at - ? > COALESCE(v.last_cv_fetch, 0)
                AND (
                    i.id IS NULL
                    OR i.store_date IS NOT rc.store_date
                );
            """,
            (MetadataSourceType.COMICVINE.value, ROUNDING_MARGIN)
        )
    }


def _due_for_pending_release(
    last_fetch: float,
    announced_at: float,
    expected_at: float,
    now: float
) -> bool:
    """Check whether a volume should be refreshed for a release that was
    announced by a supplemental source, while the issue is not in the metadata
    of the volume yet.

    The volume is refreshed once as soon as the release is announced. If the
    metadata source doesn't list the issue yet, then it's tried again later.
    Metadata sources mostly add an issue around the date that it comes out.
    So the closer to that date, the less time there is in between tries.

    Args:
        last_fetch (float): The epoch time of the last refresh of the volume.

        announced_at (float): The epoch time at which the release was first
            seen.

        expected_at (float): The epoch time at which the issue comes out. If
            that's before it was announced, then the announcement is when
            the issue is expected.

        now (float): The current epoch time.

    Returns:
        bool: Whether the volume should be refreshed.
    """
    expected_at = max(announced_at, expected_at)
    if expected_at < now - PENDING_RELEASE_MAX_AGE:
        return False

    if last_fetch < announced_at - ROUNDING_MARGIN:
        # Not refreshed yet since the announcement
        return True

    return last_fetch <= now - max(
        RETRY_INTERVAL,
        abs(now - expected_at) / 4
    )


def _epoch_of_date(value: Union[str, None]) -> float:
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').timestamp()
    except ValueError:
        return 0.0


def _volumes_with_pending_releases(now: float) -> Set[int]:
    """Get the volumes for which a release was announced by a supplemental
    source, while the issue is not in the metadata of the volume yet.

    Args:
        now (float): The current epoch time.

    Returns:
        Set[int]: The IDs of the volumes.
    """
    return {
        volume_id
        for volume_id, last_fetch, announced_at, release_date
        in get_db().execute("""
            SELECT
                v.id, COALESCE(v.last_cv_fetch, 0),
                w.first_seen, w.release_date
            FROM pending_release_watches w
            INNER JOIN volumes v
                ON v.id = w.volume_id;
        """)
        if _due_for_pending_release(
            last_fetch,
            announced_at,
            _epoch_of_date(release_date),
            now
        )
    }


def find_volumes_with_new_releases() -> Set[int]:
    """Get the volumes in the library of which the metadata is behind on the
    release data that is stored.

    Returns:
        Set[int]: The IDs of the volumes.
    """
    return (
        _volumes_behind_on_release_cache()
        | _volumes_with_pending_releases(time())
    )


def find_pending_weekly_pack_volumes(
    packs: Iterable[Mapping[str, Any]]
) -> Set[int]:
    """Get the volumes for which a Weekly Pack has an issue that is not in the
    metadata of the volume yet, and that should be refreshed for it.

    Args:
        packs (Iterable[Mapping[str, Any]]): The Weekly Packs, with their
            items matched to the library.

    Returns:
        Set[int]: The IDs of the volumes.
    """
    # The date of the last pack that announced an issue of the volume
    announcements: Dict[int, float] = {}
    for pack in packs:
        announced_at = _epoch_of_date(pack.get('week_date'))
        for item in pack['items']:
            volume_id = item.get('local_volume_id')
            if (
                item.get('local_status') == 'metadata_pending'
                and volume_id is not None
            ):
                announcements[volume_id] = max(
                    announcements.get(volume_id, 0.0),
                    announced_at
                )

    if not announcements:
        return set()

    now = time()
    return {
        volume_id
        for volume_id, last_fetch in get_db().execute(f"""
            SELECT id, COALESCE(last_cv_fetch, 0)
            FROM volumes
            WHERE id IN ({','.join('?' * len(announcements))});
            """,
            tuple(announcements)
        )
        if _due_for_pending_release(
            last_fetch,
            announcements[volume_id],
            announcements[volume_id],
            now
        )
    }


def find_changed_publisher_volumes(
    volumes: Iterable[Mapping[str, Any]],
    fetched_at: Union[int, None]
) -> Set[int]:
    """Get the volumes in the library for which the listing of a publisher
    reports a different amount of issues than the library has.

    Args:
        volumes (Iterable[Mapping[str, Any]]): The volumes of the publisher,
            with the ID of the volume in the library under `already_added`.

        fetched_at (Union[int, None]): The epoch time at which the listing
            was fetched. Volumes that were refreshed after it are left out.

    Returns:
        Set[int]: The IDs of the volumes.
    """
    if fetched_at is None:
        return set()

    issue_counts = {
        volume['already_added']: volume['issue_count']
        for volume in volumes
        if volume.get('already_added') is not None
    }
    ids = list(issue_counts)
    result: Set[int] = set()
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        result.update(
            volume_id
            for volume_id, issue_count in get_db().execute(f"""
                SELECT v.id, COUNT(i.id)
                FROM volumes v
                LEFT JOIN issues i
                    ON i.volume_id = v.id
                WHERE v.id IN ({','.join('?' * len(batch))})
                    AND COALESCE(v.last_cv_fetch, 0) < ?
                GROUP BY v.id;
                """,
                (*batch, fetched_at - ROUNDING_MARGIN)
            )
            if issue_count != issue_counts[volume_id]
        )
    return result


def refresh_release_volumes(
    volume_ids: Iterable[int],
    stop_check: Union[Callable[[], bool], None] = None
) -> List[int]:
    """Refresh and scan volumes because of a new release, regardless of when
    they were last refreshed.

    Args:
        volume_ids (Iterable[int]): The IDs of the volumes to refresh.

        stop_check (Union[Callable[[], bool], None], optional): Called to
            check whether to stop early.
            Defaults to None.

    Returns:
        List[int]: The IDs of the volumes that were refreshed.
    """
    volume_ids = sorted(set(volume_ids))
    if not volume_ids:
        return []

    LOGGER.info(
        'Refreshing %d volume(s) with new releases: %s',
        len(volume_ids), volume_ids
    )
    try:
        refresh_and_scan(
            update_websocket=True,
            allow_skipping=False,
            volume_ids=volume_ids,
            stop_check=stop_check
        )

    except InvalidComicVineApiKey:
        return []

    return volume_ids
