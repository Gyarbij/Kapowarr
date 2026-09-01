# -*- coding: utf-8 -*-

"""
Library, volume and issue classes and Refresh & Scan
"""

from __future__ import annotations

from asyncio import run
from datetime import datetime, timedelta
from functools import lru_cache
from io import BytesIO
from os.path import basename, dirname, exists, isdir, relpath
from re import IGNORECASE, compile
from time import time
from typing import Any, Callable, Dict, List, Mapping, Set, Tuple, Union

from typing_extensions import assert_never

from backend.base.custom_exceptions import (
    InvalidKeyValue,
    IssueNotFound,
    KeyNotFound,
    TaskForVolumeRunning,
    VolumeAlreadyAdded,
    VolumeDownloadedFor,
    VolumeNotFound,
)
from backend.base.definitions import (
    ActivityCategory,
    ActivityEventType,
    BaseEnum,
    Constants,
    FileData,
    GeneralFileData,
    IssueData,
    LibraryDateFilter,
    LibraryFilter,
    LibrarySorting,
    LibraryStatusFilter,
    MonitorScheme,
    SpecialVersion,
    VolumeData,
)
from backend.base.files import (
    change_basefolder,
    create_folder,
    delete_empty_child_folders,
    delete_empty_parent_folders,
    delete_file_folder,
    folder_is_inside_folder,
    rename_file,
)
from backend.base.helpers import (
    PortablePool,
    extract_year_from_date,
    first_of_subarrays,
    to_number_cv_id,
)
from backend.base.logging import LOGGER
from backend.features.activity_history import record_activity
from backend.implementations.comicvine import ComicVine
from backend.implementations.file_matching import scan_files
from backend.implementations.file_processing import mass_process_files
from backend.implementations.matching import match_title
from backend.implementations.root_folders import RootFolders
from backend.internals.db import commit, get_db
from backend.internals.db_models import FilesDB, GeneralFilesDB
from backend.internals.server import DownloadedStatusEvent, TaskStatusEvent, WebSocket
from backend.internals.settings import Settings

# autopep8: off
ONE_DAY = timedelta(days=1)
THIRTY_DAYS = timedelta(days=30)
split_regex = compile(r'(?<!vs)(?<!r\.i\.p)(?:(?<=[\.!\?])\s|(?<=[\.!\?]</p>)(?!$))', IGNORECASE)
remove_link_regex = compile(r'<a[^>]*>.*?</a>', IGNORECASE)
omnibus_regex = compile(r'\bomnibus\b', IGNORECASE)
os_regex = compile(r'(?<!preceding\s)\bone[\- ]?shot\b(?!\scollections?)', IGNORECASE)
hc_regex = compile(r'(?<!preceding\s)\bhard[\- ]?cover\b(?!\scollections?)', IGNORECASE)
tpb_regex = compile(
    r'\b(?:'
    r'(?:epic|complete|ultimate|definitive)\s+collection'
    r'|collected\s+(?:edition|volume)'
    r'|complete\s+edition'
    r'|compendium'
    r'|trade\s+paper\s*back'
    r'|showcase\s+presents'
    r')\b',
    IGNORECASE
)
hc_edition_regex = compile(
    r'\b(?:'
    r'library\s+edition'
    r'|deluxe\s+edition'
    r"|artist'?s?\s+edition"
    r'|gallery\s+edition'
    r'|absolute\s+edition'
    r'|masterworks?'
    r'|archives'
    r'|(?:premiere|oversized)\s+(?:hc|hard[\s\-]?cover|classic)'
    r')\b',
    IGNORECASE
)
vol_regex = compile(r'^v(?:ol(?:ume)?)?\.?\s(?:\d+|(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)[-\s]{0,1})+)(?:\:\s|$)', IGNORECASE)
# autopep8: on


# region Issue
class Issue:
    def __init__(self, issue_id: int, check_existence: bool = False) -> None:
        """Create an instance.

        Args:
            issue_id (int): The ID of the issue.
            check_existence (bool, optional): Check whether the issue exists
                based on its ID.
                Defaults to False.

        Raises:
            IssueNotFound: The issue was not found. Can only be raised when
                check_existence is `True`.
        """
        self.id = issue_id

        if not check_existence:
            return

        issue_id = get_db().execute(
            "SELECT id FROM issues WHERE id = ? LIMIT 1;",
            (self.id,)
        ).fetchone()

        if issue_id is None:
            raise IssueNotFound(issue_id)
        return

    @classmethod
    @lru_cache(maxsize=3)
    def from_volume_and_calc_number(
        cls,
        volume_id: int,
        calculated_issue_number: float
    ) -> Issue:
        """Create an instance based on the volume ID and calculated issue number
        of the issue. The existance of the volume is checked.

        Args:
            volume_id (int): The ID of the volume that the issue is in.
            calculated_issue_number (float): The calculated issue number of
                the issue.

        Raises:
            IssueNotFound: No issue found with the given arguments.

        Returns:
            Issue: The instance.
        """
        issue_id: Union[int, None] = get_db().execute("""
            SELECT id
            FROM issues
            WHERE volume_id = ?
                AND calculated_issue_number = ?
            LIMIT 1;
            """,
            (volume_id, calculated_issue_number)
        ).exists()

        if not issue_id:
            raise IssueNotFound(-1)

        return cls(issue_id, check_existence=True)

    def get_data(self) -> IssueData:
        """Get data about the issue.

        Returns:
            IssueData: The data.
        """
        data = get_db().execute(
            """
            SELECT
                id, volume_id, comicvine_id,
                issue_number, calculated_issue_number,
                title, date, description,
                monitored
            FROM issues
            WHERE id = ?
            LIMIT 1;
            """,
            (self.id,)
        ).fetchonedict() or {}

        return IssueData(
            **data,
            files=self.get_files()
        )

    def get_files(self) -> List[FileData]:
        """Get all files linked to the issue.

        Returns:
            List[FileData]: List of file data.
        """
        return FilesDB.fetch(issue_id=self.id)

    def __format_value(self, key: str, value: Any, from_public: bool) -> Any:
        """Check whether the value of an attribute is allowed and convert if
        needed.

        Args:
            key (str): Key of attribute.
            value (Any): Value of attribute.
            from_public (bool): If True, only allow attributes to be changed
                that are allowed to be changed by the user.

        Raises:
            KeyNotFound: Key doesn't exist or can't be changed.
            InvalidKeyValue: Value of the key is not allowed.

        Returns:
            Any: (Converted) Attribute value.
        """
        converted_value = value

        if from_public and key not in ('monitored',):
            raise KeyNotFound(key)

        if key == 'monitored' and not isinstance(converted_value, bool):
            raise InvalidKeyValue(key, value)

        return converted_value

    def update(
        self,
        data: Mapping[str, Any],
        from_public: bool = False
    ) -> None:
        """Change attributes of the issue, in a `dict.update()` type of way.

        Args:
            data (Mapping[str, Any]): The keys and their new values.

            from_public (bool, optional): If True, only allow attributes to be
                changed that are allowed to be changed by the user.
                Defaults to False.

        Raises:
            KeyNotFound: Key doesn't exist or can't be changed.
            InvalidKeyValue: Value of the key is not allowed.
        """
        issue_data = self.get_data()
        formatted_data = {}
        for key, value in data.items():
            formatted_data[key] = self.__format_value(key, value, from_public)

        changes = {
            key: {'from': getattr(issue_data, key), 'to': value}
            for key, value in formatted_data.items()
            if getattr(issue_data, key) != value
        }
        if not changes:
            return

        cursor = get_db()
        for key, value in (
            (key, change['to'])
            for key, change in changes.items()
        ):
            cursor.execute(
                f"UPDATE issues SET {key} = ? WHERE id = ?;",
                (value, self.id)
            )

        if from_public:
            monitored = changes.get('monitored', {}).get('to')
            record_activity(
                ActivityCategory.ISSUE,
                ActivityEventType.ISSUE_MONITORING_CHANGED,
                (
                    f"{'Enabled' if monitored else 'Disabled'} monitoring "
                    f'for issue #{issue_data.issue_number}'
                ),
                volume_id=issue_data.volume_id,
                issue_id=self.id,
                origin='user',
                details={'changes': changes},
                cursor=cursor
            )

        LOGGER.info(
            f'For issue {self.id}, changed: {formatted_data}'
        )
        return

    def delete(self) -> None:
        """Delete the issue from the database"""
        LOGGER.debug(
            "Deleting issue %d with CV ID %d",
            self.id, self.get_data().comicvine_id
        )
        FilesDB.delete_issue_linked_files(self.id)
        get_db().execute(
            "DELETE FROM issues WHERE id = ?;",
            (self.id,)
        )
        return


