import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from backend.base.definitions import BlocklistReason, BlocklistReasonID
from backend.implementations import blocklist
from backend.implementations.blocklist import add_to_blocklist, blocklist_contains


class BlocklistRetryTest(unittest.TestCase):
    NOW = 2_000_000_000

    def _contains(self, reason, added_at, download_link=None):
        cursor = MagicMock()
        result = cursor.execute.return_value
        result.exists.return_value = 42
        result.fetchone.return_value = {
            'id': 42,
            'reason': reason.value,
            'added_at': added_at,
            'download_link': download_link
        }
        with patch(
            'backend.implementations.blocklist.get_db',
            return_value=cursor
        ), patch(
            'backend.implementations.blocklist.time',
            return_value=self.NOW
        ):
            return blocklist_contains('https://getcomics.org/example/')

    def test_expired_automatic_failure_can_be_retried(self):
        result = self._contains(
            BlocklistReasonID.LINK_BROKEN,
            self.NOW - 25 * 60 * 60
        )

        self.assertIsNone(result)

    def test_fresh_automatic_failure_remains_blocked(self):
        result = self._contains(
            BlocklistReasonID.NO_WORKING_LINKS,
            self.NOW - 60
        )

        self.assertEqual(result, 42)

    def test_page_load_failure_has_short_cooldown(self):
        result = self._contains(
            BlocklistReasonID.LINK_BROKEN,
            self.NOW - 6 * 60
        )

        self.assertIsNone(result)

    def test_user_added_entry_never_expires(self):
        result = self._contains(
            BlocklistReasonID.ADDED_BY_USER,
            self.NOW - 365 * 24 * 60 * 60
        )

        self.assertEqual(result, 42)

    def test_readding_expired_download_link_refreshes_existing_entry(self):
        cursor = MagicMock()
        query = cursor.execute.return_value
        query.fetchone.return_value = {
            'id': 42,
            'reason': BlocklistReasonID.LINK_BROKEN.value,
            'added_at': self.NOW - 25 * 60 * 60,
            'download_link': 'https://files.example/example.cbz'
        }
        expected = object()

        with patch(
            'backend.implementations.blocklist.get_db',
            return_value=cursor
        ), patch(
            'backend.implementations.blocklist.get_blocklist_entry',
            return_value=expected
        ), patch(
            'backend.implementations.blocklist.time',
            return_value=self.NOW
        ):
            result = add_to_blocklist(
                web_link='https://getcomics.org/example/',
                web_title='Example #1',
                web_sub_title='Example #1',
                download_link='https://files.example/example.cbz',
                source=None,
                volume_id=1,
                issue_id=2,
                reason=BlocklistReason.LINK_BROKEN
            )

        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIs(result, expected)
        self.assertTrue(any('UPDATE blocklist' in sql for sql in statements))
        self.assertFalse(any('INSERT INTO blocklist' in sql for sql in statements))

    def test_user_block_upgrades_active_automatic_entry(self):
        cursor = MagicMock()
        cursor.execute.return_value.fetchone.return_value = {
            'id': 42,
            'reason': BlocklistReasonID.LINK_BROKEN.value,
            'added_at': self.NOW - 60,
            'download_link': None
        }

        with patch(
            'backend.implementations.blocklist.get_db',
            return_value=cursor
        ), patch(
            'backend.implementations.blocklist.get_blocklist_entry',
            return_value=object()
        ), patch(
            'backend.implementations.blocklist.time',
            return_value=self.NOW
        ):
            add_to_blocklist(
                web_link='https://getcomics.org/example/',
                web_title='Example #1',
                web_sub_title=None,
                download_link=None,
                source=None,
                volume_id=1,
                issue_id=2,
                reason=BlocklistReason.ADDED_BY_USER
            )

        update_call = next(
            call
            for call in cursor.execute.call_args_list
            if 'UPDATE blocklist' in call.args[0]
        )
        self.assertEqual(
            update_call.args[1]['reason'],
            BlocklistReasonID.ADDED_BY_USER.value
        )

    def test_success_clears_only_automatic_entries(self):
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()
        cursor.execute("""
            CREATE TABLE blocklist(
                id INTEGER PRIMARY KEY,
                web_link TEXT,
                download_link TEXT,
                reason INTEGER,
                added_at INTEGER
            );
        """)
        link = 'https://getcomics.org/example/'
        cursor.executemany(
            'INSERT INTO blocklist VALUES (?, ?, NULL, ?, ?);',
            (
                (1, link, BlocklistReasonID.LINK_BROKEN.value, self.NOW),
                (2, link, BlocklistReasonID.ADDED_BY_USER.value, self.NOW)
            )
        )

        with patch(
            'backend.implementations.blocklist.get_db',
            return_value=cursor
        ):
            blocklist.clear_automatic_blocklist(link)

        remaining = cursor.execute(
            'SELECT id, reason FROM blocklist ORDER BY id;'
        ).fetchall()
        connection.close()
        self.assertEqual(
            [tuple(row) for row in remaining],
            [(2, BlocklistReasonID.ADDED_BY_USER.value)]
        )


if __name__ == '__main__':
    unittest.main()
