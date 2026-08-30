# -*- coding: utf-8 -*-

from asyncio import get_running_loop
from json import dumps, loads
from threading import Lock
from time import time
from typing import Any, Dict, Tuple

from backend.base.logging import LOGGER
from backend.internals.db import get_db


class ResponseCache:
    RETENTION = 30 * 24 * 60 * 60

    _locks: Dict[str, Any] = {}
    _generations: Dict[str, int] = {}
    _locks_guard = Lock()

    def __init__(self, namespace: str, db_getter=None) -> None:
        self.source_key = namespace
        self._db_getter = db_getter or get_db

    @classmethod
    def _get_lock(cls, key: str):
        with cls._locks_guard:
            return cls._locks.setdefault(key, Lock())

    @classmethod
    async def _acquire_lock(cls, key: str):
        lock = cls._get_lock(key)
        await get_running_loop().run_in_executor(None, lock.acquire)
        return lock

    def _read_response(
        self,
        resource: str,
        cache_key: str,
        now: int,
        allow_stale: bool = False
    ) -> Any:
        expiry_clause = '' if allow_stale else 'AND expires_at > ?'
        params: Tuple[Any, ...] = (
            (self.source_key, resource, cache_key)
            if allow_stale
            else (self.source_key, resource, cache_key, now)
        )
        row = self._db_getter().execute(f"""
            SELECT payload
            FROM metadata_response_cache
            WHERE metadata_source = ?
                AND resource = ?
                AND cache_key = ?
                {expiry_clause};
        """, params).fetchone()
        return loads(row[0]) if row else None

    def _store_response(
        self,
        resource: str,
        cache_key: str,
        value: Any,
        now: int,
        ttl: int
    ) -> None:
        cursor = self._db_getter()
        cursor.execute("""
            INSERT INTO metadata_response_cache(
                metadata_source, resource, cache_key,
                payload, fetched_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(metadata_source, resource, cache_key) DO UPDATE SET
                payload = excluded.payload,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at;
        """, (
            self.source_key, resource, cache_key,
            dumps(value), now, now + ttl
        ))
        cursor.execute(
            'DELETE FROM metadata_response_cache WHERE expires_at < ?;',
            (now - self.RETENTION,)
        )
        cursor.connection.commit()

    async def get(
        self,
        resource: str,
        cache_key: str,
        ttl: int,
        fetcher,
        force_refresh: bool = False
    ) -> Any:
        now = round(time())
        if not force_refresh:
            cached = self._read_response(resource, cache_key, now)
            if cached is not None:
                return cached

        lock_key = f'{self.source_key}:{resource}:{cache_key}'
        generation = self._generations.get(lock_key, 0)
        lock = await self._acquire_lock(lock_key)
        try:
            now = round(time())
            force_refresh = (
                force_refresh
                and self._generations.get(lock_key, 0) == generation
            )
            if not force_refresh:
                cached = self._read_response(resource, cache_key, now)
                if cached is not None:
                    return cached
            try:
                value = await fetcher()
            except Exception:
                stale = self._read_response(
                    resource, cache_key, now, allow_stale=True
                )
                if stale is not None:
                    LOGGER.warning(
                        'Using stale %s %s cache after refresh failure',
                        self.source_key, resource
                    )
                    return stale
                raise

            self._store_response(resource, cache_key, value, now, ttl)
            self._generations[lock_key] = generation + 1
            return value
        finally:
            lock.release()

    async def _get_cached_response(
        self,
        resource: str,
        cache_key: str,
        ttl: int,
        fetcher,
        force_refresh: bool
    ) -> Any:
        return await self.get(
            resource,
            cache_key,
            ttl,
            fetcher,
            force_refresh
        )