# region Volume
class Volume:
    def __init__(self, volume_id: int, check_existence: bool = False) -> None:
        """Create an instance.

        Args:
            volume_id (int): The ID of the volume.
            check_existence (bool, optional): Check whether the volume exists
                based on its ID.
                Defaults to False.

        Raises:
            VolumeNotFound: The volume was not found. Can only be raised when
                check_existence is `True`.
        """
        self.id = volume_id

        if not check_existence:
            return

        volume_id = get_db().execute(
            "SELECT id FROM volumes WHERE id = ? LIMIT 1;",
            (self.id,)
        ).fetchone()

        if volume_id is None:
            raise VolumeNotFound(volume_id)
        return

    def get_data(self) -> VolumeData:
        """Get data about the volume.

        Returns:
            VolumeData: The data.
        """
        data = get_db().execute(
            """
            SELECT
                id, comicvine_id,
                title, alt_title,
                year, publisher, volume_number,
                description, site_url,
                monitored, monitor_new_issues,
                root_folder, folder, custom_folder,
                special_version, special_version_locked,
                last_cv_fetch
            FROM volumes
            WHERE id = ?
            LIMIT 1;
            """,
            (self.id,)
        ).fetchonedict() or {}

        data["special_version"] = SpecialVersion(data["special_version"])

        return VolumeData(**data)

    def get_public_data(self) -> Dict[str, Any]:
        """Get data about the volume for the public to see (the API).

        Returns:
            Dict[str, Any]: The data.
        """
        volume_info = get_db().execute("""
            SELECT
                v.id, comicvine_id,
                title, year, publisher,
                volume_number,
                special_version, special_version_locked,
                description, site_url,
                monitored, monitor_new_issues,
                v.folder, root_folder,
                rf.folder AS root_folder_path,
                (
                    SELECT COUNT(*)
                    FROM issues
                    WHERE volume_id = v.id
                ) AS issue_count,
                (
                    SELECT COUNT(DISTINCT issue_id)
                    FROM issues i
                    INNER JOIN issues_files if
                    ON i.id = if.issue_id
                    WHERE volume_id = v.id
                ) AS issues_downloaded,
                (
                    SELECT SUM(size) FROM (
                        SELECT DISTINCT f.id, size
                        FROM issues i
                        INNER JOIN issues_files if
                        INNER JOIN files f
                        ON i.id = if.issue_id
                            AND if.file_id = f.id
                        WHERE volume_id = v.id
                    )
                ) AS total_size
            FROM volumes v
            INNER JOIN root_folders rf
            ON v.root_folder = rf.id
            WHERE v.id = ?
            LIMIT 1;
            """,
            (self.id,)
        ).fetchonedict() or {}

        volume_info['volume_folder'] = relpath(
            volume_info['folder'],
            volume_info['root_folder_path']
        )
        del volume_info['root_folder_path']

        volume_info['issues'] = [i.todict() for i in self.get_issues()]
        volume_info['general_files'] = self.get_general_files()

        return volume_info

    # Alias, better in one-liners
    # vd = Volume Data
    @property
    def vd(self) -> VolumeData:
        return self.get_data()

    def get_cover(self) -> BytesIO:
        """Get the cover of the volume.

        Returns:
            BytesIO: The cover.
        """
        cover = get_db().execute(
            "SELECT cover FROM volumes_covers WHERE volume_id = ? LIMIT 1",
            (self.id,)
        ).fetchone()[0]
        return BytesIO(cover)

    def get_ending_year(self) -> Union[int, None]:
        """Get the year of the last issue that has a release date.

        Returns:
            Union[int, None]: The release year of the last issue with a release
                date set. `None` if there is no issue or no issue with a release
                date.
        """
        last_issue_date = get_db().execute("""
            SELECT MAX(date) AS last_issue_date
            FROM issues
            WHERE volume_id = ?;
            """,
            (self.id,)
        ).exists()

        return extract_year_from_date(last_issue_date)

    def get_issue(self, issue_id: int) -> Issue:
        """Get an issue from the volume based on its issue ID. It's checked that
        the issue exists and is part of the volume.

        Args:
            issue_id (int): The ID of the issue.

        Raises:
            IssueNotFound: Issue doesn't exist or isn't part of this volume.

        Returns:
            Issue: The issue instance.
        """
        issue = Issue(issue_id, check_existence=True)
        if issue.get_data().volume_id != self.id:
            raise IssueNotFound(issue_id)
        return issue

    def get_issue_from_number(self, calculated_issue_number: float) -> Issue:
        """Get an issue from the volume based on its calculated issue number.
        It's checked that the issue exists and is part of the volume.

        Args:
            calculated_issue_number (float): The calculated issue number of the
                issue.

        Raises:
            IssueNotFound: Issue doesn't exist or isn't part of this volume.

        Returns:
            Issue: The issue instance.
        """
        return Issue.from_volume_and_calc_number(
            self.id,
            calculated_issue_number
        )

    def get_issues(self, _skip_files: bool = False) -> List[IssueData]:
        """Get a list of the issues that are in the volume.

        Args:
            _skip_files (bool, optional): Don't fetch the files matched to
                each issue. Saves quite a bit of time.
                Defaults to False.

        Returns:
            List[IssueData]: The list of issues.
        """
        cursor = get_db()
        issues = cursor.execute("""
            SELECT
                id, volume_id, comicvine_id,
                issue_number, calculated_issue_number,
                title, date, description,
                monitored
            FROM issues
            WHERE volume_id = ?
            ORDER BY date, calculated_issue_number
            """,
            (self.id,)
        ).fetchalldict()

        file_mapping: Dict[int, List[FileData]] = {}
        if not _skip_files:
            cursor.execute("""
                SELECT i.id AS issue_id, f.id AS file_id, filepath, size
                FROM files f
                INNER JOIN issues_files if
                    ON f.id = if.file_id
                INNER JOIN issues i
                    ON if.issue_id = i.id
                WHERE i.volume_id = ?
                ORDER BY filepath;
                """,
                (self.id,)
            )
            for file in cursor:
                file_mapping.setdefault(file[0], []).append({
                    "id": file["file_id"],
                    "filepath": file["filepath"],
                    "size": file["size"]
                })

        result = [
            IssueData(
                **i,
                files=file_mapping.get(i["id"], [])
            )
            for i in issues
        ]
        return result

    def get_open_issues(self) -> List[Tuple[int, float]]:
        """Get the issues that are not matched to a file and are monitored.

        Returns:
            List[Tuple[int, float]]: The ID and calculated issue number of
                the open issues.
        """
        return get_db().execute(
            """
            SELECT i.id, i.calculated_issue_number
            FROM issues i
            LEFT JOIN issues_files if
            ON i.id = if.issue_id
            WHERE
                file_id IS NULL
                AND volume_id = ?
                AND monitored = 1;
            """,
            (self.id,)
        ).fetchall()

    def get_all_files(self) -> List[FileData]:
        """Get the files and general files matched to the volume.

        Returns:
            List[FileData]: List of files.
        """
        result = FilesDB.fetch(volume_id=self.id)
        result.extend(GeneralFilesDB.fetch(self.id))
        return result

    def get_general_files(self) -> List[GeneralFileData]:
        """Get the general files linked to the volume.

        Returns:
            List[GeneralFileData]: The general files.
        """
        return GeneralFilesDB.fetch(self.id)

    def __format_value(self, key: str, value: Any, from_public: bool) -> Any:
        """Check whether the value of an attribute is allowed and convert if
        needed.

        Args:
            key (str): Key of attribute.
            value (Any): Value of attribute.
            from_public (bool): If True, only allow attributes to be changed
                that are allowed to be changed by the user.

        Raises:
            KeyNotFound: Key doesn't exist or can't be changed.
            InvalidKeyValue: Value of the key is not allowed.

        Returns:
            Any: (Converted) Attribute value.
        """
        if from_public:
            key_collection = (
                'monitored',
                'monitor_new_issues',
                'special_version',
                'special_version_locked'
            )

        else:
            key_collection = VolumeData.__annotations__.keys()

        # Confirm that key exists
        if key not in key_collection:
            raise KeyNotFound(key)

        key_data = VolumeData.__dataclass_fields__[key]

        if issubclass(key_data.type, BaseEnum):
            # Convert string to Enum value
            try:
                value = key_data.type(value)
            except ValueError:
                raise InvalidKeyValue(key, value)

        # Confirm data type of submitted value
        if not isinstance(value, key_data.type):
            raise InvalidKeyValue(key, value)

        return value

    def update(
        self,
        data: Mapping[str, Any],
        from_public: bool = False
    ) -> None:
        """Change attributes of the volume, in a `dict.update()` type of way.

        Args:
            data (Mapping[str, Any]): The keys and their new values.

            from_public (bool, optional): If True, only allow attributes to be
                changed that are allowed to be changed by the user.
                Defaults to False.

        Raises:
            KeyNotFound: Key doesn't exist or can't be changed.
            InvalidKeyValue: Value of the key is not allowed.
        """
        formatted_data = {
            key: self.__format_value(key, value, from_public)
            for key, value in data.items()
        }

        volume_data = self.get_data()
        changes = {
            key: {'from': getattr(volume_data, key), 'to': value}
            for key, value in formatted_data.items()
            if getattr(volume_data, key) != value
        }
        if not changes:
            return

        cursor = get_db()
        for key, value in (
            (key, change['to'])
            for key, change in changes.items()
        ):
            cursor.execute(
                f"UPDATE volumes SET {key} = ? WHERE id = ?;",
                (value, self.id)
            )

        if from_public:
            monitored = changes.get('monitored')
            if monitored and len(changes) == 1:
                event_type = ActivityEventType.VOLUME_MONITORING_CHANGED
                summary = (
                    f"{'Enabled' if monitored['to'] else 'Disabled'} "
                    'volume monitoring'
                )
            else:
                event_type = ActivityEventType.VOLUME_UPDATED
                fields = ', '.join(
                    key.replace('_', ' ')
                    for key in changes
                )
                summary = f'Updated volume settings: {fields}'

            record_activity(
                ActivityCategory.VOLUME,
                event_type,
                summary,
                volume_id=self.id,
                origin='user',
                details={'changes': changes},
                cursor=cursor
            )

        LOGGER.info(
            f'For volume {self.id}, changed: {formatted_data}'
        )

        return

    def update_cover(self, cover: bytes) -> None:
        """Change the cover of the volume.

        Args:
            cover (bytes): The new cover image.
        """
        get_db().execute(
            """
            UPDATE volumes_covers
            SET cover = ?
            WHERE volume_id = ?;
            """,
            (cover, self.id)
        )
        return

    def apply_monitor_scheme(
        self,
        monitoring_scheme: MonitorScheme,
        record_event: bool = True
    ) -> None:
        """Apply a monitoring scheme to the issues of the volume.

        Args:
            monitoring_scheme (MonitorScheme): The monitoring scheme to apply.
        """
        cursor = get_db()
        issue_count, monitored_before = cursor.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(monitored), 0)
            FROM issues
            WHERE volume_id = ?;
            """,
            (self.id,)
        ).fetchone()

        if monitoring_scheme == MonitorScheme.NONE:
            cursor.execute("""
                UPDATE issues
                SET monitored = 0
                WHERE volume_id = ?;
                """,
                (self.id,)
            )

        elif monitoring_scheme == MonitorScheme.MISSING:
            cursor.execute("""
                WITH missing_issues AS (
                    SELECT id
                    FROM issues i
                    LEFT JOIN issues_files if
                    ON i.id = if.issue_id
                    WHERE volume_id = ?
                        AND if.issue_id IS NULL
                )
                UPDATE issues
                SET monitored = 0
                WHERE
                    volume_id = ?
                    AND id NOT IN missing_issues;
                """,
                (self.id, self.id)
            )

        elif monitoring_scheme == MonitorScheme.ALL:
            cursor.execute("""
                UPDATE issues
                SET monitored = 1
                WHERE volume_id = ?;
                """,
                (self.id,)
            )

        else:
            assert_never(monitoring_scheme)

        monitored_after = cursor.execute(
            """
            SELECT COALESCE(SUM(monitored), 0)
            FROM issues
            WHERE volume_id = ?;
            """,
            (self.id,)
        ).fetchone()[0]
        if record_event and monitored_before != monitored_after:
            record_activity(
                ActivityCategory.ISSUE,
                ActivityEventType.ISSUE_MONITORING_CHANGED,
                (
                    f'Applied {monitoring_scheme.value} monitoring scheme: '
                    f'{monitored_after} of {issue_count} issues monitored'
                ),
                volume_id=self.id,
                origin='user',
                details={
                    'scheme': monitoring_scheme.value,
                    'monitored_before': monitored_before,
                    'monitored_after': monitored_after,
                    'issue_count': issue_count
                },
                cursor=cursor
            )

        return

    def __volume_folder_used_by_other_volume(
        self,
        volume_folder: str
    ) -> bool:
        """Check whether the given volume folder is used by another volume. I.e.
        whether two volumes use the same volume folder.

        Args:
            volume_folder (str): The volume folder to check for.

        Returns:
            bool: Whether it's also used by another volume.
        """
        return get_db().execute(
            "SELECT 1 FROM volumes WHERE folder = ? AND id != ? LIMIT 1;",
            (volume_folder, self.id)
        ).exists() is not None

    def change_root_folder(self, new_root_folder_id: int) -> None:
        """Change the root folder of the volume. Updates the path in the
        database, creates the new folder (if needed) and moves the files (if any).

        Args:
            new_root_folder_id (int): The root folder ID of the new root folder.
        """
        volume_data = self.get_data()
        if volume_data.root_folder == new_root_folder_id:
            return

        root_folders = RootFolders()
        current_root_folder = root_folders[volume_data.root_folder]
        new_root_folder = root_folders[new_root_folder_id]

        LOGGER.info(
            "Changing root folder of volume %d from %s to %s",
            self.id, current_root_folder, new_root_folder
        )

        # Move files
        file_changes = change_basefolder(
            (f["filepath"] for f in self.get_all_files()),
            current_root_folder,
            new_root_folder
        )
        for old_name, new_name in file_changes.items():
            rename_file(
                old_name,
                new_name
            )
        if isdir(volume_data.folder):
            delete_empty_child_folders(volume_data.folder)

        # Update filepaths in database
        FilesDB.update_filepaths(file_changes)

        # Update volume data in database
        new_folder = change_basefolder(
            (volume_data.folder,),
            current_root_folder,
            new_root_folder
        )[volume_data.folder]
        self.update({
            'root_folder': new_root_folder_id,
            'folder': new_folder
        })

        if not self.__volume_folder_used_by_other_volume(volume_data.folder):
            # Current volume folder is not also used by another volume,
            # so we can delete it if empty.
            delete_empty_parent_folders(
                volume_data.folder,
                current_root_folder
            )

        if Settings().sv.create_empty_volume_folders:
            create_folder(new_folder)

        mass_process_files(self.id)

        record_activity(
            ActivityCategory.VOLUME,
            ActivityEventType.VOLUME_ROOT_FOLDER_CHANGED,
            'Changed root folder',
            volume_id=self.id,
            origin='user',
            details={
                'from': str(current_root_folder),
                'to': str(new_root_folder),
                'files_moved': len(file_changes)
            }
        )

        return

    def change_volume_folder(
        self,
        new_volume_folder: Union[str, None]
    ) -> None:
        """Change the volume folder of the volume. Updates the path in the
        database, creates the new folder (if needed) and moves the files (if any).

        Args:
            new_volume_folder (Union[str, None]): The new folder, or `None` if
                the default folder should be generated and used.
        """
        from backend.implementations.naming import generate_volume_folder_path

        volume_data = self.get_data()
        root_folder = RootFolders()[volume_data.root_folder]
        current_volume_folder = volume_data.folder
        new_volume_folder = generate_volume_folder_path(
            root_folder, volume_data, new_volume_folder
        )

        if current_volume_folder == new_volume_folder:
            return

        LOGGER.info(
            "Changing volume folder of volume %d from %s to %s",
            self.id, current_volume_folder, new_volume_folder
        )

        # Move files
        file_changes = change_basefolder(
            (f["filepath"] for f in self.get_all_files()),
            current_volume_folder,
            new_volume_folder
        )
        for old_name, new_name in file_changes.items():
            rename_file(
                old_name,
                new_name
            )
        if isdir(current_volume_folder):
            delete_empty_child_folders(current_volume_folder)

        # Update filepaths in database
        FilesDB.update_filepaths(file_changes)

        # Update volume data in database
        self.update({
            'custom_folder': new_volume_folder is not None,
            'folder': new_volume_folder
        })

        if Settings().sv.create_empty_volume_folders:
            create_folder(new_volume_folder)

        # Delete old folder if possible
        if isdir(new_volume_folder) and folder_is_inside_folder(
            new_volume_folder, current_volume_folder
        ):
            # New folder is parent of current folder,
            # so delete up to new folder.
            delete_empty_parent_folders(
                current_volume_folder,
                new_volume_folder
            )

        elif not self.__volume_folder_used_by_other_volume(
            current_volume_folder
        ):
            # Current volume folder is not also used by another volume,
            # so we can delete it if empty.
            delete_empty_parent_folders(
                current_volume_folder,
                root_folder
            )

        mass_process_files(self.id)

        record_activity(
            ActivityCategory.VOLUME,
            ActivityEventType.VOLUME_FOLDER_CHANGED,
            'Changed volume folder',
            volume_id=self.id,
            origin='user',
            details={
                'from': current_volume_folder,
                'to': new_volume_folder,
                'files_moved': len(file_changes)
            }
        )

        return

    def delete(self, delete_folder: bool = False) -> None:
        """Delete the volume from the library.

        Args:
            delete_folder (bool, optional): Also delete the volume folder and
                its contents.
                Defaults to False.

        Raises:
            TaskForVolumeRunning: There is a task queued for the volume.
            VolumeDownloadedFor: There is a download queued for the volume.
        """
        from backend.features.download_queue import DownloadHandler
        from backend.features.tasks import TaskHandler

        LOGGER.info(
            "Deleting volume %d with delete_folder=%s",
            self.id, delete_folder
        )

        # Check if there is no task running for the volume
        if TaskHandler.task_for_volume_running(self.id):
            raise TaskForVolumeRunning(self.id)

        # Check if nothing is downloading for the volume
        if DownloadHandler().download_for_volume_queued(self.id):
            raise VolumeDownloadedFor(self.id)

        volume_data = self.get_data()
        issue_count = len(self.get_issues())
        files = self.get_all_files()
        if delete_folder and exists(volume_data.folder):
            for f in files:
                delete_file_folder(f["filepath"])

            delete_empty_child_folders(volume_data.folder)
            delete_empty_parent_folders(
                volume_data.folder,
                RootFolders()[volume_data.root_folder]
            )

        cursor = get_db()
        with cursor:
            # ON DELETE CASCADE will take care of file and issue links.
            FilesDB.delete_linked_files(self.id)
            cursor.execute(
                """
                UPDATE activity_history
                SET volume_id = NULL, issue_id = NULL
                WHERE volume_id = ?;
                """,
                (self.id,)
            )
            cursor.execute("DELETE FROM volumes WHERE id = ?", (self.id,))
            record_activity(
                ActivityCategory.VOLUME,
                ActivityEventType.VOLUME_DELETED,
                f'Deleted {volume_data.title}',
                origin='user',
                snapshot={
                    'volume_comicvine_id': volume_data.comicvine_id,
                    'volume_title': volume_data.title,
                    'volume_year': volume_data.year
                },
                details={
                    'deleted_volume_id': self.id,
                    'delete_folder': delete_folder,
                    'issue_count': issue_count,
                    'file_count': len(files)
                },
                cursor=cursor
            )

        return

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__}; ID {self.id}>'


# region Library
class Library:
    @staticmethod
    def _get_filter_clause(
        filter: Union[LibraryFilter, int, None],
        publisher: Union[str, None] = None,
        has_description: Union[bool, None] = None,
        status_filter: Union[LibraryStatusFilter, None] = None,
        date_filter: Union[LibraryDateFilter, None] = None
    ) -> Tuple[str, List[Any]]:
        """Build a SQL WHERE clause for the given library filter.
        Uses EXISTS predicates instead of computed column aliases
        to ensure SQLite compatibility.

        Args:
            filter: The filter to apply.

        Returns:
            Tuple[str, List[Any]]: The WHERE clause and SQL parameters.
        """
        clauses: List[str] = []
        params: List[Any] = []

        legacy_status_filters = {
            LibraryFilter.WANTED: LibraryStatusFilter.MISSING_MONITORED,
            LibraryFilter.MISSING_MONITORED:
                LibraryStatusFilter.MISSING_MONITORED,
            LibraryFilter.MISSING: LibraryStatusFilter.MISSING,
            LibraryFilter.MONITORED: LibraryStatusFilter.MONITORED,
            LibraryFilter.UNMONITORED: LibraryStatusFilter.UNMONITORED,
            LibraryFilter.COMPLETE: LibraryStatusFilter.COMPLETE,
            LibraryFilter.DOWNLOADED: LibraryStatusFilter.DOWNLOADED,
            LibraryFilter.NO_ISSUES: LibraryStatusFilter.NO_ISSUES
        }
        legacy_date_filters = {
            LibraryFilter.RECENTLY_ADDED_7:
                LibraryDateFilter.RECENTLY_ADDED_7,
            LibraryFilter.RECENTLY_ADDED_30:
                LibraryDateFilter.RECENTLY_ADDED_30,
            LibraryFilter.RECENTLY_ADDED_90:
                LibraryDateFilter.RECENTLY_ADDED_90,
            LibraryFilter.RECENTLY_ADDED_180:
                LibraryDateFilter.RECENTLY_ADDED_180,
            LibraryFilter.RECENTLY_ADDED_365:
                LibraryDateFilter.RECENTLY_ADDED_365,
            LibraryFilter.RECENTLY_RELEASED_7:
                LibraryDateFilter.RECENTLY_RELEASED_7,
            LibraryFilter.RECENTLY_RELEASED_30:
                LibraryDateFilter.RECENTLY_RELEASED_30,
            LibraryFilter.RECENTLY_RELEASED_90:
                LibraryDateFilter.RECENTLY_RELEASED_90,
            LibraryFilter.RECENTLY_RELEASED_180:
                LibraryDateFilter.RECENTLY_RELEASED_180,
            LibraryFilter.RECENTLY_RELEASED_365:
                LibraryDateFilter.RECENTLY_RELEASED_365
        }

        if isinstance(filter, int):
            clauses.append("comicvine_id = ?")
            params.append(filter)

        elif filter is not None:
            if status_filter is not None or date_filter is not None:
                raise InvalidKeyValue('filter', filter.value)

            if filter in legacy_status_filters:
                status_filter = legacy_status_filters[filter]
            elif filter in legacy_date_filters:
                date_filter = legacy_date_filters[filter]
            elif filter == LibraryFilter.HAS_DESCRIPTION:
                has_description = True

        if status_filter == LibraryStatusFilter.MONITORED:
            clauses.append("monitored = 1")

        elif status_filter == LibraryStatusFilter.UNMONITORED:
            clauses.append("monitored = 0")

        elif status_filter == LibraryStatusFilter.MISSING_MONITORED:
            clauses.append("""EXISTS (
                SELECT 1 FROM issues i
                LEFT JOIN issues_files if ON i.id = if.issue_id
                WHERE i.volume_id = volumes.id
                    AND i.monitored = 1
                    AND if.issue_id IS NULL
            )""")

        elif status_filter == LibraryStatusFilter.MISSING:
            clauses.append("""EXISTS (
                SELECT 1 FROM issues i
                LEFT JOIN issues_files if ON i.id = if.issue_id
                WHERE i.volume_id = volumes.id
                    AND if.issue_id IS NULL
            )""")

        elif status_filter == LibraryStatusFilter.COMPLETE:
            clauses.append("""NOT EXISTS (
                SELECT 1 FROM issues i
                LEFT JOIN issues_files if ON i.id = if.issue_id
                WHERE i.volume_id = volumes.id
                    AND i.monitored = 1
                    AND if.issue_id IS NULL
            )""")
            clauses.append("""EXISTS (
                SELECT 1 FROM issues
                WHERE volume_id = volumes.id
                    AND monitored = 1
            )""")

        elif status_filter == LibraryStatusFilter.NO_ISSUES:
            clauses.append("""NOT EXISTS (
                SELECT 1 FROM issues
                WHERE volume_id = volumes.id
            )""")

        elif status_filter == LibraryStatusFilter.PARTIALLY_DOWNLOADED:
            clauses.append("""EXISTS (
                SELECT 1 FROM issues i
                INNER JOIN issues_files if ON i.id = if.issue_id
                WHERE i.volume_id = volumes.id
            )""")
            clauses.append("""EXISTS (
                SELECT 1 FROM issues i
                LEFT JOIN issues_files if ON i.id = if.issue_id
                WHERE i.volume_id = volumes.id
                    AND if.issue_id IS NULL
            )""")

        elif status_filter == LibraryStatusFilter.DOWNLOADED:
            clauses.append("""EXISTS (
                SELECT 1 FROM issues i
                INNER JOIN issues_files if ON i.id = if.issue_id
                WHERE i.volume_id = volumes.id
            )""")

        recently_added_days = {
            LibraryDateFilter.RECENTLY_ADDED_7: 7,
            LibraryDateFilter.RECENTLY_ADDED_30: 30,
            LibraryDateFilter.RECENTLY_ADDED_90: 90,
            LibraryDateFilter.RECENTLY_ADDED_180: 180,
            LibraryDateFilter.RECENTLY_ADDED_365: 365
        }
        recently_released_days = {
            LibraryDateFilter.RECENTLY_RELEASED_7: 7,
            LibraryDateFilter.RECENTLY_RELEASED_30: 30,
            LibraryDateFilter.RECENTLY_RELEASED_90: 90,
            LibraryDateFilter.RECENTLY_RELEASED_180: 180,
            LibraryDateFilter.RECENTLY_RELEASED_365: 365
        }
        if (
            date_filter is not None
            and date_filter in recently_added_days
        ):
            days = recently_added_days[date_filter]
            clauses.append("created_at >= ?")
            params.append(round(time()) - days * 24 * 60 * 60)

        elif (
            date_filter is not None
            and date_filter in recently_released_days
        ):
            days = recently_released_days[date_filter]
            clauses.append("""EXISTS (
                SELECT 1 FROM issues
                WHERE volume_id = volumes.id
                    AND date IS NOT NULL
                    AND date != ''
                    AND date(date) >= date('now', ?)
            )""")
            params.append(f'-{days} days')

        if publisher:
            clauses.append("publisher = ?")
            params.append(publisher)

        if has_description is True:
            clauses.append("description IS NOT NULL")
            clauses.append("TRIM(description) != ''")

        elif has_description is False:
            clauses.append("(description IS NULL OR TRIM(description) = '')")

        if not clauses:
            return '', []

        return f"WHERE {' AND '.join(clauses)}", params

    @classmethod
    def get_volume_count(
        cls,
        filter: Union[LibraryFilter, int, None] = None,
        query: str = '',
        publisher: Union[str, None] = None,
        has_description: Union[bool, None] = None,
        status_filter: Union[LibraryStatusFilter, None] = None,
        date_filter: Union[LibraryDateFilter, None] = None
    ) -> int:
        """Get the total number of volumes matching the filter and query.
        Uses an efficient COUNT query without fetching full data.

        Args:
            filter: The filter to apply.
            query (str): Search query to filter by title (LIKE match).

        Returns:
            int: The count of matching volumes.
        """
        sql_filter, params = cls._get_filter_clause(
            filter,
            publisher=publisher,
            has_description=has_description,
            status_filter=status_filter,
            date_filter=date_filter
        )

        if query:
            like_clause = "WHERE title LIKE ?"
            if sql_filter:
                like_clause = sql_filter + " AND title LIKE ?"
            result = get_db().execute(
                f"SELECT COUNT(*) FROM volumes {like_clause};",
                [*params, f'%{query}%']
            ).exists()
        else:
            result = get_db().execute(
                f"SELECT COUNT(*) FROM volumes {sql_filter};",
                params
            ).exists()

        return result or 0

    @classmethod
    def get_public_volumes(
        cls,
        sort: LibrarySorting = LibrarySorting.TITLE,
        filter: Union[LibraryFilter, int, None] = None,
        offset: int = 0,
        limit: int = 0,
        minimal: bool = False,
        publisher: Union[str, None] = None,
        has_description: Union[bool, None] = None,
        status_filter: Union[LibraryStatusFilter, None] = None,
        date_filter: Union[LibraryDateFilter, None] = None
    ) -> List[Dict[str, Any]]:
        """Get all the volumes in the library.

        Args:
            sort (LibrarySorting, optional): How to sort the list.
                Defaults to LibrarySorting.TITLE.

            filter (Union[LibraryFilter, None], optional): Apply a filter to
                the list if not `None`.
                Defaults to None.

            offset (int, optional): Number of volumes to skip.
                Defaults to 0.

            limit (int, optional): Maximum number of volumes to return.
                0 means no limit.
                Defaults to 0.

            minimal (bool, optional): If True, strip description from results.
                Defaults to False.

        Returns:
            List[Dict[str, Any]]: The list of volumes in the library.
        """
        sql_filter, filter_params = cls._get_filter_clause(
            filter,
            publisher=publisher,
            has_description=has_description,
            status_filter=status_filter,
            date_filter=date_filter
        )

        pagination = ''
        params: list = [*filter_params]
        if limit > 0:
            pagination = 'LIMIT ? OFFSET ?'
            params.extend([limit, offset])

        description_col = ",\n                description" if not minimal else ""

        volumes = get_db().execute(f"""
            WITH
                vol_issues AS (
                    SELECT id, monitored, date
                    FROM issues
                    WHERE volume_id = volumes.id
                ),
                issues_to_files AS (
                    SELECT issue_id, monitored, f.id, size
                    FROM issues i
                    INNER JOIN issues_files if
                    INNER JOIN files f
                    ON i.id = if.issue_id
                        AND if.file_id = f.id
                    WHERE volume_id = volumes.id
                )
            SELECT
                id, comicvine_id,
                title, year, publisher,
                volume_number{description_col},
                monitored, monitor_new_issues,
                folder,
                (
                    SELECT COUNT(id) FROM vol_issues
                ) AS issue_count,
                (
                    SELECT COUNT(id) FROM vol_issues WHERE monitored = 1
                ) AS issue_count_monitored,
                (
                    SELECT COUNT(DISTINCT issue_id) FROM issues_to_files
                ) AS issues_downloaded,
                (
                    SELECT COUNT(DISTINCT issue_id) FROM issues_to_files WHERE monitored = 1
                ) AS issues_downloaded_monitored,
                (
                    SELECT SUM(size) FROM (SELECT DISTINCT id, size FROM issues_to_files)
                ) AS total_size
            FROM volumes
            {sql_filter}
            ORDER BY {sort.value}
            {pagination};
            """,
            params
        ).fetchalldict()

        return volumes

    @classmethod
    def search(
        cls,
        query: str,
        sort: LibrarySorting = LibrarySorting.TITLE,
        filter: Union[LibraryFilter, None] = None,
        offset: int = 0,
        limit: int = 0,
        minimal: bool = False,
        publisher: Union[str, None] = None,
        has_description: Union[bool, None] = None,
        status_filter: Union[LibraryStatusFilter, None] = None,
        date_filter: Union[LibraryDateFilter, None] = None
    ) -> List[Dict[str, Any]]:
        """Search in the library with a query.

        Args:
            query (str): The query to search with.

            sort (LibrarySorting, optional): How to sort the list.
                Defaults to LibrarySorting.TITLE.

            filter (Union[LibraryFilters, None], optional): Apply a filter to
                the list if not `None`.
                Defaults to None.

            offset (int, optional): Number of results to skip.
                Defaults to 0.

            limit (int, optional): Max results to return. 0 = no limit.
                Defaults to 0.

            minimal (bool, optional): Strip description from results.
                Defaults to False.

        Returns:
            List[Dict[str, Any]]: The resulting list of matching volumes
                in the library.
        """
        if query.startswith(('4050-', 'cv:')):
            try:
                cv_id = to_number_cv_id((query,))[0]
                volumes = cls.get_public_volumes(
                    sort, cv_id,
                    offset=offset,
                    limit=limit,
                    minimal=minimal,
                    publisher=publisher,
                    has_description=has_description,
                    status_filter=status_filter,
                    date_filter=date_filter
                )

            except ValueError:
                volumes = []

        else:
            # Get all volumes (with filter) and apply title matching.
            # No LIMIT/OFFSET at DB level since match_title is Python-side.
            all_volumes = cls.get_public_volumes(
                sort,
                filter,
                minimal=minimal,
                publisher=publisher,
                has_description=has_description,
                status_filter=status_filter,
                date_filter=date_filter
            )
            matched = [
                v
                for v in all_volumes
                if match_title(v['title'], query, allow_contains=True)
            ]

            # Apply pagination in Python
            if limit > 0:
                volumes = matched[offset:offset + limit]
            elif offset > 0:
                volumes = matched[offset:]
            else:
                volumes = matched

        return volumes

    @classmethod
    def search_count(
        cls,
        query: str,
        filter: Union[LibraryFilter, None] = None,
        publisher: Union[str, None] = None,
        has_description: Union[bool, None] = None,
        status_filter: Union[LibraryStatusFilter, None] = None,
        date_filter: Union[LibraryDateFilter, None] = None
    ) -> int:
        """Count search results without fetching full data.

        Args:
            query (str): The search query.
            filter: The filter to apply.

        Returns:
            int: Number of matching volumes.
        """
        if query.startswith(('4050-', 'cv:')):
            try:
                cv_id = to_number_cv_id((query,))[0]
                return cls.get_volume_count(
                    cv_id,
                    publisher=publisher,
                    has_description=has_description,
                    status_filter=status_filter,
                    date_filter=date_filter
                )
            except ValueError:
                return 0

        # For text search, we need Python-side match_title,
        # so we must fetch titles and count matches
        sql_filter, params = cls._get_filter_clause(
            filter,
            publisher=publisher,
            has_description=has_description,
            status_filter=status_filter,
            date_filter=date_filter
        )
        titles = get_db().execute(
            f"SELECT title FROM volumes {sql_filter};",
            params
        ).fetchall()
        return sum(
            1 for (title,) in titles
            if match_title(title, query, allow_contains=True)
        )

    @classmethod
    def get_publishers(cls) -> List[str]:
        """Get distinct non-empty publisher names for quick filtering."""
        result = get_db().execute(
            """
            SELECT DISTINCT publisher
            FROM volumes
            WHERE publisher IS NOT NULL
                AND TRIM(publisher) != ''
            ORDER BY publisher COLLATE NOCASE;
            """
        ).fetchall()
        return [p[0] for p in result]

    @classmethod
    def get_stats(cls) -> Dict[str, int]:
        """Get library statistics.

        Returns:
            Dict[str, int]: The statistics.
        """
        result = get_db().execute("""
            WITH v AS (
                SELECT COUNT(*) AS volumes,
                    SUM(monitored) AS monitored
                FROM volumes
            )
            SELECT
                v.volumes,
                v.monitored,
                v.volumes - v.monitored AS unmonitored,
                (SELECT COUNT(*) FROM issues) AS issues,
                (SELECT COUNT(DISTINCT issue_id) FROM issues_files) AS downloaded_issues,
                (SELECT COUNT(*) FROM files) AS files,
                (SELECT IFNULL(SUM(size), 0) FROM files) AS total_file_size
            FROM v;
        """).fetchonedict() or {}
        return result

    @classmethod
    def get_volumes(cls) -> List[int]:
        """Get a list of the IDs of all the volumes.

        Returns:
            List[int]: The list of IDs.
        """
        return first_of_subarrays(get_db().execute(
            "SELECT id FROM volumes;"
        ))

    @classmethod
    def get_volume(cls, volume_id: int) -> Volume:
        """Get a volume from the library.

        Args:
            volume_id (int): The ID of the volume.

        Raises:
            VolumeNotFound: The ID doesn't map to any volume in the library.

        Returns:
            Volume: The volume.
        """
        return Volume(volume_id, check_existence=True)

    @classmethod
    def get_issue(cls, issue_id: int) -> Issue:
        """Get an issue from the library.

        Args:
            issue_id (int): The ID of the issue.

        Raises:
            IssueNotFound: The ID doesn't map to any issue in the library.

        Returns:
            Issue: The issue.
        """
        return Issue(issue_id, check_existence=True)

    @classmethod
    def _cv_to_id(cls, comicvine_id: int) -> Union[int, None]:
        """Find the volume ID based on the CV ID.

        Args:
            comicvine_id (int): The CV ID of the volume to check for.

        Returns:
            bool: The volume ID with the given CV ID, or `None` if not found.
        """
        return get_db().execute(
            "SELECT id FROM volumes WHERE comicvine_id = ? LIMIT 1;",
            (comicvine_id,)
        ).exists()

    @classmethod
    def add(
        cls,
        comicvine_id: int,
        root_folder_id: int,
        monitored: bool,
        monitor_scheme: MonitorScheme = MonitorScheme.ALL,
        monitor_new_issues: bool = True,
        volume_folder: Union[str, None] = None,
        special_version: Union[SpecialVersion, None] = None,
        auto_search: bool = False
    ) -> int:
        """Add a volume to the library.

        Args:
            comicvine_id (int): The CV ID of the volume.

            root_folder_id (int): The ID of the rootfolder in which
                the volume folder will be.

            monitored (bool): Whether the volume should be monitored.

            monitor_scheme (MonitorScheme, optional): Which issues to monitor.
                Defaults to `MonitorScheme.ALL`.

            monitor_new_issues (bool, optional): Whether to monitor new issues.
                Defaults to True.

            volume_folder (Union[str, None], optional): Custom volume folder.
                Defaults to None.

            special_version (Union[SpecialVersion, None], optional): Give `None`
                to let Kapowarr determine the special version ('auto').
                Otherwise, give a `SpecialVersion` to override and lock the
                special version state.

                Defaults to None.

            auto_search (bool, optional): Start an auto search for the volume
                after adding it.
                Defaults to False.

        Raises:
            RootFolderNotFound: The root folder with the given ID was not found.
            VolumeFolderInvalid: The volume folder is the parent or child of
                another volume folder.
            VolumeAlreadyAdded: The volume already exists in the library.
            CVRateLimitReached: The ComicVine API rate limit is reached.

        Returns:
            int: The ID of the new volume.
        """
        from backend.implementations.naming import generate_volume_folder_path

        LOGGER.info(
            'Adding a volume to the library: '
            'CV ID %d, RF ID %d, M %s, MS %s, MNI %s, VF %s, SV %s',
            comicvine_id,
            root_folder_id,
            monitored,
            monitor_scheme.value,
            monitor_new_issues,
            volume_folder,
            special_version
        )

        potential_volume_id = cls._cv_to_id(comicvine_id)
        if potential_volume_id:
            raise VolumeAlreadyAdded(comicvine_id, potential_volume_id)

        # Raises RootFolderNotFound when ID is invalid
        root_folder = RootFolders().get_one(root_folder_id)

        vd = run(ComicVine().fetch_volume(comicvine_id))

        cursor = get_db()
        with cursor:
            volume_id = cursor.execute(
                """
                INSERT INTO volumes(
                    comicvine_id,
                    title,
                    alt_title,
                    year,
                    publisher,
                    volume_number,
                    description,
                    site_url,
                    monitored,
                    monitor_new_issues,
                    root_folder,
                    custom_folder,
                    created_at,
                    last_cv_fetch,
                    special_version,
                    special_version_locked
                ) VALUES (
                    :comicvine_id, :title, :alt_title,
                    :year, :publisher, :volume_number, :description,
                    :site_url, :monitored, :monitor_new_issues,
                    :root_folder, :custom_folder,
                    :created_at,
                    :last_cv_fetch, :special_version, :special_version_locked
                );
                """,
                {
                    "comicvine_id": vd["comicvine_id"],
                    "title": vd["title"],
                    "alt_title": (vd["aliases"] or [None])[0],
                    "year": vd["year"],
                    "publisher": vd["publisher"],
                    "volume_number": vd["volume_number"],
                    "description": vd["description"],
                    "site_url": vd["site_url"],
                    "monitored": monitored,
                    "monitor_new_issues": monitor_new_issues,
                    "root_folder": root_folder.id,
                    "custom_folder": volume_folder is not None,
                    "created_at": round(time()),
                    "last_cv_fetch": round(time()),
                    "special_version": None,
                    "special_version_locked": special_version is not None
                }
            ).lastrowid

            cursor.execute(
                """
                INSERT INTO volumes_covers(volume_id, cover)
                VALUES (:volume_id, :cover);
                """,
                {
                    "volume_id": volume_id,
                    "cover": vd["cover"]
                }
            )

            cursor.executemany("""
                INSERT INTO issues(
                    volume_id,
                    comicvine_id,
                    issue_number,
                    calculated_issue_number,
                    title,
                    date,
                    description,
                    monitored
                ) VALUES (
                    :volume_id, :comicvine_id,
                    :issue_number, :calculated_issue_number,
                    :title, :date, :description,
                    :monitored
                );
                """,
                (
                    {
                        "volume_id": volume_id,
                        "comicvine_id": i["comicvine_id"],
                        "issue_number": i["issue_number"],
                        "calculated_issue_number": i["calculated_issue_number"],
                        "title": i["title"],
                        "date": i["date"],
                        "description": i["description"],
                        "monitored": True
                    }
                    for i in vd["issues"] or []
                )
            )

            volume = Volume(volume_id)

            if special_version is None:
                special_version = determine_special_version(volume.id)
            volume.update({'special_version': special_version})

            folder = generate_volume_folder_path(
                root_folder.folder,
                volume.get_data(),
                volume_folder
            )
            volume.update({'folder': folder})

            if Settings().sv.create_empty_volume_folders:
                create_folder(folder)
                scan_files(volume_id)

            volume.apply_monitor_scheme(monitor_scheme, record_event=False)

            mass_process_files(volume_id)

            record_activity(
                ActivityCategory.VOLUME,
                ActivityEventType.VOLUME_ADDED,
                f'Added {vd["title"]}',
                volume_id=volume_id,
                origin='user',
                details={
                    'monitor_scheme': monitor_scheme.value,
                    'monitored': monitored,
                    'monitor_new_issues': monitor_new_issues,
                    'root_folder': root_folder.folder,
                    'auto_search': auto_search
                },
                cursor=cursor
            )

        if auto_search:
            from backend.features.tasks import AutoSearchVolume, TaskHandler

            # Volume is accessed from different thread so changes must be saved,
            # but that's already done by the completion of the transaction above
            task = AutoSearchVolume(volume_id)
            TaskHandler().add(task)

        LOGGER.info(
            f'Added volume with CV ID {comicvine_id} and ID {volume_id}'
        )
        return volume_id


# region Refresh & Scan
def _get_activity_counts(cursor, volume_id: int) -> Tuple[int, int]:
    return cursor.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM issues WHERE volume_id = ?),
            (
                SELECT COUNT(*)
                FROM (
                    SELECT if.file_id
                    FROM issues_files if
                    INNER JOIN issues i ON i.id = if.issue_id
                    WHERE i.volume_id = ?
                    UNION
                    SELECT file_id
                    FROM volume_files
                    WHERE volume_id = ?
                )
            );
        """,
        (volume_id, volume_id, volume_id)
    ).fetchone()


