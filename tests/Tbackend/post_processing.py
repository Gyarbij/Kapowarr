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
        return download

    @patch('backend.features.post_processing.record_activity')
    def test_records_failed_download(self, record_activity):
        download = self._download(DownloadState.FAILED_STATE)

        record_download_activity(download)

        args, kwargs = record_activity.call_args
        self.assertEqual(args[0], ActivityCategory.DOWNLOAD)
        self.assertEqual(args[1], ActivityEventType.DOWNLOAD_FAILED)
        self.assertEqual(args[2], 'Download failed: Example Series 001')
        self.assertFalse(kwargs['success'])
        self.assertEqual(kwargs['details']['source'], 'GetComics')

    @patch('backend.features.post_processing.record_activity')
    def test_records_replacement_after_success(self, record_activity):
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