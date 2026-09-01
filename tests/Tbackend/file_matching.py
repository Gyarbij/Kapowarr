import unittest
from unittest.mock import MagicMock, patch

from backend.implementations.file_matching import set_file_matching


class ManualFileMatchingTest(unittest.TestCase):
    @patch('backend.implementations.file_matching.record_activity')
    @patch('backend.implementations.file_matching.scan_files')
    @patch('backend.implementations.file_matching.FilesDB.add_file')
    @patch('backend.implementations.file_matching.folder_is_inside_folder')
    @patch('backend.implementations.naming.mass_rename')
    @patch('backend.implementations.naming.preview_mass_rename')
    @patch('backend.implementations.file_matching.get_db')
    def test_match_and_rename_only_changed_file(
        self,
        get_db,
        preview_mass_rename,
        mass_rename,
        folder_is_inside_folder,
        add_file,
        scan_files,
        record_activity
    ):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchone.return_value = ('/library/Example Series',)
        get_db.return_value = cursor
        folder_is_inside_folder.return_value = True
        add_file.return_value = 9
        old_path = '/library/Example Series/download.cbz'
        new_path = '/library/Example Series/Example Series 001.cbz'
        preview_mass_rename.return_value = ({old_path: new_path}, None)
        mass_rename.return_value = [new_path]
        matches = [{
            'filepath': old_path,
            'issue_ids': [3],
            'general_file': False,
            'forced_match': True
        }]

        result = set_file_matching(1, matches, rename_files=True)

        self.assertEqual(result, {old_path: new_path})
        mass_rename.assert_called_once_with(
            1,
            filepath_filter=[old_path],
            rename_volume_folder=False,
            record_event=False
        )
        self.assertEqual(
            record_activity.call_args.kwargs['details']['renames'],
            [{'from': old_path, 'to': new_path}]
        )
        scan_files.assert_called_once_with(1, update_websocket=True)


if __name__ == '__main__':
    unittest.main()