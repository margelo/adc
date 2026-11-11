import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    api_url: str
    api_key: str = field(repr=False)
    token_id: str
    account: str
    account_id: str | None = None


def credentials_path(api_url: str) -> Path:
    configured = os.environ.get('XDG_CONFIG_HOME')
    if configured:
        directory = Path(configured) / 'adc'
    else:
        directory = Path.home() / '.config' / 'adc'
    encoded = api_url.encode()
    digest = hashlib.sha256(encoded)
    filename = digest.hexdigest()
    return directory / f'{filename}.json'


def load_credentials(api_url: str) -> Credentials | None:
    path = credentials_path(api_url)
    try:
        contents = path.read_text()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(contents)
        if isinstance(data, dict) is False:
            raise ValueError('Invalid credential format')
        values = {}
        for name in ('api_url', 'api_key', 'token_id', 'account'):
            value = data.get(name)
            if isinstance(value, str) is False or value == '':
                raise ValueError('Invalid credential field')
            values[name] = value
        if values['api_url'] != api_url:
            raise ValueError('Credentials belong to another API')
        account_id = data.get('account_id')
        if account_id is not None and (isinstance(account_id, str) is False or account_id == ''):
            raise ValueError('Invalid account ID')
    except ValueError as error:
        raise CredentialError(f'Cannot read the saved login at {path}') from error
    return Credentials(**values, account_id=account_id)


def save_credentials(credentials: Credentials) -> None:
    path = credentials_path(credentials.api_url)
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.login-', dir=directory)
    temporary_path = Path(temporary)
    try:
        payload = asdict(credentials)
        contents = json.dumps(payload)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(contents)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def remove_credentials(api_url: str) -> None:
    path = credentials_path(api_url)
    path.unlink(missing_ok=True)
