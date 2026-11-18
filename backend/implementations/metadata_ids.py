# -*- coding: utf-8 -*-

from asyncio import run
from datetime import date
from json import dumps, loads
from time import time
from typing import Iterable, List, Optional

from backend.base.custom_exceptions import (CVRateLimitReached,
                                            InvalidComicVineApiKey,
                                            InvalidKeyValue, VolumeNotMatched)
from backend.base.definitions import (ActivityCategory, ActivityEventType,
                                      IssueMetadata, MetadataSourceID,
                                      MonitorScheme, VolumeMetadata)
from backend.base.logging import LOGGER
from backend.features.activity_history import record_activity
from backend.implementations.comicvine import ComicVine
from backend.implementations.matching import match_title
from backend.implementations.release_discovery import _publisher_family
from backend.internals.db import get_db

PUBLISHER_SOURCES = frozenset(('marvel', 'dc', 'comixnow', 'lunar', 'locg'))
RECONCILIATION_INTERVAL = 6 * 60 * 60


def bind_source_id(
    entity: str, local_id: int, identity: MetadataSourceID
) -> None:
    if entity not in ('volume', 'issue'):
        raise ValueError(f'Unsupported metadata entity: {entity}')
    source = identity['source']
    source_id = str(identity['source_id'])
    if source not in PUBLISHER_SOURCES | {'comicvine'} or not source_id:
        raise InvalidKeyValue('source_id', source_id)
    table = f'{entity}_source_ids'
    owner_column = f'{entity}_id'
    cursor = get_db()
    owner = cursor.execute(
        f'SELECT {owner_column} FROM {table} WHERE source = ? AND source_id = ?;',
        (source, source_id)
    ).fetchone()
    if owner is not None and owner[0] != local_id:
        raise InvalidKeyValue('source_id',
     f'{source}:{source_id} is already linked')
    result = cursor.execute(f"""
        INSERT INTO {table}(
            {owner_column}, source, source_id, source_url, matched_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source, source_id) DO UPDATE SET
            source_url = COALESCE(excluded.source_url, {table}.source_url),
            matched_at = excluded.matched_at
        WHERE {table}.{owner_column} = excluded.{owner_column};
    """, (local_id, source, source_id, identity.get('source_url'), round(time())))
    if result.rowcount != 1:
        raise InvalidKeyValue('source_id',
     f'{source}:{source_id} is already linked')


def comicvine_identity(source_id: int) -> MetadataSourceID:
    return {
        'source': 'comicvine', 'source_id': str(source_id), 'source_url': None
    }


def _compatible_issue_dates(expected: Optional[str], stored: Optional[str],
                            cover: Optional[str]) -> bool:
    if not expected or not (stored or cover):
        return True
    if stored:
        return abs(
            (date.fromisoformat(expected) -
     date.fromisoformat(stored)).days) <= 7
    return abs(int(expected[:4]) - int(cover[:4])) <= 1


