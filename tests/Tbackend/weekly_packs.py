import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.implementations.weekly_packs import (
    parse_weekly_pack,
    queue_weekly_pack_items,
)


class WeeklyPackParserTest(unittest.TestCase):
    def setUp(self):
        self.post = {
            'id': 404065,
            'date': '2026-08-19T18:28:22',
            'modified': '2026-08-20T08:00:00',
            'link': 'https://getcomics.org/other-comics/2026-08-19-weekly-pack/',
            'title': {'rendered': '2026.08.19 Weekly Pack'},
            'content': {'rendered': """
                <h3><span>JPG</span></h3>
                <ul>
                    <li>2026.08.19 DC Week (738 MB) :
                        <a href="http://getcomics.org/dls/archive">PIXELDRAIN</a>
                    </li>
                </ul>
                <h3><span>WEBP</span></h3>
                <ul>
                    <li>2026.08.19 Marvel Week (WebP) (344 MB) :
                        <a href="https://getcomics.org/dls/webp">MEGA</a>
                    </li>
                </ul>
                <h3><span>DC COMICS</span></h3>
                <ul>
                    <li>Lobo #5 :
                        <a href="https://getcomics.org/dc/lobo-5-2026/">Download</a>
                        <a href="https://read.example/lobo">Read Online</a>
                    </li>
                </ul>
                <h3><span>MARVEL COMICS</span></h3>
                <ul>
                    <li>Daredevil #5 :
                        <a href="https://getcomics.org/marvel/daredevil-5-2026/">Download</a>
                    </li>
                </ul>
                <h3><span>IMAGE COMICS</span></h3>
                <ul>
                    <li>Kaya #36 :
                        <a href="https://getcomics.org/other-comics/kaya-36-2026/">Download</a>
                    </li>
                </ul>
                <h3><span>INDIE COMICS</span></h3>
                <p>BOOM STUDIOS:</p>
                <ul>
                    <li>Vampyrates! #2 :
                        <a href="https://getcomics.org/other-comics/vampyrates-2-2026/">Download</a>
                    </li>
                    <li>Trap #1 :
                        <a href="https://getcomics.org.evil.example/trap/">Download</a>
                    </li>
                </ul>
            """}
        }

    def test_separates_archives_from_individual_issues(self):
        pack = parse_weekly_pack(self.post)

        self.assertEqual(pack['week_date'], '2026-08-19')
        self.assertEqual(len(pack['archives']), 2)
        self.assertEqual(len(pack['items']), 4)
        self.assertEqual(
            {item['publisher'] for item in pack['items']},
            {'DC Comics', 'Marvel Comics', 'Image Comics', 'BOOM STUDIOS'}
        )
        self.assertEqual(pack['archives'][0]['size'], '738 MB')
        self.assertEqual(pack['archives'][1]['format'], 'WEBP')

    def test_extracts_issue_metadata_and_indie_subpublisher(self):
        pack = parse_weekly_pack(self.post)
        lobo = pack['items'][0]
        indie = pack['items'][-1]

        self.assertEqual(lobo['series_title'], 'Lobo')
        self.assertEqual(lobo['calculated_issue_number'], 5.0)
        self.assertEqual(lobo['issue_year'], 2026)
        self.assertEqual(indie['subpublisher'], 'BOOM STUDIOS')

    def test_excludes_read_online_and_deceptive_hosts(self):
        pack = parse_weekly_pack(self.post)
        urls = [item['external_url'] for item in pack['items']]

        self.assertNotIn('https://read.example/lobo', urls)
        self.assertFalse(any('evil.example' in url for url in urls))

    def test_incomplete_post_without_archives_remains_usable(self):
        self.post['content']['rendered'] = """
            <h3>DC COMICS</h3>
            <ul><li>Lobo #6 :
                <a href="https://getcomics.org/dc/lobo-6-2026/">Download</a>
            </li></ul>
        """

        pack = parse_weekly_pack(self.post)

        self.assertEqual(pack['archives'], [])
        self.assertEqual(len(pack['items']), 1)
        self.assertFalse(pack['has_aggregate_archives'])

    def test_issue_range_is_review_only_for_discovery(self):
        from backend.implementations.weekly_packs import _discovery_item

        item = {
            'record_key': 'range',
            'calculated_issue_number': (1.0, 3.0)
        }

        result = _discovery_item(item, '2026-08-26')

        self.assertIsNone(result['calculated_issue_number'])

    @patch('backend.implementations.weekly_packs.get_weekly_packs')
    def test_queue_revalidates_exact_issue_and_rejects_metadata_pending(
        self,
        get_weekly_packs
    ):
        pack = parse_weekly_pack(self.post)
        lobo = next(
            item for item in pack['items']
            if item['series_title'] == 'Lobo'
        )
        pending = {
            **lobo,
            'record_key': 'https://getcomics.org/dc/lobo-6-2026/',
            'external_url': 'https://getcomics.org/dc/lobo-6-2026/',
            'display_title': 'Lobo #6',
            'issue_number': 6.0,
            'calculated_issue_number': 6.0
        }
        pack['items'] = [lobo, pending]
        get_weekly_packs.return_value = [pack]

        library_rows = [{
            'volume_id': 3643,
            'title': 'Lobo',
            'alt_title': None,
            'volume_year': 2026,
            'publisher': 'DC Comics',
            'volume_monitored': 1,
            'issue_id': 305,
            'calculated_issue_number': 5.0,
            'issue_date': '2026-07-22',
            'issue_monitored': 1,
            'downloaded': 0
        }]
        handler = MagicMock()
        handler.link_in_queue.return_value = False
        handler.add = AsyncMock(return_value=([{'id': 10}], None))

        result = asyncio.run(queue_weekly_pack_items(
            [lobo['record_key'], pending['record_key']],
            download_handler=handler,
            library_rows=library_rows
        ))

        self.assertEqual(result[0]['status'], 'queued')
        self.assertEqual(result[1]['status'], 'rejected')
        self.assertEqual(result[1]['local_status'], 'metadata_pending')
        handler.add.assert_awaited_once_with(
            lobo['external_url'], 3643, 305, False
        )

    @patch('backend.implementations.weekly_packs.get_weekly_packs')
    @patch('backend.implementations.weekly_packs._monitor_weekly_item')
    def test_monitor_and_download_updates_exact_item_before_queue(
        self,
        monitor_item,
        get_weekly_packs
    ):
        pack = parse_weekly_pack(self.post)
        lobo = next(
            item for item in pack['items']
            if item['series_title'] == 'Lobo'
        )
        pack['items'] = [lobo]
        get_weekly_packs.return_value = [pack]
        library_rows = [{
            'volume_id': 3643,
            'title': 'Lobo',
            'alt_title': None,
            'volume_year': 2026,
            'publisher': 'DC Comics',
            'volume_monitored': 0,
            'issue_id': 305,
            'calculated_issue_number': 5.0,
            'issue_date': '2026-07-22',
            'issue_monitored': 0,
            'downloaded': 0
        }]
        events = []
        monitor_item.side_effect = lambda *_args: events.append('monitor')
        handler = MagicMock()
        handler.link_in_queue.return_value = False

        async def add(*_args):
            events.append('queue')
            return [{'id': 10}], None

        handler.add = AsyncMock(side_effect=add)

        result = asyncio.run(queue_weekly_pack_items(
            [lobo['record_key']],
            action='monitor_and_download',
            download_handler=handler,
            library_rows=library_rows
        ))

        self.assertEqual(events, ['monitor', 'queue'])
        self.assertEqual(result[0]['status'], 'queued')
        self.assertEqual(result[0]['local_status'], 'missing_monitored')
        monitor_item.assert_called_once_with(3643, 305)

    @patch('backend.implementations.weekly_packs.get_weekly_packs')
    @patch('backend.implementations.weekly_packs._monitor_weekly_item')
    def test_download_rejects_exact_unmonitored_item_without_mutation(
        self,
        monitor_item,
        get_weekly_packs
    ):
        pack = parse_weekly_pack(self.post)
        lobo = next(
            item for item in pack['items']
            if item['series_title'] == 'Lobo'
        )
        pack['items'] = [lobo]
        get_weekly_packs.return_value = [pack]
        library_rows = [{
            'volume_id': 3643,
            'title': 'Lobo',
            'alt_title': None,
            'volume_year': 2026,
            'publisher': 'DC Comics',
            'volume_monitored': 1,
            'issue_id': 305,
            'calculated_issue_number': 5.0,
            'issue_date': '2026-07-22',
            'issue_monitored': 0,
            'downloaded': 0
        }]
        handler = MagicMock()

        result = asyncio.run(queue_weekly_pack_items(
            [lobo['record_key']],
            download_handler=handler,
            library_rows=library_rows
        ))

        self.assertEqual(result[0]['status'], 'rejected')
        self.assertEqual(result[0]['local_status'], 'missing_unmonitored')
        monitor_item.assert_not_called()
        handler.add.assert_not_called()


if __name__ == '__main__':
    unittest.main()
