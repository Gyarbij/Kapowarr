# -*- coding: utf-8 -*-

from re import IGNORECASE, compile
from typing import Tuple, Union

from requests.exceptions import RequestException

from backend.base.definitions import Constants, DownloadState
from backend.base.helpers import Session
from backend.implementations.download_general import BaseTorrentClient

filename_magnet_link = compile(r'(?<=&dn=).*?(?=&)', IGNORECASE)


class qBittorrent(BaseTorrentClient):
    _tokens = ('title', 'base_url', 'username', 'password')

    def __init__(self, id: int) -> None:
        super().__init__(id)

        self.ssn = Session()

        if self.username and self.password:
            data = {
                'username': self.username,
                'password': self.password
            }
        else:
            data = {}

        self.ssn.post(
            f'{self.base_url}/api/v2/auth/login',
            data=data
        )

        self.torrent_found = False

        return

    def add_download(self,
        magnet_link: str,
        target_folder: str,
        download_name: Union[str, None]
    ) -> str:
        if download_name is not None:
            magnet_link = filename_magnet_link.sub(download_name, magnet_link)

        files = {
            'urls': (None, magnet_link),
            'savepath': (None, target_folder),
            'category': (None, Constants.TORRENT_TAG)
        }

        self.ssn.post(
            f'{self.base_url}/api/v2/torrents/add',
            files=files
        )

        return magnet_link.split('urn:btih:')[1].split('&')[0]

    def get_download_status(self, download_id: str) -> Union[dict, None]:
        r = self.ssn.get(
            f'{self.base_url}/api/v2/torrents/properties',
            params={'hash': download_id}
        )
        if r.status_code == 404:
            return None if self.torrent_found else {}

        self.torrent_found = True
        result = r.json()

        if result['pieces_have'] <= 0:
            state = DownloadState.QUEUED_STATE

        elif result['completion_date'] == -1:
            state = DownloadState.DOWNLOADING_STATE

        elif result['eta'] != 8640000:
            state = DownloadState.SEEDING_STATE

        else:
            state = DownloadState.IMPORTING_STATE

        return {
            'size': result['total_size'],
            'progress': round(
                (result['total_downloaded'] - result['total_wasted'])
                /
                result['total_size'] * 100,

                2
            ),
            'speed': result['dl_speed'],
            'state': state
        }

    def delete_download(self, download_id: str, delete_files: bool) -> None:
        self.ssn.post(
            f'{self.base_url}/api/v2/torrents/delete',
            data={
                'hashes': download_id,
                'deleteFiles': delete_files
            }
        )
        return

    @staticmethod
    def test(
        base_url: str,
        username: Union[str, None] = None,
        password: Union[str, None] = None,
        api_token: Union[str, None] = None
    ) -> Tuple[bool, Union[str, None]]:
        try:
            if username and password:
                params = {
                    'username': username,
                    'password': password
                }
            else:
                params = {}

            auth_request = Session().post(
                f'{base_url}/api/v2/auth/login',
                data=params
            )
            if auth_request.status_code == 404:
                return False, "Invalid base URL or version too low; at least v4.1"
            elif not auth_request.ok:
                return False, "Invalid instance; not Qbittorrent"
            auth_success = auth_request.headers.get('set-cookie') is not None

            if auth_success:
                return True, None
            else:
                return False, "Can't authenticate"

        except RequestException:
            return False, "Can't connect; invalid base URL"