def upsert_issue_metadata(
    volume_id: int, metadata: IssueMetadata, monitored: bool
) -> int:
    """Enrich an issue in place; never replace the local ID or monitoring."""
    cursor = get_db()
    identities = list(metadata.get('source_ids', ()))
    cv_id = metadata['comicvine_id']
    if cv_id is not None:
        identities.append(comicvine_identity(cv_id))
    owners = set()
    for identity in identities:
        row = cursor.execute("""
            SELECT i.id, i.volume_id
            FROM issue_source_ids s INNER JOIN issues i ON i.id = s.issue_id
            WHERE s.source = ? AND s.source_id = ?;
        """, (identity['source'], identity['source_id'])).fetchone()
        if row is not None:
            if row[1] != volume_id:
                raise InvalidKeyValue(
                    'source_id', 'Issue belongs to another volume')
            owners.add(row[0])
    if cv_id is not None:
        row = cursor.execute(
            'SELECT id, volume_id FROM issues WHERE comicvine_id = ?;', (cv_id,)
        ).fetchone()
        if row is not None:
            if row[1] != volume_id:
                raise InvalidKeyValue('comicvine_id', cv_id)
            owners.add(row[0])
    if not owners and metadata['calculated_issue_number'] is not None:
        candidates = cursor.execute("""
            SELECT id, comicvine_id, store_date, date FROM issues
            WHERE volume_id = ? AND calculated_issue_number = ?;
        """, (volume_id, metadata['calculated_issue_number'])).fetchall()
        # Two distinct confirmed CV issues can legitimately have the same
        # number.
        candidates = [
            row for row in candidates
            if cv_id is None or row[1] is None or row[1] == cv_id
        ]
        if candidates and not any(
            _compatible_issue_dates(
                metadata.get('store_date') or metadata['date'], row[2], row[3]
            )
            for row in candidates
        ):
            raise InvalidKeyValue('issue_number',
     'Issue date conflicts with existing metadata')
        candidates = [
            row for row in candidates
            if _compatible_issue_dates(
                metadata.get('store_date') or metadata['date'], row[2], row[3]
            )
        ]
        owners.update(row[0] for row in candidates)
    if len(owners) > 1:
        raise InvalidKeyValue('issue_number', 'Ambiguous issue identity')

    issue_id = next(iter(owners), None)
    values = {
        'volume_id': volume_id,
        'comicvine_id': cv_id,
        'issue_number': metadata['issue_number'],
        'calculated_issue_number': metadata['calculated_issue_number'] or 0.0,
        'title': metadata['title'],
        'date': metadata['date'],
        'store_date': metadata.get('store_date'),
        'description': metadata['description'],
        'monitored': monitored
    }
    if issue_id is None:
        issue_id = cursor.execute("""
            INSERT INTO issues(
                volume_id, comicvine_id, issue_number, calculated_issue_number,
                title, date, store_date, description, monitored
            ) VALUES (
                :volume_id, :comicvine_id, :issue_number, :calculated_issue_number,
                :title, :date, :store_date, :description, :monitored
            );
        """, values).lastrowid
    else:
        cursor.execute("""
            UPDATE issues SET
                comicvine_id = COALESCE(:comicvine_id, comicvine_id),
                issue_number = :issue_number,
                calculated_issue_number = :calculated_issue_number,
                title = COALESCE(:title, title),
                date = CASE
                    WHEN :comicvine_id IS NULL AND comicvine_id IS NOT NULL
                    THEN date ELSE COALESCE(:date, date) END,
                store_date = COALESCE(:store_date, store_date),
                description = COALESCE(NULLIF(:description, ''), description)
            WHERE id = :id;
        """, {**values, 'id': issue_id})
    for identity in identities:
        bind_source_id('issue', issue_id, identity)
    return issue_id


def get_metadata_identity_data(volume_id: int) -> dict:
    cursor = get_db()
    volumes = cursor.execute("""
        SELECT source, source_id, source_url FROM volume_source_ids
        WHERE volume_id = ? ORDER BY source, source_id;
    """, (volume_id,)).fetchalldict()
    issues = cursor.execute("""
        SELECT i.issue_id, s.issue_number, i.source, i.source_id, i.source_url
        FROM issue_source_ids i INNER JOIN issues s ON s.id = i.issue_id
        WHERE s.volume_id = ? ORDER BY s.calculated_issue_number, i.source;
    """, (volume_id,)).fetchalldict()
    state = cursor.execute("""
        SELECT status, checked_at, candidate_ids, message
        FROM metadata_reconciliation WHERE volume_id = ?;
    """, (volume_id,)).fetchonedict()
    if state is not None:
        state['candidate_ids'] = loads(state['candidate_ids'])
    pending = cursor.execute("""
        SELECT COUNT(*) FROM issues
        WHERE volume_id = ? AND comicvine_id IS NULL;
    """, (volume_id,)).fetchone()[0]
    cover = cursor.execute("""
        SELECT d.cover_url
        FROM issues i INNER JOIN issue_source_ids s ON s.issue_id = i.id
        INNER JOIN release_discovery d
            ON d.provider = s.source AND d.external_id = s.source_id
        WHERE i.volume_id = ? AND d.cover_url IS NOT NULL
        ORDER BY i.calculated_issue_number LIMIT 1;
    """, (volume_id,)).fetchone()
    return {
        'volumes': volumes, 'issues': issues, 'reconciliation': state,
        'pending_issues': pending, 'cover_url': cover[0] if cover else None
    }


