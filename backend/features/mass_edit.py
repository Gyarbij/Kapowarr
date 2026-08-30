# -*- coding: utf-8 -*-

from typing import Any, List, Union

from backend.base.custom_exceptions import (
    InvalidKeyValue,
    KeyNotFound,
    RootFolderNotFound,
    VolumeDownloadedFor,
)
from backend.base.definitions import MassEditorAction, MonitorScheme
from backend.base.helpers import get_subclasses
from backend.base.logging import LOGGER
from backend.features.download_queue import DownloadHandler
from backend.features.search import (
    auto_search,
    create_search_outcome,
    format_search_outcome,
)
from backend.implementations.conversion import mass_convert
from backend.implementations.file_processing import (
    mass_set_file_date,
    mass_set_ownership,
    mass_set_permissions,
)
from backend.implementations.naming import mass_rename
from backend.implementations.root_folders import RootFolders
from backend.implementations.volumes import Volume, refresh_and_scan
from backend.internals.db import iter_commit
from backend.internals.server import MassEditorStatusEvent, WebSocket


def _search_volumes(
    volume_ids: List[int],
    identifier: str,
    progress_offset: int = 0,
    total_progress: Union[int, None] = None
):
    download_handler = DownloadHandler()
    ws = WebSocket()
    outcome = create_search_outcome()
    total_items = total_progress or len(volume_ids)

    for item_index, volume_id in enumerate(iter_commit(volume_ids)):
        ws.emit(MassEditorStatusEvent(
            identifier,
            progress_offset + item_index + 1,
            total_items
        ))

        search_results = auto_search(volume_id, outcome=outcome)
        outcome['selected_links'] += len(search_results)
        queue_results = []
        for result in search_results:
            if download_handler.link_in_queue(result['link']):
                outcome['already_queued_links'] += 1
                continue

            queue_results.append((result['link'], volume_id, None, False))

        for added, failure in download_handler.add_multiple(queue_results):
            outcome['queued_links'] += len(added)
            if not added and failure is None:
                outcome['already_queued_links'] += 1
            elif failure is not None:
                reason = failure.value
                failures = outcome['enqueue_failures']
                failures[reason] = failures.get(reason, 0) + 1

    summary = format_search_outcome(outcome)
    LOGGER.info('Mass search complete: %s', summary)
    ws.emit(MassEditorStatusEvent(
        identifier,
        total_items,
        total_items,
        dict(outcome)
    ))
    return outcome


class MassEditorDelete(MassEditorAction):
    identifier = 'delete'

    def run(self, **kwargs) -> None:
        delete_volume_folder = kwargs.get('delete_folder', False)
        if not isinstance(delete_volume_folder, bool):
            raise InvalidKeyValue('delete_folder', delete_volume_folder)

        LOGGER.info(f'Using mass editor, deleting volumes: {self.volume_ids}')

        ws = WebSocket()
        total_items = len(self.volume_ids)

        for item_index, volume_id in enumerate(iter_commit(self.volume_ids)):
            ws.emit(MassEditorStatusEvent(
                self.identifier,
                item_index + 1,
                total_items
            ))

            try:
                Volume(volume_id).delete(delete_volume_folder)
            except VolumeDownloadedFor:
                continue

        return


class MassEditorRootFolder(MassEditorAction):
    identifier = 'root_folder'

    def run(self, **kwargs) -> None:
        root_folder_id = kwargs.get('root_folder_id')
        if root_folder_id is None:
            raise KeyNotFound('root_folder_id')
        if not isinstance(root_folder_id, int):
            raise InvalidKeyValue('root_folder_id', root_folder_id)
        # Raises RootFolderNotFound if ID is invalid
        if not RootFolders().is_id_valid(root_folder_id):
            raise RootFolderNotFound(root_folder_id)

        LOGGER.info(
            f'Using mass editor, settings root folder to {root_folder_id} for volumes: {self.volume_ids}'
        )

        ws = WebSocket()
        total_items = len(self.volume_ids)

        for item_index, volume_id in enumerate(iter_commit(self.volume_ids)):
            ws.emit(MassEditorStatusEvent(
                self.identifier,
                item_index + 1,
                total_items
            ))

            Volume(volume_id).change_root_folder(root_folder_id)

        return


class MassEditorRename(MassEditorAction):
    identifier = 'rename'

    def run(self, **kwargs) -> None:
        LOGGER.info(f'Using mass editor, renaming volumes: {self.volume_ids}')

        ws = WebSocket()
        total_items = len(self.volume_ids)

        for item_index, volume_id in enumerate(iter_commit(self.volume_ids)):
            ws.emit(MassEditorStatusEvent(
                self.identifier,
                item_index + 1,
                total_items
            ))

            mass_rename(volume_id)

        return


