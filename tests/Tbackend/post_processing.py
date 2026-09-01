import unittest
from unittest.mock import MagicMock, patch

from backend.base.definitions import (
    ActivityCategory,
    ActivityEventType,
    DownloadSource,
    DownloadState,
)
from backend.features.post_processing import (
    PostProcessor,
    PostProcessorTorrentsComplete,
    PostProcessorTorrentsCopy,
    add_file_to_database,
    record_download_activity,
    reset_file_link,
    set_file_properties,
)


class DownloadActivityTest(unittest.TestCase):
    def _download(self, state, replaced_path=None):
        download = MagicMock()
        download.state = state
        download.activity_replaced_path = replaced_path
        download.title = 'Example Series 001'
        download.volume_id = 1
        download.issue_id = 2
        download.web_link = 'https://example.test/release'
        download.web_title = 'Example release'
        download.web_sub_title = 'Issue 1'
        download.source_type = DownloadSource.GETCOMICS
        download.source_name = 'GetComics'
        download.files = ['/library/Example Series 001.cbz']
        download.forced_match = False
        return download

    @patch('backend.features.post_processing.record_activity')
    @patch('backend.features.post_processing._get_download_issue_ids')
    def test_records_failed_download(self, get_issue_ids, record_activity):
        download = self._download(DownloadState.FAILED_STATE)

        record_download_activity(download)

        args, kwargs = record_activity.call_args
        self.assertEqual(args[0], ActivityCategory.DOWNLOAD)
        self.assertEqual(args[1], ActivityEventType.DOWNLOAD_FAILED)
        self.assertEqual(args[2], 'Download failed: Example Series 001')
        self.assertFalse(kwargs['success'])
        self.assertEqual(kwargs['details']['source'], 'GetComics')
        get_issue_ids.assert_not_called()

    @patch('backend.features.post_processing.record_activity')
    @patch('backend.features.post_processing._get_download_issue_ids')
    def test_records_replacement_after_success(
        self,
        get_issue_ids,
        record_activity
    ):
        get_issue_ids.return_value = [2]
        replaced = '/library/Example Series 001.cbz'
        download = self._download(
            DownloadState.IMPORTING_STATE,
            replaced
        )

        record_download_activity(download)

        args, kwargs = record_activity.call_args
        self.assertEqual(args[1], ActivityEventType.DOWNLOAD_REPLACED)
        self.assertEqual(
            args[2],
            'Downloaded and replaced Example Series 001'
        )
        self.assertTrue(kwargs['success'])
        self.assertEqual(kwargs['details']['replaced_path'], replaced)

    @patch('backend.features.post_processing.record_activity')
    @patch('backend.features.post_processing._get_download_issue_ids')
    def test_resolves_issue_context_after_forced_download(
        self,
        get_issue_ids,
        record_activity
    ):
        download = self._download(DownloadState.IMPORTING_STATE)
        download.issue_id = None
        download.forced_match = True
        get_issue_ids.return_value = [7]

        record_download_activity(download)

        kwargs = record_activity.call_args.kwargs
        self.assertEqual(kwargs['issue_id'], 7)
        self.assertEqual(kwargs['details']['matched_issue_ids'], [7])
        self.assertFalse(kwargs['details']['needs_manual_match'])

    @patch('backend.features.post_processing.record_activity')
    @patch('backend.features.post_processing._get_download_issue_ids')
    def test_marks_unresolved_forced_download_for_manual_match(
        self,
        get_issue_ids,
        record_activity
    ):
        download = self._download(DownloadState.IMPORTING_STATE)
        download.issue_id = None
        download.forced_match = True
        get_issue_ids.return_value = []

        record_download_activity(download)

        args, kwargs = record_activity.call_args
        self.assertEqual(
            args[2],
            'Downloaded Example Series 001 (needs issue match)'
        )
        self.assertTrue(kwargs['details']['needs_manual_match'])

    @patch('backend.features.post_processing.record_activity')
    @patch('backend.features.post_processing._get_download_issue_ids')
    def test_marks_forced_download_mapped_to_wrong_issue(
        self,
        get_issue_ids,
        record_activity
    ):
        download = self._download(DownloadState.IMPORTING_STATE)
        download.forced_match = True
        get_issue_ids.return_value = [3]

        record_download_activity(download)

        kwargs = record_activity.call_args.kwargs
        self.assertEqual(kwargs['issue_id'], 2)
        self.assertEqual(kwargs['details']['intended_issue_id'], 2)
        self.assertTrue(kwargs['details']['needs_manual_match'])

    @patch('backend.features.post_processing.scan_files')
    def test_forced_download_allows_special_version_mismatch(self, scan_files):
        download = self._download(DownloadState.IMPORTING_STATE)
        download.forced_match = True

        add_file_to_database(download)

        scan_files.assert_called_once_with(
            download.volume_id,
            filepath_filter=download.files,
            update_websocket=True,
            allow_special_version_mismatch=True
        )

    @patch('backend.features.post_processing.set_file_matching')
    @patch('backend.features.post_processing.FilesDB.volume_of_file')
    @patch('backend.features.post_processing.isfile', return_value=True)
    @patch('backend.features.post_processing.scan_files')
    def test_forced_issue_download_falls_back_to_explicit_match(
        self,
        _scan_files,
        _isfile,
        volume_of_file,
        set_file_matching
    ):
        download = self._download(DownloadState.IMPORTING_STATE)
        download.forced_match = True
        volume_of_file.return_value = None

        add_file_to_database(download)

        set_file_matching.assert_called_once_with(
            1,
            [{
                'filepath': '/library/Example Series 001.cbz',
                'issue_ids': [2],
                'general_file': False,
                'forced_match': True
            }],
            record_event=False
        )

    def test_successful_processors_record_only_after_file_work(self):
        for processor in (PostProcessor, PostProcessorTorrentsComplete):
            with self.subTest(processor=processor.__name__):
                self.assertGreater(
                    processor.actions_success.index(
                        record_download_activity
                    ),
                    processor.actions_success.index(set_file_properties)
                )

        self.assertGreater(
            PostProcessorTorrentsCopy.actions_seeding.index(
                record_download_activity
            ),
            PostProcessorTorrentsCopy.actions_seeding.index(
                set_file_properties
            )
        )
        self.assertLess(
            PostProcessorTorrentsCopy.actions_seeding.index(
                record_download_activity
            ),
            PostProcessorTorrentsCopy.actions_seeding.index(reset_file_link)
        )


if __name__ == '__main__':
    unittest.main()