def _record_reconciliation(
    volume_id: int,
    status: str,
    candidates: Iterable[int] = (),
    message: str = '') -> dict:
    result = {
        'status': status, 'checked_at': round(time()),
        'candidate_ids': list(candidates), 'message': message
    }
    get_db().execute("""
        INSERT INTO metadata_reconciliation(
            volume_id, status, checked_at, candidate_ids, message
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(volume_id) DO UPDATE SET
            status = excluded.status, checked_at = excluded.checked_at,
            candidate_ids = excluded.candidate_ids, message = excluded.message;
    """, (volume_id, status, result['checked_at'],
          dumps(result['candidate_ids']), message))
    return result


def _same_series(volume: dict, candidate: dict) -> bool:
    return (
        _publisher_family(volume.get('publisher'))
        == _publisher_family(candidate.get('publisher'))
        and bool(_publisher_family(volume.get('publisher')))
        and (volume.get('year') is None or candidate.get('year') == volume['year'])
        and any(
            match_title(title, alternative)
            for title in (volume['title'], volume.get('alt_title') or '')
            for alternative in (candidate['title'], *(candidate.get('aliases') or ()))
            if title and alternative
        )
    )


def reconcile_comicvine(
    volume_id: int,
    candidate_id: Optional[int] = None,
    client: Optional[ComicVine] = None,
    metadata: Optional[VolumeMetadata] = None
) -> dict:
    from backend.implementations.volumes import Volume

    volume = vars(Volume(volume_id, check_existence=True).get_data())
    cv = client or ComicVine()
    if volume['comicvine_id'] is not None:
        candidate_ids = [volume['comicvine_id']]
        if candidate_id is not None and candidate_id != volume['comicvine_id']:
            raise InvalidKeyValue('comicvine_id', candidate_id)
    elif candidate_id is not None:
        candidate_ids = [candidate_id]
    else:
        candidates = run(cv.search_volumes(volume['title']))
        candidate_ids = sorted({
            candidate['comicvine_id']
            for candidate in candidates
            if candidate['comicvine_id'] is not None
            and _same_series(volume, candidate)
        })
    if len(candidate_ids) != 1:
        return _record_reconciliation(
            volume_id,
            'ambiguous' if candidate_ids else 'pending',
            candidate_ids,
            'No unique ComicVine series match')
    cv_id = candidate_ids[0]
    owner = get_db().execute(
        'SELECT id FROM volumes WHERE comicvine_id = ? AND id != ?;',
        (cv_id, volume_id)
    ).fetchone()
    if owner is not None:
        return _record_reconciliation(
            volume_id, 'conflict', [cv_id],
            f'ComicVine series already belongs to local volume {owner[0]}'
        )
    metadata = metadata or run(cv.fetch_volume(cv_id))
    if metadata['comicvine_id'] != cv_id:
        raise InvalidKeyValue(
            'comicvine_id',
            'Fetched series ID does not match')
    if not _same_series(volume, metadata):
        raise InvalidKeyValue('comicvine_id', 'Series identity does not match')
    if len(metadata['issues'] or ()) == metadata['issue_count']:
        confirmed_ids = {
            item['comicvine_id'] for item in metadata['issues'] or ()
        }
        linked_issues = get_db().execute("""
            SELECT i.comicvine_id FROM issues i
            WHERE i.volume_id = ? AND i.comicvine_id IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM issue_source_ids s
                    WHERE s.issue_id = i.id AND s.source != 'comicvine'
                );
        """, (volume_id,)).fetchall()
        if any(row[0] not in confirmed_ids for row in linked_issues):
            return _record_reconciliation(
                volume_id,
                'conflict',
                [cv_id],
                'ComicVine omitted a publisher-linked issue; retained for review')
    local_dates = get_db().execute("""
        SELECT calculated_issue_number, COALESCE(store_date, date)
        FROM issues WHERE volume_id = ? AND comicvine_id IS NULL;
    """, (volume_id,)).fetchall()
    date_matches = False
    for number, expected_date in local_dates:
        same_number = [
            item for item in metadata['issues'] or ()
            if item['calculated_issue_number'] == number
        ]
        dated = [item for item in same_number if item.get(
            'store_date') or item['date']]
        if expected_date and dated:
            compatible = [
                item for item in dated
                if (
                    abs((date.fromisoformat(item['store_date'])
                         - date.fromisoformat(expected_date)).days) <= 7
                    if item.get('store_date') else
                    volume['year'] is not None
                    and abs(int(item['date'][:4]) - int(expected_date[:4])) <= 1
                )
            ]
            if not compatible:
                return _record_reconciliation(
                    volume_id, 'ambiguous', [cv_id],
                    'Issue release years conflict with publisher metadata'
                )
            date_matches = True
    if volume['year'] is None and local_dates and not date_matches:
        return _record_reconciliation(
            volume_id, 'pending', [cv_id],
            'Waiting for an issue/date match to confirm the series'
        )

    cursor = get_db()
    # All network work is complete before opening the write transaction.
    with cursor:
        cursor.execute("""
            UPDATE volumes SET comicvine_id = ?,
                alt_title = COALESCE(alt_title, CASE WHEN title != ? THEN title END),
                title = ?, year = ?,
                publisher = ?, description = COALESCE(NULLIF(?, ''), description),
                site_url = ?
            WHERE id = ?;
        """, (cv_id, metadata['title'], metadata['title'], metadata['year'], metadata['publisher'],
              metadata['description'], metadata['site_url'], volume_id))
        bind_source_id('volume', volume_id, comicvine_identity(cv_id))
        for issue in metadata['issues'] or ():
            upsert_issue_metadata(
                volume_id, issue, volume['monitor_new_issues'])
        pending_after = cursor.execute("""
            SELECT COUNT(*) FROM issues
            WHERE volume_id = ? AND comicvine_id IS NULL;
        """, (volume_id,)).fetchone()[0]
        if volume['comicvine_id'] is None or pending_after < len(local_dates):
            record_activity(
                ActivityCategory.VOLUME, ActivityEventType.VOLUME_UPDATED,
                'Linked ComicVine and publisher metadata',
                volume_id=volume_id, origin='system',
                details={
                    'comicvine_id': cv_id,
                    'linked_publisher_issues': len(local_dates) - pending_after
                },
                cursor=cursor
            )
        if len(metadata['issues'] or ()) == metadata['issue_count']:
            cursor.execute(
                'UPDATE volumes SET last_cv_fetch = ? WHERE id = ?;',
                (time(), volume_id)
            )
        if metadata.get('cover') is not None:
            cursor.execute(
                'UPDATE volumes_covers SET cover = ? WHERE volume_id = ?;',
                (metadata['cover'], volume_id)
            )
        return _record_reconciliation(
            volume_id, 'linked', [cv_id], 'ComicVine and publisher IDs linked'
        )