def _describe_count_change(name: str, difference: int) -> str:
    action = 'added' if difference > 0 else 'removed'
    count = abs(difference)
    return f'{count} {name if count == 1 else name + "s"} {action}'


def determine_special_version(volume_id: int) -> SpecialVersion:
    """Determine what Special Version a volume is, if any.

    Args:
        volume_id (int): The ID of the volume to determine for.

    Returns:
        SpecialVersion: The result.
    """
    volume = Volume(volume_id)
    volume_data = volume.get_data()
    issues = volume.get_issues()
    one_issue = len(issues) == 1

    if issues and all(
        vol_regex.search(i.title or '')
        for i in issues
    ):
        return SpecialVersion.VOLUME_AS_ISSUE

    # Title-based format detection (applies regardless of issue count).
    # Check order: Omnibus > HC editions > TPB collections
    # (most specific format to broadest catch).
    if omnibus_regex.search(volume_data.title):
        return SpecialVersion.OMNIBUS

    if hc_edition_regex.search(volume_data.title):
        return SpecialVersion.HARD_COVER

    if tpb_regex.search(volume_data.title):
        return SpecialVersion.TPB

    if one_issue:
        if os_regex.search(volume_data.title):
            return SpecialVersion.ONE_SHOT

        if hc_regex.search(volume_data.title):
            return SpecialVersion.HARD_COVER

        issue_title = (issues[0].title or '').lower().replace(' ', '')

        if issue_title == 'omnibus':
            return SpecialVersion.OMNIBUS

        if issue_title in ('hc', 'hard-cover', 'hardcover'):
            return SpecialVersion.HARD_COVER

        if issue_title in ('os', 'one-shot', 'oneshot'):
            return SpecialVersion.ONE_SHOT

    if 'annual' in volume_data.title.lower():
        # Volume is annual
        return SpecialVersion.NORMAL

    if one_issue and volume_data.description:
        # Look for Special Version in first sentence of description. Only first
        # sentence as to avoid false hits, like referring to another volume that
        # is a Special Version in the description (e.g. "Included in the TPB")
        first_sentence = split_regex.split(volume_data.description)[0]
        first_sentence = remove_link_regex.sub('', first_sentence)

        if omnibus_regex.search(first_sentence):
            return SpecialVersion.OMNIBUS

        if os_regex.search(first_sentence):
            return SpecialVersion.ONE_SHOT

        if (
            hc_regex.search(first_sentence)
            or hc_edition_regex.search(first_sentence)
        ):
            return SpecialVersion.HARD_COVER

        if tpb_regex.search(first_sentence):
            return SpecialVersion.TPB

    if one_issue and issues[0].date:
        thirty_plus_days_ago = (
            datetime.now() - datetime.strptime(issues[0].date, "%Y-%m-%d")
            > THIRTY_DAYS
        )

        # The volume only has one issue. If the issue was released in the last
        # month, then we'll assume it's just a new volume that has only released
        # one issue up to this point. If the issue was released more than a
        # month ago, then we'll assume it's a TPB.
        if thirty_plus_days_ago:
            return SpecialVersion.TPB

    return SpecialVersion.NORMAL


