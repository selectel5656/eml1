import os
from typing import List, Dict
import requests


class ApiClient:
    """Client for interacting with remote mail API."""

    USER_AGENT = 'ru.XXXX.XXXX/XXXX.XXXX (iPhone12,1; iOS 16.5.1)'

    def __init__(
        self,
        domain: str,
        api_key: str,
        uuid: str,
        login: str = '',
        from_name: str = '',
        user_agent: str | None = None,
        proxy: str | None = None,
    ):
        self.domain = domain
        self.api_key = api_key
        self.uuid = uuid
        self.login = login
        self.from_name = from_name
        self.user_agent = user_agent or self.USER_AGENT
        self.proxy = proxy

    def _headers(self, content_type: str = 'application/x-www-form-urlencoded') -> Dict[str, str]:
        headers = {
            'Accept': '*/*',
            'Authorization': f'OAuth {self.api_key}',
            'Accept-Encoding': 'gzip, deflate, br',
            'User-Agent': self.user_agent,
            'Accept-Language': 'ru-RU;q=1, en-RU;q=0.9',
        }
        if content_type:
            headers['Content-Type'] = content_type
        return headers

    def _proxies(self):
        if not self.proxy:
            return None
        addr = f'http://{self.proxy}'
        return {'http': addr, 'https': addr}

    def check_account(self) -> bool:
        url = f'https://mail.{self.domain}/api/mobile/v1/reset_fresh?app_state=active&uuid={self.uuid}'
        try:
            r = requests.get(url, headers=self._headers(), proxies=self._proxies())
            data = r.json()
            return data.get('status', {}).get('status') == 1
        except Exception:
            return False

    def generate_operation_id(self) -> str:
        url = (
            f'https://mail.{self.domain}/api/mobile/v2/generate_operation_id'
            f'?app_state=foreground&uuid={self.uuid}&client=iphone'
        )
        try:
            r = requests.get(url, headers=self._headers(), proxies=self._proxies())
            data = r.json()
            return data.get('operation_id', '')
        except Exception:
            return ''

    def upload_attachment(self, path: str, filename: str) -> Dict[str, str]:
        url = (
            f'https://mail.{self.domain}/api/mobile/v1/upload'
            f'?app_state=foreground&uuid={self.uuid}&client=iphone'
        )
        try:
            with open(path, 'rb') as f:
                files = {
                    'filename': (None, filename),
                    'attachment': (filename, f, 'application/octet-stream'),
                }
                r = requests.post(
                    url,
                    headers=self._headers(content_type=None),
                    files=files,
                    proxies=self._proxies(),
                )
                return r.json()
        except Exception:
            return {}

    def send_mail(self, subject: str, body: str, recipients: List[str], att_urls: List[str], operation_id: str) -> bool:
        url = f'https://mail.{self.domain}/api/mobile/v1/send?app_state=foreground&uuid={self.uuid}'
        payload = {
            'att_ids': att_urls,
            'attachesCount': len(att_urls),
            'send': body,
            'ttype': 'html',
            'subj': subject,
            'operation_id': operation_id,
            'compose_check': '',
            'from_mailbox': self.login,
            'bcc': '; '.join(recipients),
            'from_name': self.from_name,
        }
        try:
            r = requests.post(
                url,
                headers=self._headers('application/json'),
                json=payload,
                proxies=self._proxies(),
            )
            data = r.json()
            return data.get('status', {}).get('status') == 1
        except Exception:
            return False
