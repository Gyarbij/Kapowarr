import sqlite3
import unittest
from unittest.mock import patch

from backend.base.custom_exceptions import ExternalClientNotFound
from backend.base.definitions import DownloadType
from backend.implementations.external_clients import ExternalClients
from backend.internals.db import KapowarrCursor


class LeastUsedClientTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.cursor = self.connection.cursor(KapowarrCursor)
        self.cursor.executescript("""
            CREATE TABLE external_download_clients(
                id INTEGER PRIMARY KEY,
                download_type INTEGER NOT NULL
            );
            CREATE TABLE download_queue(
                id INTEGER PRIMARY KEY,
                external_client_id INTEGER
            );
        """)

    def tearDown(self):
        self.connection.close()

    def _least_used(self, download_type=DownloadType.TORRENT):
        with patch(
            'backend.implementations.external_clients.get_db',
            return_value=self.cursor
        ), patch.object(
            ExternalClients, 'get_client',
            side_effect=lambda client_id: client_id
        ):
            return ExternalClients.get_least_used_client(download_type)

    def _add_clients(self, *client_ids, download_type=DownloadType.TORRENT):
        self.cursor.executemany(
            'INSERT INTO external_download_clients VALUES (?, ?);',
            ((i, download_type.value) for i in client_ids)
        )

    def _add_downloads(self, client_id, count):
        self.cursor.executemany(
            'INSERT INTO download_queue(external_client_id) VALUES (?);',
            ((client_id,) for _ in range(count))
        )

    def test_idle_client_preferred_over_busy_client(self):
        self._add_clients(1, 2)
        self._add_downloads(1, 3)

        self.assertEqual(self._least_used(), 2)

    def test_client_with_fewest_downloads_is_chosen(self):
        self._add_clients(1, 2)
        self._add_downloads(1, 1)
        self._add_downloads(2, 4)

        self.assertEqual(self._least_used(), 1)

    def test_other_download_types_are_ignored(self):
        self._add_clients(1, download_type=DownloadType.DIRECT)

        with self.assertRaises(ExternalClientNotFound):
            self._least_used()


if __name__ == '__main__':
    unittest.main()
