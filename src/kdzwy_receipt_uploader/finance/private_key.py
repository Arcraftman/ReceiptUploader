"""Current-Windows-user DPAPI protection for the portable finance API key."""
import ctypes
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [('length',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]


def transform(value, encrypt):
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32',use_last_error=True)
    method = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    if not method(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise OSError('无法读取当前 Windows 用户的加密密钥，请重新配置')
    try:
        return ctypes.string_at(target.data,target.length)
    finally:
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))


def protect(value):
    return transform(value,True)


def unprotect(value):
    return transform(value,False)
