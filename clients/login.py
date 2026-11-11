import asyncio
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlencode

import httpx

from clients.api import CloudError, normalize_api_url
from clients.credentials import Credentials, save_credentials


DEFAULT_WEB_URL = 'https://platform.margelo.com'


def configured_web_url() -> str:
    value = os.environ.get('ADC_WEB_URL') or DEFAULT_WEB_URL
    return normalize_api_url(value, setting='ADC_WEB_URL')


def verify_credentials(credentials: Credentials, account_id: str, transport=None) -> None:
    headers = {'Authorization': f'Bearer {credentials.api_key}'}
    with httpx.Client(timeout=15, transport=transport) as client:
        response = client.get(credentials.api_url + '/v1/me', headers=headers)
        response.raise_for_status()
        account = response.json()
    if account['id'] != account_id:
        raise CloudError('The CLI authorization belongs to another account.')


def revoke_credentials(credentials: Credentials, transport=None) -> None:
    token_id = quote(credentials.token_id, safe='')
    headers = {'Authorization': f'Bearer {credentials.api_key}'}
    try:
        with httpx.Client(timeout=15, transport=transport) as client:
            response = client.delete(credentials.api_url + '/v1/tokens/' + token_id, headers=headers)
    except httpx.RequestError as error:
        raise CloudError('Could not reach the API to revoke the CLI key. Try adc logout again.') from error
    if response.is_success or response.status_code in (401, 404):
        return
    raise CloudError('Could not revoke the CLI key. Try adc logout again.')


class LoginHandler(BaseHTTPRequestHandler):
    server: 'LoginServer'

    def setup(self):
        parent = super()
        parent.setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def reply(self, status: int, content: str):
        page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Device Cloud · CLI</title>
<style>body{{font:16px system-ui;background:#fafafa;color:#171717;padding:48px 20px}}
main{{max-width:400px;margin:6vh auto;background:white;border:1px solid #e5e5e5;border-radius:16px;padding:32px}}
h1{{font-size:26px}}p{{line-height:1.5;color:#555}}</style></head>
<body><main><strong>Device Cloud</strong>{content}</main></body></html>'''
        body = page.encode()
        length = len(body)
        content_length = str(length)
        self.send_response(status)
        headers = {
            'Content-Type': 'text/html; charset=utf-8',
            'Content-Length': content_length,
            'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        }
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def valid_target(self):
        return self.path == '/callback' and self.headers.get('Host') == self.server.host

    def do_GET(self):
        if self.valid_target() is False:
            self.reply(404, '<h1>Not found</h1>')
            return
        self.reply(200, '<h1>Finish signing in to Device Cloud</h1><p>Use the web app opened by your terminal.</p>')

    def do_POST(self):
        if self.valid_target() is False:
            self.reply(404, '<h1>Not found</h1>')
            return
        if self.headers.get('Origin') != self.server.web_url:
            self.reply(403, '<h1>Invalid request origin</h1>')
            return
        if self.server.finished.is_set():
            self.reply(410, '<h1>This login has finished</h1>')
            return
        content_type = self.headers.get('Content-Type', '')
        media_type = content_type.split(';', 1)[0]
        try:
            length_header = self.headers.get('Content-Length', '0')
            length = int(length_header)
            if media_type != 'application/x-www-form-urlencoded' or length <= 0 or length > 8192:
                raise ValueError('Invalid callback')
            raw = self.rfile.read(length)
            text = raw.decode()
            form = parse_qs(text, keep_blank_values=True, max_num_fields=7)
            values = {}
            for name, entries in form.items():
                if len(entries) != 1 or entries[0] == '':
                    raise ValueError('Invalid callback field')
                values[name] = entries[0]
            received_state = values.get('state', '').encode()
            expected_state = self.server.state.encode()
            if secrets.compare_digest(received_state, expected_state) is False:
                raise ValueError('Invalid state')
            if values.get('api_url') != self.server.api_url:
                raise ValueError('Invalid API address')
            if values.get('error') == 'access_denied':
                self.server.failure = 'Browser authorization was cancelled.'
                self.server.finished.set()
                self.reply(200, '<h1>Authorization cancelled</h1><p>You can close this tab.</p>')
                return
            for name in ('api_key', 'token_id', 'account', 'account_id'):
                if name not in values:
                    raise ValueError('Missing authorization field')
        except ValueError:
            self.reply(400, '<h1>Invalid CLI authorization</h1><p>Run adc login again.</p>')
            return
        account_id = values['account_id']
        credentials = Credentials(self.server.api_url, values['api_key'], values['token_id'], values['account'], account_id)
        try:
            verify_credentials(credentials, account_id)
            if self.server.cancelled.is_set():
                raise CloudError('Login was cancelled. Run adc login again.')
            save_credentials(credentials)
        except (CloudError, httpx.HTTPError, OSError, ValueError, KeyError):
            try:
                revoke_credentials(credentials)
            except (CloudError, httpx.HTTPError):
                self.server.failure = 'Login failed. Remove the unused ADC CLI key from the dashboard.'
                self.server.finished.set()
            self.reply(400, '<h1>Authorization failed</h1><p>Return to the web app and try again.</p>')
            return
        self.server.result = credentials
        self.server.finished.set()
        self.reply(200, '<h1>You’re signed in</h1><p>Return to your terminal. You can close this tab.</p>')


class LoginServer(HTTPServer):
    def __init__(self, api_url: str, web_url: str):
        self.api_url = api_url
        self.web_url = web_url
        self.state = secrets.token_urlsafe(32)
        self.finished = threading.Event()
        self.cancelled = threading.Event()
        self.result: Credentials | None = None
        self.failure: str | None = None
        parent = super()
        parent.__init__(('127.0.0.1', 0), LoginHandler)
        self.host = f'127.0.0.1:{self.server_port}'
        self.callback_url = 'http://' + self.host + '/callback'


async def browser_login(api_url: str, timeout_seconds=300) -> Credentials:
    web_url = configured_web_url()
    server = LoginServer(api_url, web_url)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    parameters = urlencode({'redirect_uri': server.callback_url, 'state': server.state, 'api_url': api_url})
    url = web_url + '/cli/login?' + parameters
    print(f'Opening Device Cloud to sign in: {url}', flush=True)
    try:
        await asyncio.to_thread(webbrowser.open, url)
        completed = await asyncio.to_thread(server.finished.wait, timeout_seconds)
        if completed is False:
            raise CloudError('Login timed out. Run adc login again.')
        if server.failure:
            raise CloudError(server.failure)
        if server.result is None:
            raise CloudError('Login did not complete.')
        return server.result
    finally:
        server.cancelled.set()
        server.finished.set()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()
