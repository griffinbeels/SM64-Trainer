"""Windows Credential Manager only; no plaintext or insecure fallback store."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os

from .config import CommunityError


class _Credential(C.Structure):
    _fields_ = [("Flags", W.DWORD), ("Type", W.DWORD), ("TargetName", W.LPWSTR),
               ("Comment", W.LPWSTR), ("LastWritten", W.FILETIME),
               ("CredentialBlobSize", W.DWORD), ("CredentialBlob", C.POINTER(C.c_ubyte)),
               ("Persist", W.DWORD), ("AttributeCount", W.DWORD), ("Attributes", C.c_void_p),
               ("TargetAlias", W.LPWSTR), ("UserName", W.LPWSTR)]


class WindowsCredentialAPI:
    def __init__(self):
        if os.name != "nt":
            raise CommunityError("vault_unavailable", "Secure Windows token storage is unavailable.")
        self.dll = C.WinDLL("advapi32", use_last_error=True)
        self.dll.CredReadW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(C.POINTER(_Credential))]
        self.dll.CredReadW.restype = W.BOOL
        self.dll.CredWriteW.argtypes = [C.POINTER(_Credential), W.DWORD]
        self.dll.CredWriteW.restype = W.BOOL
        self.dll.CredDeleteW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD]
        self.dll.CredDeleteW.restype = W.BOOL
        self.dll.CredFree.argtypes = [C.c_void_p]
        self.dll.CredFree.restype = None

    def read(self, target):
        pointer = C.POINTER(_Credential)()
        if not self.dll.CredReadW(target, 1, 0, C.byref(pointer)):
            if C.get_last_error() == 1168:
                return None
            raise CommunityError("vault_read", "The secure token store could not be read.")
        try:
            value = pointer.contents
            if not 1 <= value.CredentialBlobSize <= 2560:
                raise CommunityError("vault_format", "The secure token store is invalid.")
            return C.string_at(value.CredentialBlob, value.CredentialBlobSize).decode("utf-8")
        finally:
            self.dll.CredFree(pointer)

    def write(self, target, token):
        encoded = token.encode("utf-8")
        if not 1 <= len(encoded) <= 2560:
            raise CommunityError("vault_format", "The refreshed token exceeds secure storage limits.")
        buffer = (C.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        value = _Credential(Type=1, TargetName=target, CredentialBlobSize=len(encoded),
                            CredentialBlob=buffer, Persist=2, UserName="SM64 community")
        try:
            if not self.dll.CredWriteW(C.byref(value), 0):
                raise CommunityError("vault_write", "The refreshed token could not be saved securely.")
        finally:
            C.memset(buffer, 0, len(encoded))

    def delete(self, target):
        if not self.dll.CredDeleteW(target, 1, 0) and C.get_last_error() != 1168:
            raise CommunityError("vault_delete", "The secure token could not be removed.")


class RefreshTokenStore:
    def __init__(self, config, *, api=None):
        identity = "\0".join((config.supabase_url, config.oauth_client_id, config.channel))
        self.target = "SM64TrainerCommunity:" + hashlib.sha256(identity.encode()).hexdigest()
        self._api = api

    def _backend(self):
        if self._api is None:
            self._api = WindowsCredentialAPI()
        return self._api

    def load(self):
        return self._backend().read(self.target)

    def save(self, refresh_token):
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CommunityError("vault_format", "The refreshed token is invalid.")
        self._backend().write(self.target, refresh_token)

    def clear(self):
        self._backend().delete(self.target)