def refresh_and_scan(
    volume_id: Union[int, None] = None,
    update_websocket: bool = False,
    allow_skipping: bool = True,
    volume_ids: Union[List[int], None] = None,
    stop_check: Union[Callable[[], bool], None] = None
) -> None:
    """Refresh and scan one or more volumes, which means to pull metadata from
    the online database and to scan for files.

    Args:
        volume_id (Union[int, None], optional): The ID of the volume if it is
            desired to only refresh and scan one. If left to `None`, all volumes
            are refreshed and scanned.
            Defaults to None.

        update_websocket (bool, optional): Send task progress updates over
            the websocket.
            Defaults to False.

        allow_skipping (bool, optional): Skip volumes that have been updated in
            the last 24 hours or that have the same amount of issues as what
            the metadata source reports.
            Defaults to True.
    """
    def should_stop() -> bool:
        return stop_check is not None and stop_check()

    if should_stop():
        return

    current_time = datetime.now()
    one_day_ago = current_time - ONE_DAY
    thirty_days_ago = current_time - THIRTY_DAYS

    cursor = get_db()
    if volume_id:
        cursor.execute("""
            SELECT comicvine_id, id, last_cv_fetch
            FROM volumes
            WHERE id = ?
            LIMIT 1;
            """,
            (volume_id,)
        )

    elif volume_ids is not None:
        if not volume_ids:
            return
        placeholders = ','.join('?' for _ in volume_ids)
        cursor.execute(
            f"""
            SELECT comicvine_id, id, last_cv_fetch
            FROM volumes
            WHERE id IN ({placeholders})
                AND last_cv_fetch <= ?
            ORDER BY last_cv_fetch ASC;
            """,
            (
                *volume_ids,
                one_day_ago.timestamp()
                if allow_skipping else
                current_time.timestamp(),
            )
        )

    else:
        cursor.execute("""
            SELECT comicvine_id, id, last_cv_fetch
            FROM volumes
            WHERE last_cv_fetch <= ?
            ORDER BY last_cv_fetch ASC;
            """,
            (
                one_day_ago.timestamp()
                if allow_skipping else
                current_time.timestamp(),
            )
        )

    cv_to_id_fetch: Dict[int, Tuple[int, int]] = {
        e["comicvine_id"]: (e["id"], e["last_cv_fetch"])
        for e in cursor
    }
    if not cv_to_id_fetch:
        return
    activity_counts_before = {
        local_id: _get_activity_counts(cursor, local_id)
        for local_id, _ in cv_to_id_fetch.values()
    }

    # Update volumes
    if should_stop():
        return
    cv = ComicVine()
    volume_datas = filtered_volume_datas = run(
        cv.fetch_volumes(tuple(cv_to_id_fetch.keys()))
    )
    if should_stop():
        return

    if not volume_id and allow_skipping:
        cv_id_to_issue_count: Dict[int, int] = dict(cursor.execute("""
            SELECT v.comicvine_id, COUNT(i.id)
            FROM volumes v
            LEFT JOIN issues i
            ON v.id = i.volume_id
            WHERE v.last_cv_fetch <= ?
            GROUP BY v.id;
            """,
            (one_day_ago.timestamp(),)
        ))

        filtered_volume_datas = [
            v
            for v in volume_datas
            if cv_id_to_issue_count[v["comicvine_id"]] != v["issue_count"]
            # Do a fetch anyway if it hasn't been done for 30 days
            or cv_to_id_fetch[v["comicvine_id"]][1] <= thirty_days_ago.timestamp()
        ]

    cursor.executemany(
        """
        UPDATE volumes
        SET
            title = :title,
            alt_title = :alt_title,
            year = :year,
            publisher = :publisher,
            volume_number = :volume_number,
            description = :description,
            site_url = :site_url,
            last_cv_fetch = :last_cv_fetch
        WHERE id = :id;
        """,
        ({
            "title": vd["title"],
            "alt_title": (vd["aliases"] or [None])[0],
            "year": vd["year"],
            "publisher": vd["publisher"],
            "volume_number": vd["volume_number"],
            "description": vd["description"],
            "site_url": vd["site_url"],
            "last_cv_fetch": current_time.timestamp(),

            "id": cv_to_id_fetch[vd["comicvine_id"]][0]
        }
            for vd in volume_datas
        ))

    cursor.executemany(
        """
        UPDATE volumes_covers
        SET
            cover = :cover
        WHERE volume_id = :volume_id;
        """,
        ({
            "volume_id": cv_to_id_fetch[vd["comicvine_id"]][0],
            "cover": vd["cover"]
        }
            for vd in volume_datas
        ))

    commit()
    if should_stop():
        return

    # Update issues
    issue_datas = run(cv.fetch_issues(
        tuple(vd["comicvine_id"] for vd in filtered_volume_datas)
    ))
    if should_stop():
        return
    monitor_issues_volume_ids: Set[int] = set(first_of_subarrays(cursor.execute(
        "SELECT id FROM volumes WHERE monitor_new_issues = 1;"
    )))
    cursor.executemany(
        """
        INSERT INTO issues(
            volume_id,
            comicvine_id,
            issue_number,
            calculated_issue_number,
            title,
            date,
            store_date,
            description,
            monitored
        ) VALUES (
            :volume_id, :comicvine_id, :issue_number, :calculated_issue_number,
            :title, :date, :store_date, :description, :monitored
        )
        ON CONFLICT(comicvine_id) DO
        UPDATE
        SET
            issue_number = :issue_number,
            calculated_issue_number = :calculated_issue_number,
            title = :title,
            date = :date,
            store_date = :store_date,
            description = :description;
        """,
        ({
            "volume_id": cv_to_id_fetch[isd["volume_id"]][0],
            "comicvine_id": isd["comicvine_id"],
            "issue_number": isd["issue_number"],
            "calculated_issue_number": isd["calculated_issue_number"] or 0.0,
            "title": isd["title"],
            "date": isd["date"],
            "store_date": isd.get("store_date"),
            "description": isd["description"],
            "monitored": cv_to_id_fetch[isd["volume_id"]][0] in monitor_issues_volume_ids
        }
            for isd in issue_datas
        ))

    commit()
    if should_stop():
        return

    # Delete issues from DB that aren't found in response
    volume_issues_fetched: Dict[int, Set[int]] = {}
    for isd in issue_datas:
        (volume_issues_fetched
            .setdefault(isd["volume_id"], set())
            .add(isd["comicvine_id"]))

    for vd in filtered_volume_datas:
        if len(volume_issues_fetched.get(
            vd["comicvine_id"]
        ) or tuple()) != vd["issue_count"]:
            continue

        # All issues of the volume have been fetched, which is not guaranteed
        # because of rate limits.
        issue_cv_to_id = dict(cursor.execute("""
            SELECT i.comicvine_id, i.id
            FROM issues i
            INNER JOIN volumes v
            ON i.volume_id = v.id
            WHERE v.comicvine_id = ?;
            """,
            (vd["comicvine_id"],)
        ).fetchall())
        for issue_cv, issue_id in issue_cv_to_id.items():
            if issue_cv not in volume_issues_fetched[vd["comicvine_id"]]:
                # Issue is in database but not in response, so remove
                Issue(issue_id).delete()
                commit()

    # Refresh Special Version
    updated_special_versions = tuple(
        {
            "special_version": determine_special_version(
                cv_to_id_fetch[vd["comicvine_id"]][0]
            ),
            "id": cv_to_id_fetch[vd["comicvine_id"]][0]
        }
        for vd in volume_datas
    )
    cursor.executemany("""
        UPDATE volumes
        SET special_version = :special_version
        WHERE id = :id AND special_version_locked = 0;
        """,
        updated_special_versions
    )

    commit()
    if should_stop():
        return

    # Scan for files
    if volume_id:
        scan_files(volume_id, update_websocket=update_websocket)

    else:
        v_ids = [
            (v[0], [], False, update_websocket)
            for v in cv_to_id_fetch.values()
        ]
        total_count = len(v_ids)

        if not total_count:
            return

        batch_size = min(
            Constants.DB_MAX_CONCURRENT_CONNECTIONS,
            total_count
        )
        scanned_count = 0
        for batch_start in range(0, total_count, batch_size):
            if should_stop():
                break
            batch = v_ids[batch_start:batch_start + batch_size]
            with PortablePool(max_processes=len(batch)) as pool:
                if update_websocket:
                    ws = WebSocket()
                    for _ in pool.istarmap_unordered(scan_files, batch):
                        scanned_count += 1
                        ws.emit(TaskStatusEvent(
                            'Scanned files for volume '
                            f'{scanned_count}/{total_count}'
                        ))

                else:
                    pool.starmap(scan_files, batch)
                    scanned_count += len(batch)

        if not should_stop():
            FilesDB.delete_unmatched_files()

    for local_id, _ in cv_to_id_fetch.values():
        issues_before, files_before = activity_counts_before[local_id]
        issues_after, files_after = _get_activity_counts(cursor, local_id)
        issue_difference = issues_after - issues_before
        file_difference = files_after - files_before
        if not issue_difference and not file_difference:
            continue

        changes = []
        if issue_difference:
            changes.append(_describe_count_change('issue', issue_difference))
        if file_difference:
            changes.append(_describe_count_change('file', file_difference))

        record_activity(
            ActivityCategory.VOLUME,
            ActivityEventType.VOLUME_SCAN_COMPLETED,
            f'Refreshed series: {", ".join(changes)}',
            volume_id=local_id,
            origin='system',
            details={
                'issues_before': issues_before,
                'issues_after': issues_after,
                'files_before': files_before,
                'files_after': files_after
            },
            cursor=cursor
        )

    return


