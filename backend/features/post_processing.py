# -*- coding: utf-8 -*-

"""
The post-download processing (a.k.a. post-processing or PP) of downloads.
"""

from __future__ import annotations

from os.path import basename, exists, isfile, join, splitext
from typing import TYPE_CHECKING, Dict, List

from backend.base.definitions import (
    ActivityCategory,
    ActivityEventType,
    BlocklistReason,
    DownloadState,
    FileConstants,
)
from backend.base.files import (
    copy_directory,
    delete_file_folder,
    rename_file,
    set_detected_extension,
)
from backend.base.logging import LOGGER
from backend.features.activity_history import record_activity
from backend.implementations.blocklist import add_to_blocklist
from backend.implementations.conversion import mass_convert
from backend.implementations.converters import extract_files_from_folder
from backend.implementations.download_clients import TorrentDownload
from backend.implementations.file_matching import scan_files, set_file_matching
from backend.implementations.file_processing import mass_process_files
from backend.implementations.naming import mass_rename
from backend.implementations.volumes import Volume
from backend.internals.db import commit, get_db
from backend.internals.db_models import FilesDB
from backend.internals.settings import Settings

if TYPE_CHECKING:
    from backend.base.definitions import Download


# region General
def reset_file_link(download: TorrentDownload) -> None:
    "Set download.files back to original folder from the copied folder"
    download.files = download._original_files
    return


# region Database
def remove_from_queue(download: Download) -> None:
    "Delete the download from the queue in the database"
    get_db().execute(
        "DELETE FROM download_queue WHERE id = ?",
        (download.id,)
    ).connection.commit()
    return


def _get_download_issue_ids(download: Download) -> List[int]:
    if not download.files:
        return []

    placeholders = ','.join('?' for _ in download.files)
    return [
        row[0]
        for row in get_db().execute(
            f"""
            SELECT DISTINCT if.issue_id
            FROM issues_files if
            INNER JOIN files f ON f.id = if.file_id
            INNER JOIN issues i ON i.id = if.issue_id
            WHERE i.volume_id = ?
                AND f.filepath IN ({placeholders})
            ORDER BY if.issue_id;
            """,
            (download.volume_id, *download.files)
        )
    ]


def record_download_activity(download: Download) -> None:
    "Record the final outcome of a download"
    success = download.state != DownloadState.FAILED_STATE
    replaced_path = download.activity_replaced_path
    matched_issue_ids = _get_download_issue_ids(download) if success else []
    intended_issue_id = download.issue_id
    issue_id = intended_issue_id
    if issue_id is None and len(matched_issue_ids) == 1:
        issue_id = matched_issue_ids[0]
    needs_manual_match = (
        success
        and download.forced_match
        and (
            not matched_issue_ids
            or (
                intended_issue_id is not None
                and intended_issue_id not in matched_issue_ids
            )
        )
    )

    if not success:
        event_type = ActivityEventType.DOWNLOAD_FAILED
        summary = f'Download failed: {download.title}'
    elif replaced_path:
        event_type = ActivityEventType.DOWNLOAD_REPLACED
        summary = f'Downloaded and replaced {download.title}'
    else:
        event_type = ActivityEventType.DOWNLOAD_SUCCEEDED
        summary = f'Downloaded {download.title}'
    if needs_manual_match:
        summary += ' (needs issue match)'

    record_activity(
        ActivityCategory.DOWNLOAD,
        event_type,
        summary,
        volume_id=download.volume_id,
        issue_id=issue_id,
        success=success,
        origin='download',
        details={
            'web_link': download.web_link,
            'web_title': download.web_title,
            'web_sub_title': download.web_sub_title,
            'source': download.source_type.value,
            'source_name': download.source_name,
            'files': list(download.files),
            'replaced_path': replaced_path,
            'forced_match': download.forced_match,
            'intended_issue_id': intended_issue_id,
            'matched_issue_ids': matched_issue_ids,
            'needs_manual_match': needs_manual_match
        }
    )
    return


