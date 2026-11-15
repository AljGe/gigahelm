# -*- coding: utf-8 -*-
"""
Ensures only one app instance runs via a named mutex and shared memory on Windows.
Handles activating the existing window and inter-instance communication.
"""

import sys
import os
import ctypes
import json
from typing import Optional, Any, Dict

# Import names, sizes, app name, and commands from settings
from config.settings import (
    MUTEX_NAME, SHARED_MEM_NAME, SHARED_MEM_SIZE, APP_NAME,
    SHARED_MEM_HWND_OFFSET, SHARED_MEM_HWND_SIZE, SHARED_MEM_COMMAND_OFFSET,
    SHARED_MEM_PAYLOAD_OFFSET, SHARED_MEM_PAYLOAD_SIZE, COMMAND_NONE,
    COMMAND_RELOAD_AND_SHOW, COMMAND_RELOAD_ONLY
)
# Import messages from localization
from .localization import tr

# --- Win32 API imports and setup ---
_mutex_handle: Optional[int] = None
_shared_mem_handle: Optional[int] = None
_shared_mem_view: Optional[int] = None
_single_instance_check_enabled: bool = False

if os.name == 'nt':
    try:
        from ctypes import wintypes
        import win32con

        # Constants
        ERROR_ALREADY_EXISTS = 183
        INVALID_HANDLE_VALUE = -1
        PAGE_READWRITE = 0x04
        FILE_MAP_ALL_ACCESS = 0xF001F

        # Function prototypes
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.restype = wintypes.HANDLE
        CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

        GetLastError = kernel32.GetLastError
        ReleaseMutex = kernel32.ReleaseMutex
        CloseHandle = kernel32.CloseHandle

        # Shared memory functions
        CreateFileMappingW = kernel32.CreateFileMappingW
        CreateFileMappingW.restype = wintypes.HANDLE
        CreateFileMappingW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]

        MapViewOfFile = kernel32.MapViewOfFile
        MapViewOfFile.restype = wintypes.LPVOID
        MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]

        UnmapViewOfFile = kernel32.UnmapViewOfFile
        UnmapViewOfFile.restype = wintypes.BOOL
        UnmapViewOfFile.argtypes = [wintypes.LPCVOID]

        # Activation functions
        IsWindow = user32.IsWindow
        SetForegroundWindow = user32.SetForegroundWindow
        ShowWindow = user32.ShowWindow
        SW_RESTORE = win32con.SW_RESTORE

        _single_instance_check_enabled = True

    except (ImportError, AttributeError, OSError) as e:
        print(f"Warning: failed to load Win32 modules for single-instance check: {e}", file=sys.stderr)
        _single_instance_check_enabled = False
else:
    # Disable this feature on non-Windows systems
    _single_instance_check_enabled = False

# --- Shared memory management ---

def _create_or_open_shared_memory() -> bool:
    """Creates or opens the named shared memory block."""
    global _shared_mem_handle, _shared_mem_view
    if not _single_instance_check_enabled: return False

    _shared_mem_handle = CreateFileMappingW(
        INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, SHARED_MEM_SIZE, SHARED_MEM_NAME
    )
    if not _shared_mem_handle:
        return False

    _shared_mem_view = MapViewOfFile(_shared_mem_handle, FILE_MAP_ALL_ACCESS, 0, 0, SHARED_MEM_SIZE)
    if not _shared_mem_view:
        CloseHandle(_shared_mem_handle)
        _shared_mem_handle = None
        return False

    return True

def write_hwnd_to_shared_memory(hwnd: int):
    """Writes the window handle (HWND) to the specified shared memory location."""
    global _shared_mem_view
    if not _single_instance_check_enabled or not _shared_mem_view:
        return

    try:
        hwnd_str = str(hwnd).encode('utf-8')
        if len(hwnd_str) >= SHARED_MEM_HWND_SIZE:
            raise ValueError("HWND string is too large for its shared memory block")

        # Write the HWND at the specified offset in the buffer
        buffer = (ctypes.c_char * SHARED_MEM_HWND_SIZE).from_address(_shared_mem_view + SHARED_MEM_HWND_OFFSET)
        ctypes.memset(_shared_mem_view + SHARED_MEM_HWND_OFFSET, 0, SHARED_MEM_HWND_SIZE) # Zero it first
        buffer.value = hwnd_str
    except Exception as e:
        print(f"Error writing HWND to shared memory: {e}", file=sys.stderr)

def read_hwnd_from_shared_memory() -> Optional[int]:
    """Reads the window handle (HWND) from the specified shared memory location."""
    global _shared_mem_view
    if not _single_instance_check_enabled or not _shared_mem_view:
        return None

    try:
        buffer = (ctypes.c_char * SHARED_MEM_HWND_SIZE).from_address(_shared_mem_view + SHARED_MEM_HWND_OFFSET)
        hwnd_str = buffer.value.decode('utf-8').strip('\x00')
        return int(hwnd_str) if hwnd_str else None
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        print(f"Error reading HWND from shared memory: {e}", file=sys.stderr)
        return None

def write_command_to_shared_memory(command: int):
    """Writes the command byte to the specified shared memory location."""
    global _shared_mem_view
    if not _single_instance_check_enabled or not _shared_mem_view:
        return

    try:
        command_ptr = (ctypes.c_byte*1).from_address(_shared_mem_view + SHARED_MEM_COMMAND_OFFSET)
        command_ptr[0] = command
    except Exception as e:
        print(f"Error writing command to shared memory: {e}", file=sys.stderr)

