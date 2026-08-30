import unittest
from unittest.mock import MagicMock, patch

from backend.features.mass_edit import MassEditorRefreshSearch, MassEditorSearch
from backend.features.search import create_search_outcome


class MassEditorSearchTest(unittest.TestCase):
    @patch(
        'backend.features.mass_edit.iter_commit',
        side_effect=lambda values: values
    )
    @patch('backend.features.mass_edit.WebSocket')
    @patch('backend.features.mass_edit.DownloadHandler')
    @patch('backend.features.mass_edit.auto_search')
    def test_reports_selected_queued_and_duplicate_links(
        self,
        auto_search,
        download_handler_class,
        websocket_class,
        _iter_commit
    ):
        def search(volume_id, outcome=None):
            outcome['volumes_scanned'] += 1
            outcome['open_issues'] += 1
            outcome['candidates_found'] += 2
            outcome['matched_candidates'] += 1
            outcome['rejections']['title_mismatch'] = (
                outcome['rejections'].get('title_mismatch', 0) + 1
            )
            return [{'link': f'https://getcomics.org/{volume_id}/'}]

        auto_search.side_effect = search
        download_handler = download_handler_class.return_value
        download_handler.link_in_queue.side_effect = (False, True)
        download_handler.add_multiple.side_effect = (
            [([{'id': 10}], None)],
            []
        )

        outcome = MassEditorSearch([1, 2]).run()

        self.assertEqual(outcome['volumes_scanned'], 2)
        self.assertEqual(outcome['selected_links'], 2)
        self.assertEqual(outcome['queued_links'], 1)
        self.assertEqual(outcome['already_queued_links'], 1)
        self.assertEqual(outcome['rejections'], {'title_mismatch': 2})

        final_event = websocket_class.return_value.emit.call_args_list[-1].args[0]
        self.assertEqual(final_event.get_body()['summary'], dict(outcome))

    @patch(
        'backend.features.mass_edit.iter_commit',
        side_effect=lambda values: values
    )
    @patch('backend.features.mass_edit.WebSocket')
    @patch('backend.features.mass_edit.refresh_and_scan')
    @patch('backend.features.mass_edit._search_volumes')
    def test_refresh_search_refreshes_complete_snapshot_first(
        self,
        search_volumes,
        refresh_and_scan,
        _websocket,
        _iter_commit
    ):
        calls = []
        refresh_and_scan.side_effect = (
            lambda volume_id: calls.append(('refresh', volume_id))
        )
        search_volumes.side_effect = lambda volume_ids, *args, **kwargs: (
            calls.append(('search', list(volume_ids)))
            or create_search_outcome()
        )

        MassEditorRefreshSearch([1, 2]).run()

        self.assertEqual(calls, [
            ('refresh', 1),
            ('refresh', 2),
            ('search', [1, 2])
        ])


if __name__ == '__main__':
    unittest.main()
