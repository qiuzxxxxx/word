"""Encrypt secrets using Windows DPAPI or the deployment's Fernet key."""

import base64
import ctypes
from ctypes import wintypes

from django.conf import settings


_PREFIX = 'dpapi:v1:'
_SERVER_PREFIX = 'fernet:v1:'


class VaultError(RuntimeError):
    """Raised when the operating system key store cannot protect a secret."""


class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _raise_last_error(action):
    raise VaultError('%s failed: Windows error %s' % (action, ctypes.get_last_error()))


def _local_free(pointer):
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def _require_windows():
    if not hasattr(ctypes, 'windll'):
        raise VaultError('Windows DPAPI is required to access locally stored API keys')


def encrypt(plaintext):
    """Return a versioned envelope for ``plaintext``."""
    raw = (plaintext or '').encode('utf-8')
    if getattr(settings, 'SERVER_MODE', False):
        return _SERVER_PREFIX + settings.SERVER_VAULT.encrypt(raw).decode('ascii')

    _require_windows()
    source, _source_buffer = _blob(raw)
    protected = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(ctypes.byref(source), '学习工具 AI API key', None,
                                     None, None, 0, ctypes.byref(protected)):
        _raise_last_error('CryptProtectData')
    try:
        payload = ctypes.string_at(protected.pbData, protected.cbData)
        return _PREFIX + base64.urlsafe_b64encode(payload).decode('ascii')
    finally:
        _local_free(protected.pbData)


def decrypt(envelope):
    """Decrypt a value produced by :func:`encrypt`."""
    if getattr(settings, 'SERVER_MODE', False):
        from cryptography.fernet import InvalidToken

        if not isinstance(envelope, str) or not envelope.startswith(_SERVER_PREFIX):
            raise VaultError('请在服务器的 AI 模型设置中重新录入密钥')
        try:
            token = envelope[len(_SERVER_PREFIX):].encode('ascii')
            return settings.SERVER_VAULT.decrypt(token).decode('utf-8')
        except (InvalidToken, UnicodeError) as exc:
            raise VaultError('无法解密已保存的服务器 AI 密钥') from exc

    _require_windows()
    if not isinstance(envelope, str) or not envelope.startswith(_PREFIX):
        raise VaultError('invalid encrypted API key format')
    try:
        protected_bytes = base64.urlsafe_b64decode(envelope[len(_PREFIX):].encode('ascii'))
    except Exception as exc:
        raise VaultError('invalid encrypted API key encoding') from exc
    protected, _protected_buffer = _blob(protected_bytes)
    plain = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(ctypes.byref(protected), None, None, None, None,
                                       0, ctypes.byref(plain)):
        _raise_last_error('CryptUnprotectData')
    try:
        return ctypes.string_at(plain.pbData, plain.cbData).decode('utf-8')
    finally:
        _local_free(plain.pbData)