def write_payload_to_shared_memory(payload: Dict[str, Any]):
    """Writes the JSON payload to the specified shared memory location."""
    global _shared_mem_view
    if not _single_instance_check_enabled or not _shared_mem_view:
        return
    try:
        payload_str = json.dumps(payload, ensure_ascii=False)
        payload_bytes = payload_str.encode("utf-8")
        if len(payload_bytes) >= SHARED_MEM_PAYLOAD_SIZE:
            raise ValueError("Command payload too large to write to shared memory")

        ctypes.memset(_shared_mem_view + SHARED_MEM_PAYLOAD_OFFSET, 0, SHARED_MEM_PAYLOAD_SIZE)
        payload_buffer = (ctypes.c_char * SHARED_MEM_PAYLOAD_SIZE).from_address(_shared_mem_view + SHARED_MEM_PAYLOAD_OFFSET)
        payload_buffer.value = payload_bytes
    except Exception as e:
        print(f"Error writing payload to shared memory: {e}", file=sys.stderr)

def read_command_from_shared_memory() -> Optional[int]:
    """Reads the command byte from the specified shared memory location."""
    global _shared_mem_view
    if not _single_instance_check_enabled or not _shared_mem_view:
        return None

    try:
        command_ptr = (ctypes.c_byte*1).from_address(_shared_mem_view + SHARED_MEM_COMMAND_OFFSET)
        return command_ptr[0]
    except Exception as e:
        print(f"Error reading command from shared memory: {e}", file=sys.stderr)
        return None

def read_payload_from_shared_memory() -> Dict[str, Any]:
    """Reads the JSON payload from shared memory."""
    global _shared_mem_view
    if not _single_instance_check_enabled or not _shared_mem_view:
        return {}
    try:
        payload_buffer = (ctypes.c_char * SHARED_MEM_PAYLOAD_SIZE).from_address(_shared_mem_view + SHARED_MEM_PAYLOAD_OFFSET)
        payload_str = payload_buffer.value.decode("utf-8").strip("\x00").strip()
        if not payload_str:
            return {}
        payload = json.loads(payload_str)
        return payload if isinstance(payload, dict) else {}
    except Exception as e:
        print(f"Error reading payload from shared memory: {e}", file=sys.stderr)
        return {}

def clear_payload_in_shared_memory():
    """Clears the payload region in shared memory."""
    global _shared_mem_view
    if not _single_instance_check_enabled or not _shared_mem_view:
        return
    try:
        ctypes.memset(_shared_mem_view + SHARED_MEM_PAYLOAD_OFFSET, 0, SHARED_MEM_PAYLOAD_SIZE)
    except Exception as e:
        print(f"Error clearing shared memory payload: {e}", file=sys.stderr)

def close_shared_memory():
    """Unmaps and closes the shared memory handle."""
    global _shared_mem_view, _shared_mem_handle
    if _shared_mem_view:
        UnmapViewOfFile(_shared_mem_view)
        _shared_mem_view = None
    if _shared_mem_handle:
        CloseHandle(_shared_mem_handle)
        _shared_mem_handle = None

# --- Single-instance check ---

def check_single_instance(
    is_task_launch: bool = False,
    command_override: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Checks for another instance using a system-wide mutex.
    If another instance exists, this function sends it a command and returns False.
    Otherwise, it acquires the mutex and returns True.
    """
    global _mutex_handle, _single_instance_check_enabled
    if not _single_instance_check_enabled:
        return True # Always allow running when not on Windows or the API is unavailable

    _mutex_handle = CreateMutexW(None, True, MUTEX_NAME)
    last_error = GetLastError()

    if last_error == ERROR_ALREADY_EXISTS:
        # Another instance is already running
        if _mutex_handle:
            CloseHandle(_mutex_handle) # Release the handle we tried to create but failed to own
        
        # Decide which command to send based on the launch type
        command_to_send = command_override if command_override is not None else (
            COMMAND_RELOAD_ONLY if is_task_launch else COMMAND_RELOAD_AND_SHOW
        )
        _send_command_and_exit(command_to_send, payload=payload)
        return False # Tell the main program to exit

    if not _mutex_handle:
        # Fatal error: the mutex could not be created
        ctypes.windll.user32.MessageBoxW(0, "Cannot create mutex. The application may not run correctly.", "Fatal Error", 0x10)
        return True # Allow running, but functionality may be limited

    # This is the first instance
    _create_or_open_shared_memory()
    return True

def _send_command_and_exit(command: int, payload: Optional[Dict[str, Any]] = None):
    """
    Connects to the existing instance's shared memory, sends the command, activates the window (if needed), and exits.
    """
    if _create_or_open_shared_memory():
        hwnd = read_hwnd_from_shared_memory()
        if hwnd and IsWindow(hwnd):
            # Only activate the window when the command asks for it
            if command == COMMAND_RELOAD_AND_SHOW:
                _bring_window_to_front(hwnd)
        
        write_payload_to_shared_memory(payload or {})
        write_command_to_shared_memory(command)
        close_shared_memory()
    else:
        # If even the shared memory cannot be opened, show a fallback message box
        ctypes.windll.user32.MessageBoxW(0,
            tr("single_instance_error_msg", app_name=APP_NAME),
            tr("single_instance_error_title"), 0x40)

def _bring_window_to_front(hwnd: int):
    """Brings the specified window to the front using a reliable method."""
    try:
        # Step 1: Restore the window (handles minimized or hidden states)
        ShowWindow(hwnd, SW_RESTORE)
        
        # Step 2: Set the window as foreground; stronger than a plain show()
        SetForegroundWindow(hwnd)
    except Exception as e:
        print(f"Error bringing window to front: {e}", file=sys.stderr)

def release_mutex():
    """Releases the mutex and shared memory handle on app exit."""
    global _mutex_handle
    close_shared_memory()
    if _mutex_handle:
        ReleaseMutex(_mutex_handle)
        CloseHandle(_mutex_handle)
        _mutex_handle = None