def add_file_to_database(download: Download) -> None:
    "Register files in database and match to a volume/issue"
    _match_download_files(download)
    return


def _match_download_files(download: Download) -> None:
    scan_files(
        download.volume_id,
        filepath_filter=download.files,
        update_websocket=True,
        allow_special_version_mismatch=download.forced_match
    )

    if not download.forced_match or download.issue_id is None:
        return

    unmatched_files = [
        filepath
        for filepath in download.files
        if (
            isfile(filepath)
            and FilesDB.volume_of_file(filepath) is None
        )
    ]
    if unmatched_files:
        set_file_matching(
            download.volume_id,
            [
                {
                    'filepath': filepath,
                    'issue_ids': [download.issue_id],
                    'general_file': False,
                    'forced_match': True
                }
                for filepath in unmatched_files
            ],
            record_event=False
        )
    return


# region Blocklist
def add_dl_to_blocklist(download: Download) -> None:
    "Add the download to the blocklist in the database"
    add_to_blocklist(
        download.web_link,
        download.web_title,
        download.web_sub_title,
        download.download_link,
        download.source_type,
        download.volume_id,
        download.issue_id,
        BlocklistReason.LINK_BROKEN
    )
    return


# region Moving
def move_to_dest(download: Download) -> None:
    "Move file/fold from download folder to final destination"
    if not exists(download.files[0]):
        return

    folder = Volume(download.volume_id).vd.folder
    extension = splitext(download.files[0])[1].lower()
    if extension not in FileConstants.SCANNABLE_EXTENSIONS:
        extension = ''

    file_dest = join(
        folder,
        download.filename_body + extension
    )
    LOGGER.debug(
        f'Moving download to final destination: {download}, Dest: {file_dest}'
    )

    # If it takes very long to delete/move the file/folder (because of it's size),
    # the DB is left locked for a long period leading to timeouts.
    commit()

    if exists(file_dest):
        LOGGER.warning(
            f'The file/folder {file_dest} already exists; replacing with downloaded file'
        )
        download.activity_replaced_path = file_dest
        delete_file_folder(file_dest)

    rename_file(download.files[0], file_dest)
    download.files = [file_dest]
    return


def move_torrent_to_dest(download: TorrentDownload) -> None:
    """
    Move folder downloaded using torrent from download folder to
    final destination, extract files, scan them, rename them.
    """
    if not exists(download.files[0]):
        return

    move_to_dest(download)

    download.files = extract_files_from_folder(
        download.files[0],
        download.volume_id
    )

    if not download.files:
        return

    _match_download_files(download)

    rename_files = Settings().sv.rename_downloaded_files
    if rename_files:
        download.files = mass_rename(
            download.volume_id,
            filepath_filter=download.files,
            process_individual_files=False,
            record_event=False
        )

    return


def copy_file_torrent(download: TorrentDownload) -> None:
    """
    Copy downloaded files to dest. Change download.file to copy.
    Change back using `PPA.reset_file_link()`.
    """
    download._original_files = download.files
    if not exists(download.files[0]):
        return

    folder = Volume(download.volume_id).vd.folder
    file_dest = join(folder, basename(download.files[0]))
    LOGGER.debug(
        f'Copying download to final destination: {download}, Dest: {file_dest}'
    )

    # If it takes very long to delete/copy the folder (because of it's size),
    # the DB is left locked for a long period leading to timeouts.
    commit()

    if exists(file_dest):
        LOGGER.warning(
            f'The file/folder {file_dest} already exists; replacing with downloaded file'
        )
        download.activity_replaced_path = file_dest
        delete_file_folder(file_dest)

    copy_directory(download.files[0], file_dest)

    download.files = extract_files_from_folder(
        file_dest,
        download.volume_id
    )

    if not download.files:
        return

    _match_download_files(download)

    rename_files = Settings().sv.rename_downloaded_files
    if rename_files:
        download.files = mass_rename(
            download.volume_id,
            filepath_filter=download.files,
            process_individual_files=False,
            record_event=False
        )

    return


