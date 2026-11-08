"""Validated runtime settings for native and Docker installations.

Loading configuration never creates files or directories. Only public_config()
may be sent to the browser; runtime settings include private key material.
"""
import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import re


APP_NAME = 'LM Benchlab'
APP_SLUG = 'lm-benchlab'
VERSION = '7.4'

# Existing installations retain their configuration. An explicitly present
# canonical value takes precedence, including an empty value that must validate.
_ENV_ALIASES = {
    'LM_BENCHLAB_HOST': 'APP_HOST',
    'LM_BENCHLAB_PORT': 'APP_PORT',
    'LM_BENCHLAB_DB': 'LLM_LAB_DB',
    'LM_BENCHLAB_MASTER_KEY': 'LLM_LAB_MASTER_KEY',
    'LM_BENCHLAB_NO_BROWSER': 'LLM_LAB_NO_BROWSER',
    'LM_BENCHLAB_DEFAULT_ENGINE_HOST': 'LLM_LAB_DEFAULT_ENGINE_HOST',
}


class ConfigurationError(ValueError):
    """A named environment setting cannot be used to start the application."""


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    host: str
    port: int
    db_path: Path
    master_key: str = field(repr=False)
    no_browser: bool
    default_engine_host: str

    @property
    def open_browser(self) -> bool:
        return not self.no_browser

    def public_config(self) -> dict:
        """Return an explicit allowlist of settings safe for the browser."""
        return {'name': APP_NAME, 'version': VERSION,
                'default_engine_host': self.default_engine_host}


def _text(environ: Mapping, name: str, default: str) -> tuple[str, str]:
    if name not in environ and _ENV_ALIASES[name] in environ:
        name = _ENV_ALIASES[name]
    value = environ.get(name, default)
    if not isinstance(value, str):
        raise ConfigurationError(f'{name} must be a string')
    return value.strip(), name


def _host(value: str, name: str) -> str:
    if not value:
        raise ConfigurationError(f'{name} must not be blank')
    # The application HTTP server and hostname endpoint form currently use IPv4.
    # A URL belongs in an individual engine profile rather than a host setting.
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        labels = value.rstrip('.').split('.')
        if (len(value) > 253 or re.fullmatch(r'[0-9.]+', value)
                or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                       for label in labels)):
            raise ConfigurationError(f'{name} must be an IPv4 address or hostname without a URL, port or path')
    else:
        if address.version != 4:
            raise ConfigurationError(f'{name} must use an IPv4 address or hostname; IPv6 is not supported')
    return value


def _database_path(value: str, base_dir: Path, name: str) -> Path:
    if not value:
        raise ConfigurationError(f'{name} must not be blank')
    if (value == ':memory:' or value.lower().startswith('file:') or '://' in value
            or any(ord(char) < 32 for char in value)
            or any(char in value for char in '<>"|?*')
            or value.endswith(('/', '\\'))
            or re.split(r'[/\\]', value)[-1] in ('.', '..')):
        raise ConfigurationError(f'{name} must be a SQLite file path, not a directory or URI')
    try:
        path = Path(value).expanduser()
        if ':' in str(path)[len(path.drive):] or (path.drive and not path.is_absolute()):
            raise ConfigurationError(f'{name} must be an absolute or project-relative SQLite file path')
        if not path.is_absolute():
            path = base_dir / path
        path = path.resolve()
        if path.is_dir():
            raise ConfigurationError(f'{name} must name a file, not a directory')
        if any(parent.exists() and not parent.is_dir() for parent in path.parents):
            raise ConfigurationError(f'{name} has a parent path that is not a directory')
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError(f'{name} must be a valid SQLite file path') from exc
    return path


def load_config(environ: Mapping[str, str] | None = None,
                base_dir: str | Path | None = None) -> RuntimeConfig:
    """Read one environment snapshot and validate it without filesystem writes.

    Relative database paths resolve from the project directory, independent of
    the process working directory. Passing environ replaces the OS environment,
    which also makes tests and embedding deterministic.
    """
    values = dict(os.environ if environ is None else environ)
    root = Path(base_dir).resolve() if base_dir is not None else Path(__file__).resolve().parent
    host = _host(*_text(values, 'LM_BENCHLAB_HOST', '127.0.0.1'))
    engine_host = _host(*_text(values, 'LM_BENCHLAB_DEFAULT_ENGINE_HOST', '127.0.0.1'))
    port_text, port_name = _text(values, 'LM_BENCHLAB_PORT', '8768')
    if (not re.fullmatch(r'[0-9]{1,5}', port_text)
            or not 1 <= int(port_text) <= 65535):
        raise ConfigurationError(f'{port_name} must be an integer from 1 to 65535')
    browser_text, browser_name = _text(values, 'LM_BENCHLAB_NO_BROWSER', '1')
    browser_text = browser_text.lower()
    if browser_text not in ('1', '0', 'true', 'false', 'yes', 'no', 'on', 'off'):
        raise ConfigurationError(f'{browser_name} must be 1/0, true/false, yes/no or on/off')
    master_key, master_name = _text(values, 'LM_BENCHLAB_MASTER_KEY', '')
    if master_key:
        try:
            decoded = base64.b64decode(master_key.encode('ascii'), altchars=b'-_', validate=True)
        except (UnicodeError, binascii.Error, ValueError) as exc:
            raise ConfigurationError(f'{master_name} must be a valid Fernet key or blank') from exc
        if len(decoded) != 32:
            raise ConfigurationError(f'{master_name} must be a valid Fernet key or blank')
    default_db = root / 'data' / 'lm_benchlab_history.sqlite3'
    legacy_db = root / 'data' / 'llm_concurrency_history.sqlite3'
    if ('LM_BENCHLAB_DB' not in values and 'LLM_LAB_DB' not in values
            and not default_db.exists() and legacy_db.is_file()):
        default_db = legacy_db
    db_text, db_name = _text(values, 'LM_BENCHLAB_DB', str(default_db))
    db_path = _database_path(db_text, root, db_name)
    return RuntimeConfig(host=host, port=int(port_text), db_path=db_path,
                         master_key=master_key,
                         no_browser=browser_text in ('1', 'true', 'yes', 'on'),
                         default_engine_host=engine_host)