class MassEditorUpdate(MassEditorAction):
    identifier = 'update'

    def run(self, **kwargs) -> None:
        LOGGER.info(f'Using mass editor, updating volumes: {self.volume_ids}')

        ws = WebSocket()
        total_items = len(self.volume_ids)

        for item_index, volume_id in enumerate(iter_commit(self.volume_ids)):
            ws.emit(MassEditorStatusEvent(
                self.identifier,
                item_index + 1,
                total_items
            ))

            refresh_and_scan(volume_id)

        return


class MassEditorSearch(MassEditorAction):
    identifier = 'search'

    def run(self, **kwargs):
        LOGGER.info(
            f'Using mass editor, auto searching for volumes: {self.volume_ids}'
        )

        return _search_volumes(self.volume_ids, self.identifier)


class MassEditorRefreshSearch(MassEditorAction):
    identifier = 'refresh_search'

    def run(self, **kwargs):
        LOGGER.info(
            'Using mass editor, refreshing and searching volumes: %s',
            self.volume_ids
        )

        ws = WebSocket()
        volume_count = len(self.volume_ids)
        total_items = volume_count * 2
        for item_index, volume_id in enumerate(iter_commit(self.volume_ids)):
            ws.emit(MassEditorStatusEvent(
                self.identifier,
                item_index + 1,
                total_items
            ))
            refresh_and_scan(volume_id)

        return _search_volumes(
            self.volume_ids,
            self.identifier,
            progress_offset=volume_count,
            total_progress=total_items
        )


class MassEditorConvert(MassEditorAction):
    identifier = 'convert'

    def run(self, **kwargs) -> None:
        LOGGER.info(
            f'Using mass editor, converting for volumes: {self.volume_ids}'
        )

        ws = WebSocket()
        total_items = len(self.volume_ids)

        for item_index, volume_id in enumerate(iter_commit(self.volume_ids)):
            ws.emit(MassEditorStatusEvent(
                self.identifier,
                item_index + 1,
                total_items
            ))

            mass_convert(volume_id)
        return


class MassEditorUnmonitor(MassEditorAction):
    identifier = 'unmonitor'

    def run(self, **kwargs) -> None:
        LOGGER.info(
            f'Using mass editor, unmonitoring volumes: {self.volume_ids}'
        )

        for volume_id in self.volume_ids:
            Volume(volume_id).update({'monitored': False})

        return


class MassEditorMonitor(MassEditorAction):
    identifier = 'monitor'

    def run(self, **kwargs) -> None:
        LOGGER.info(f'Using mass editor, monitoring volumes: {self.volume_ids}')

        for volume_id in self.volume_ids:
            Volume(volume_id).update({'monitored': True})

        return


class MassEditorMonitoringScheme(MassEditorAction):
    identifier = 'monitoring_scheme'

    def run(self, **kwargs) -> None:
        monitoring_scheme = kwargs.get('monitoring_scheme')
        if monitoring_scheme is None:
            raise KeyNotFound('monitoring_scheme')
        try:
            monitoring_scheme = MonitorScheme(monitoring_scheme)
        except ValueError:
            raise InvalidKeyValue('monitoring_scheme', monitoring_scheme)

        LOGGER.info(
            f'Using mass editor, applying monitoring scheme "{monitoring_scheme.value}" for volumes: {self.volume_ids}'
        )

        for volume_id in self.volume_ids:
            Volume(volume_id).apply_monitor_scheme(monitoring_scheme)

        return


class MassEditorFileDate(MassEditorAction):
    identifier = 'file_date'

    def run(self, **kwargs) -> None:
        LOGGER.info(
            f'Using mass editor, setting the file dates of volumes: {self.volume_ids}'
        )

        for volume_id in self.volume_ids:
            mass_set_file_date(volume_id)

        return


class MassEditorFilePermissions(MassEditorAction):
    identifier = 'file_permissions'

    def run(self, **kwargs) -> None:
        LOGGER.info(
            f'Using mass editor, setting the file permissions of volumes: {self.volume_ids}'
        )

        for volume_id in self.volume_ids:
            mass_set_permissions(volume_id)

        return


class MassEditorFileOwnership(MassEditorAction):
    identifier = 'file_ownership'

    def run(self, **kwargs) -> None:
        LOGGER.info(
            f'Using mass editor, setting the file ownership of volumes: {self.volume_ids}'
        )

        for volume_id in self.volume_ids:
            mass_set_ownership(volume_id)

        return


def run_mass_editor_action(
    action: str,
    volume_ids: List[int],
    **kwargs
) -> Any:
    """Run a mass editor action.

    Args:
        action (str): The action to run.
        volume_ids (List[int]): The volume IDs to run the action on.
        **kwargs (Dict[str, Any]): The arguments to pass to the action.

    Raises:
        InvalidKeyValue: If the action or any argument is not valid.
    """
    for ActionClass in get_subclasses(MassEditorAction):
        if ActionClass.identifier == action:
            break
    else:
        raise InvalidKeyValue('action', action)

    return ActionClass(volume_ids).run(**kwargs)