# region Extras
def delete_file(download: Download) -> None:
    "Delete file from download folder"
    for f in download.files:
        delete_file_folder(f)
    return


def rename_with_proper_extension(download: Download) -> None:
    """
    Rename a file with the proper extension based on mimetype. Rescan files
    in case a rename is done.
    """
    renamed_files: Dict[str, str] = {}
    for idx, file in enumerate(download.files):
        if not isfile(file):
            continue

        new_file = set_detected_extension(file)
        if new_file != file:
            rename_file(file, new_file)
            download.files[idx] = new_file
            renamed_files[file] = new_file

    if renamed_files:
        FilesDB.update_filepaths(renamed_files)
        commit()

    return


def convert_file(download: Download) -> None:
    "Convert a file into a different format based on settings"
    if not Settings().sv.convert:
        return

    download.files += mass_convert(
        download.volume_id,
        download.issue_id,
        filepath_filter=download.files,
        update_websocket_files=True,
        process_individual_files=False,
        record_event=False
    )
    return


def set_file_properties(download: Download) -> None:
    "Process the file to set ownership, permissions and file date"

    mass_process_files(
        download.volume_id,
        download.issue_id
    )
    return


# region Post-Processors
class PostProcessor:
    actions_success = [
        remove_from_queue,
        move_to_dest,
        rename_with_proper_extension,
        add_file_to_database,
        convert_file,
        set_file_properties,
        record_download_activity
    ]

    actions_seeding = []

    actions_canceled = [
        delete_file,
        remove_from_queue
    ]

    actions_shutdown = [
        delete_file
    ]

    actions_failed = [
        remove_from_queue,
        record_download_activity,
        delete_file
    ]

    actions_perm_failed = [
        remove_from_queue,
        record_download_activity,
        add_dl_to_blocklist,
        delete_file
    ]

    @staticmethod
    def _run_actions(actions: list, download) -> None:
        for action in actions:
            action(download)
        return

    @classmethod
    def success(cls, download) -> None:
        LOGGER.info(f'Postprocessing of successful download: {download.id}')
        cls._run_actions(cls.actions_success, download)
        return

    @classmethod
    def seeding(cls, download) -> None:
        LOGGER.info(f'Postprocessing of seeding download: {download.id}')
        cls._run_actions(cls.actions_seeding, download)
        return

    @classmethod
    def canceled(cls, download) -> None:
        LOGGER.info(f'Postprocessing of canceled download: {download.id}')
        cls._run_actions(cls.actions_canceled, download)
        return

    @classmethod
    def shutdown(cls, download) -> None:
        LOGGER.info(f'Postprocessing of shut down download: {download.id}')
        cls._run_actions(cls.actions_shutdown, download)
        return

    @classmethod
    def failed(cls, download) -> None:
        LOGGER.info(f'Postprocessing of failed download: {download.id}')
        cls._run_actions(cls.actions_failed, download)
        return

    @classmethod
    def perm_failed(cls, download) -> None:
        LOGGER.info(
            f'Postprocessing of permanently failed download: {download.id}'
        )
        cls._run_actions(cls.actions_perm_failed, download)
        return


class PostProcessorTorrentsComplete(PostProcessor):
    actions_success = [
        remove_from_queue,
        move_torrent_to_dest,
        convert_file,
        set_file_properties,
        record_download_activity
    ]


class PostProcessorTorrentsCopy(PostProcessor):
    actions_success = [
        remove_from_queue,
        delete_file
    ]

    actions_seeding = [
        copy_file_torrent,
        convert_file,
        set_file_properties,
        record_download_activity,
        reset_file_link
    ]
