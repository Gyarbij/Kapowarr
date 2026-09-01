# -*- coding: utf-8 -*-

from json import dumps, loads
from time import time
from typing import Any, Dict, Mapping, Optional, Union

from backend.base.definitions import ActivityCategory, ActivityEventType, BaseEnum
from backend.base.logging import LOGGER
from backend.internals.db import get_db

ActivityValue = Union[str, ActivityCategory, ActivityEventType]

_SNAPSHOT_COLUMNS = (
    'volume_id',
    'issue_id',
    'file_id',
    'volume_comicvine_id',
    'issue_comicvine_id',
    'volume_title',
    'volume_year',
    'issue_number',
    'issue_title',
    'file_path'
)


def _enum_value(value: ActivityValue) -> str:
    if isinstance(value, (ActivityCategory, ActivityEventType)):
        return str(value.value)
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseEnum):
        return value.value
    raise TypeError(f'{value!r} is not JSON serializable')


def _resolve_snapshot(
    cursor: Any,
    volume_id: Optional[int],
    issue_id: Optional[int],
    file_id: Optional[int]
) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}

    if issue_id is not None:
        issue = cursor.execute(
            """
            SELECT
                v.id AS volume_id,
                v.comicvine_id AS volume_comicvine_id,
                v.title AS volume_title,
                v.year AS volume_year,
                i.id AS issue_id,
                i.comicvine_id AS issue_comicvine_id,
                i.issue_number,
                i.title AS issue_title
            FROM issues i
            INNER JOIN volumes v ON v.id = i.volume_id
            WHERE i.id = ?;
            """,
            (issue_id,)
        ).fetchone()
        if issue is not None:
            snapshot.update(dict(issue))

    if volume_id is not None and 'volume_id' not in snapshot:
        volume = cursor.execute(
            """
            SELECT
                id AS volume_id,
                comicvine_id AS volume_comicvine_id,
                title AS volume_title,
                year AS volume_year
            FROM volumes
            WHERE id = ?;
            """,
            (volume_id,)
        ).fetchone()
        if volume is not None:
            snapshot.update(dict(volume))

    if file_id is not None:
        file = cursor.execute(
            """
            SELECT id AS file_id, filepath AS file_path
            FROM files
            WHERE id = ?;
            """,
            (file_id,)
        ).fetchone()
        if file is not None:
            snapshot.update(dict(file))

    snapshot.setdefault('volume_id', volume_id)
    snapshot.setdefault('issue_id', issue_id)
    snapshot.setdefault('file_id', file_id)
    return snapshot


def record_activity(
    category: ActivityValue,
    event_type: ActivityValue,
    summary: str,
    *,
    volume_id: Optional[int] = None,
    issue_id: Optional[int] = None,
    file_id: Optional[int] = None,
    success: Optional[bool] = True,
    origin: str = 'system',
    details: Optional[Mapping[str, Any]] = None,
    snapshot: Optional[Mapping[str, Any]] = None,
    created_at: Optional[int] = None,
    cursor: Any = None
) -> int:
    """Record one meaningful, durable activity event."""
    cursor = cursor or get_db()
    resolved_snapshot = _resolve_snapshot(
        cursor,
        volume_id,
        issue_id,
        file_id
    )
    if snapshot:
        resolved_snapshot.update(snapshot)

    values = {
        column: resolved_snapshot.get(column)
        for column in _SNAPSHOT_COLUMNS
    }
    result = cursor.execute(
        """
        INSERT INTO activity_history(
            created_at, category, event_type, summary,
            volume_id, issue_id, file_id,
            volume_comicvine_id, issue_comicvine_id,
            volume_title, volume_year, issue_number, issue_title, file_path,
            success, origin, details
        ) VALUES (
            :created_at, :category, :event_type, :summary,
            :volume_id, :issue_id, :file_id,
            :volume_comicvine_id, :issue_comicvine_id,
            :volume_title, :volume_year, :issue_number, :issue_title,
            :file_path, :success, :origin, :details
        );
        """,
        {
            'created_at': created_at or round(time()),
            'category': _enum_value(category),
            'event_type': _enum_value(event_type),
            'summary': summary,
            'success': success,
            'origin': origin,
            'details': dumps(
                details or {},
                default=_json_default,
                sort_keys=True
            ),
            **values
        }
    )
    return result.lastrowid


def _deserialize_activity(row: Any) -> Dict[str, Any]:
    activity = dict(row)
    try:
        activity['details'] = loads(activity['details'])
    except (TypeError, ValueError):
        activity['details'] = {}
    return activity


def get_activity_history(
    *,
    before_id: Optional[int] = None,
    volume_id: Optional[int] = None,
    issue_id: Optional[int] = None,
    category: Optional[ActivityValue] = None,
    event_type: Optional[ActivityValue] = None,
    success: Optional[bool] = None,
    limit: int = 50,
    cursor: Any = None
) -> Dict[str, Any]:
    """Get a stable, newest-first page of activity events."""
    if limit < 1 or limit > 100:
        raise ValueError('limit must be between 1 and 100')

    conditions = []
    parameters: Dict[str, Any] = {'limit': limit + 1}
    filters = {
        'before_id': before_id,
        'volume_id': volume_id,
        'issue_id': issue_id,
        'category': _enum_value(category) if category is not None else None,
        'event_type': (
            _enum_value(event_type) if event_type is not None else None
        ),
        'success': success
    }
    for key, value in filters.items():
        if value is None:
            continue
        operator = '<' if key == 'before_id' else '='
        column = 'id' if key == 'before_id' else key
        conditions.append(f'{column} {operator} :{key}')
        parameters[key] = value

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ''
    cursor = cursor or get_db()
    rows = cursor.execute(
        f"""
        SELECT *
        FROM activity_history
        {where}
        ORDER BY id DESC
        LIMIT :limit;
        """,
        parameters
    ).fetchall()
    activities = [_deserialize_activity(row) for row in rows]
    has_more = len(activities) > limit
    items = activities[:limit]

    return {
        'items': items,
        'next_before_id': items[-1]['id'] if has_more else None,
        'has_more': has_more
    }


def delete_activity_history(cursor: Any = None) -> None:
    """Delete all durable activity events."""
    LOGGER.info('Deleting activity history')
    (cursor or get_db()).execute('DELETE FROM activity_history;')
    return