def _publisher_issue(record: dict) -> IssueMetadata:
    return {
        'comicvine_id': None, 'volume_id': None,
        'issue_number': f'{record["issue_number"]:g}',
        'calculated_issue_number': record['issue_number'],
        'title': None, 'date': record['release_date'],
        'store_date': record['release_date'], 'description': '',
        'source_ids': [{
            'source': record['provider'], 'source_id': record['external_id'],
            'source_url': record['external_url']
        }]
    }


def _publisher_issue_matches_local(record: dict, issue_id: int) -> bool:
    row = get_db().execute("""
        SELECT v.year, i.store_date, i.date
        FROM issues i INNER JOIN volumes v ON v.id = i.volume_id
        WHERE i.id = ?;
    """, (issue_id,)).fetchone()
    if row is None:
        return False
    if record.get(
        'series_year') is not None and record['series_year'] == row[0]:
        return True
    return bool(
        record.get('release_date') and (row[1] or row[2])
        and _compatible_issue_dates(record['release_date'], row[1], row[2])
    )


def add_publisher_issue(
    source: str, source_id: str, root_folder_id: int,
    monitored: bool = True, monitor_new_issues: bool = True,
    auto_search: bool = False
) -> int:
    from backend.implementations.volumes import Library, Volume

    if source not in PUBLISHER_SOURCES:
        raise InvalidKeyValue('source', source)
    cursor = get_db()
    record = cursor.execute("""
        SELECT * FROM release_discovery
        WHERE provider = ? AND external_id = ?;
    """, (source, source_id)).fetchonedict()
    if record is None or record['issue_number'] is None:
        raise InvalidKeyValue('source_id', source_id)
    existing = cursor.execute("""
        SELECT i.volume_id FROM issue_source_ids s
        INNER JOIN issues i ON i.id = s.issue_id
        WHERE s.source = ? AND s.source_id = ?;
    """, (source, source_id)).fetchone()
    if existing is not None:
        return existing[0]

    candidate = {
        'title': record['series_title'], 'year': record['series_year'],
        'publisher': record['publisher']
    }
    matches = [
        row
        for row in cursor.execute(
            'SELECT id, title, alt_title, year, publisher FROM volumes;'
        ).fetchalldict()
        if _same_series(candidate, {
            'title': row['title'], 'year': row['year'],
            'publisher': row['publisher'],
            'aliases': [row['alt_title']] if row['alt_title'] else []
        })
    ]
    if len(matches) > 1:
        raise InvalidKeyValue('source_id',
     'Multiple library series match; review required')
    issue = _publisher_issue(record)
    if matches:
        volume_id = matches[0]['id']
        volume = Volume(volume_id).get_data()
        if record['series_year'] is None:
            existing_issues = cursor.execute("""
                SELECT id FROM issues
                WHERE volume_id = ? AND calculated_issue_number = ?;
            """, (volume_id, record['issue_number'])).fetchall()
            enabled = cursor.execute(
                'SELECT 1 FROM metadata_reconciliation WHERE volume_id = ?;',
                (volume_id,)
            ).fetchone()
            if (
                existing_issues and not any(
                    _publisher_issue_matches_local(record, row[0])
                    for row in existing_issues
                )
                or not existing_issues and enabled is None
            ):
                raise InvalidKeyValue(
                    'source_id', 'Publisher series year/date is inconclusive; review required')
        with cursor:
            local_issue_id = upsert_issue_metadata(
                volume_id, issue, volume.monitor_new_issues
            )
            record_activity(
                ActivityCategory.VOLUME, ActivityEventType.VOLUME_UPDATED,
                f'Linked publisher metadata: {source}:{source_id}',
                volume_id=volume_id, issue_id=local_issue_id, origin='user',
                details={'source': source, 'source_id': source_id},
                cursor=cursor
            )
            _record_reconciliation(
                volume_id,
                'pending',
                message='Publisher metadata enabled')
            cursor.execute(
                'UPDATE metadata_reconciliation SET checked_at = 0 WHERE volume_id = ?;',
                (volume_id,)
            )
    else:
        metadata: VolumeMetadata = {
            'comicvine_id': None, 'title': record['series_title'],
            'year': record['series_year'], 'volume_number': 1,
            'publisher': record['publisher'], 'description': '',
            'site_url': record['external_url'] or '', 'aliases': [],
            'cover_link': record['cover_url'] or '', 'cover': None,
            'issues': [issue], 'issue_count': 1, 'already_added': None,
            'translated': False
        }
        volume_id = Library.add_metadata(
            metadata, root_folder_id, monitored, MonitorScheme.ALL,
            monitor_new_issues, auto_search=False
        )
    if auto_search:
        from backend.features.tasks import AutoSearchVolume, TaskHandler
        TaskHandler().add(AutoSearchVolume(volume_id))
    return volume_id