def delete_issue_file(file_id: int) -> None:
    """Delete a file from the library and remove it from the filesystem.

    Args:
        file_id (int): The ID of the file to delete.
    """
    file_data = FilesDB.fetch(file_id=file_id)[0]
    volume_id = FilesDB.volume_of_file(file_data["filepath"])
    unmonitor_deleted_issues = Settings().sv.unmonitor_deleted_issues and volume_id

    if volume_id:
        vf = Library.get_volume(volume_id).vd.folder
        delete_file_folder(file_data["filepath"])
        delete_empty_parent_folders(dirname(file_data["filepath"]), vf)
    else:
        delete_file_folder(file_data["filepath"])

    cursor = get_db()
    matched_issue_ids: List[int] = first_of_subarrays(cursor.execute(
        "SELECT issue_id FROM issues_files WHERE file_id = ?;",
        (file_id,)
    ))
    not_downloaded_issues: List[int] = first_of_subarrays(cursor.execute("""
        WITH matched_file_counts AS (
            SELECT
                issue_id,
                COUNT(file_id) AS matched_file_count
            FROM issues_files
            WHERE issue_id IN (
                SELECT issue_id
                FROM issues_files
                WHERE file_id = ?
            )
            GROUP BY issue_id
        )
        SELECT issue_id
        FROM matched_file_counts
        WHERE matched_file_count = 1;
        """,
        (file_id,)
    ))

    if volume_id:
        WebSocket().emit(DownloadedStatusEvent(
            volume_id,
            not_downloaded_issues=not_downloaded_issues
        ))

    if unmonitor_deleted_issues:
        cursor.executemany(
            "UPDATE issues SET monitored = 0 WHERE id = ?;",
            ((i,) for i in not_downloaded_issues)
        )

    FilesDB.delete_file(file_id)

    issue_id = (
        matched_issue_ids[0]
        if len(matched_issue_ids) == 1
        else None
    )
    record_activity(
        ActivityCategory.FILE,
        ActivityEventType.FILE_DELETED,
        f'Deleted {basename(file_data["filepath"])}',
        volume_id=volume_id or None,
        issue_id=issue_id,
        origin='user',
        snapshot={'file_path': file_data['filepath']},
        details={
            'deleted_file_id': file_id,
            'affected_issue_ids': matched_issue_ids,
            'issues_unmonitored': (
                not_downloaded_issues if unmonitor_deleted_issues else []
            )
        },
        cursor=cursor
    )

    return