def publisher_volumes_matching(metadata: VolumeMetadata) -> List[int]:
    return [
        row['id']
        for row in get_db().execute("""
            SELECT id, title, alt_title, year, publisher
            FROM volumes WHERE comicvine_id IS NULL;
        """).fetchalldict()
        if _same_series(row, metadata)
    ]


def sync_discovery_ids(records: List[dict]) -> bool:
    """Attach corroborating IDs; add new publisher issues only for opted-in series."""
    cursor = get_db()
    enabled = {
        row[0] for row in cursor.execute(
            'SELECT volume_id FROM metadata_reconciliation;'
        )
    }
    changed = False
    with cursor:
        for record in records:
            volume_id = record.get('local_volume_id')
            if volume_id is None or record.get('local_status') == 'ambiguous':
                continue
            for source in record.get('provenance', ()):
                if source['provider'] not in PUBLISHER_SOURCES or not source.get(
                    'external_id'):
                    continue
                issue_id = record.get('local_issue_id')
                identity: MetadataSourceID = {
                    'source': source['provider'],
                    'source_id': str(source['external_id']),
                    'source_url': source.get('external_url')
                }
                if issue_id is not None:
                    known = cursor.execute("""
                        SELECT issue_id FROM issue_source_ids
                        WHERE source = ? AND source_id = ?;
                    """, (identity['source'], identity['source_id'])).fetchone()
                    if known is None and not _publisher_issue_matches_local(
                        record, issue_id):
                        LOGGER.warning(
                            'Not linking %s:%s: insufficient issue year/date evidence',
                            identity['source'],
                            identity['source_id'])
                        continue
                    local_issue = cursor.execute("""
                            SELECT comicvine_id, store_date, calculated_issue_number
                            FROM issues WHERE id = ?;
                    """, (issue_id,)).fetchone()
                    if (
                            local_issue is not None and local_issue[0] is None
                            and record.get('release_date_provider') == source['provider']
                    ):
                        raw = cursor.execute("""
                                SELECT * FROM release_discovery
                                WHERE provider = ? AND external_id = ?;
                            """, (identity['source'], identity['source_id'])).fetchonedict()
                        if raw is not None and (
                            local_issue[1] != raw['release_date']
                            or local_issue[2] != raw['issue_number']
                        ):
                            upsert_issue_metadata(
                                volume_id, _publisher_issue(raw), False)
                            changed = True
                    bind_source_id('issue', issue_id, identity)
                elif volume_id in enabled:
                    raw = cursor.execute("""
                        SELECT * FROM release_discovery
                        WHERE provider = ? AND external_id = ?;
                    """, (identity['source'], identity['source_id'])).fetchonedict()
                    if raw is None or raw['issue_number'] is None:
                        continue
                    monitored = cursor.execute(
                        'SELECT monitor_new_issues FROM volumes WHERE id = ?;',
                        (volume_id,)
                    ).fetchone()[0]
                    upsert_issue_metadata(
                        volume_id, _publisher_issue(raw), monitored)
                    cursor.execute("""
                        UPDATE metadata_reconciliation
                        SET status = 'pending', checked_at = 0
                        WHERE volume_id = ?;
                    """, (volume_id,))
                    changed = True
    return changed


def pending_reconciliation_volumes(
    volume_ids: Optional[List[int]] = None, force: bool = False
) -> List[int]:
    candidates = get_db().execute("""
        SELECT v.id, r.checked_at
        FROM volumes v INNER JOIN metadata_reconciliation r ON r.volume_id = v.id
        WHERE v.comicvine_id IS NULL
            OR EXISTS (
                SELECT 1 FROM issues i
                WHERE i.volume_id = v.id AND i.comicvine_id IS NULL
            );
    """).fetchall()
    selected = None if volume_ids is None else set(volume_ids)
    return [
        volume_id for volume_id, checked_at in candidates
        if (selected is None or volume_id in selected)
        and (force or checked_at <= time() - RECONCILIATION_INTERVAL)
    ]


def refresh_publisher_volumes(
    volume_ids: Optional[List[int]] = None, force: bool = False
) -> List[int]:
    candidates = pending_reconciliation_volumes(volume_ids, force)
    attempted = []
    for volume_id in candidates:
        attempted.append(volume_id)
        try:
            LOGGER.info(
                'Reconciling publisher metadata for volume %s',
                volume_id)
            reconcile_comicvine(volume_id)
        except (InvalidComicVineApiKey, CVRateLimitReached) as error:
            _record_reconciliation(
                volume_id, 'error', message=type(error).__name__
            )
            LOGGER.warning(
                'Publisher volume %s could not reconcile: %s',
                volume_id, type(error).__name__
            )
            get_db().connection.commit()
            break
        except (InvalidKeyValue, VolumeNotMatched) as error:
            _record_reconciliation(
                volume_id,
                'conflict' if isinstance(error, InvalidKeyValue) else 'pending',
                message='Metadata match could not be verified; review required'
            )
            LOGGER.warning(
                'Publisher volume %s match was not verified: %s',
                volume_id, type(error).__name__
            )
        get_db().connection.commit()
    return attempted
