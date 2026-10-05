"""Whether this machine has a Vulkan device, asked once per process.

The artwork upscaler (Real-ESRGAN ncnn) needs one. The probe loads the Vulkan loader, makes a
bare instance and counts physical devices; any failure on the way means no.
docs/maps_contract.md §4.1.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
import threading

__all__ = ["probe_vulkan", "vulkan_available"]

_LOADERS = {"win32": ("vulkan-1",), "darwin": ("vulkan", "MoltenVK")}
_DEFAULT_LOADERS = ("vulkan", "libvulkan.so.1")

#: ``VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO``.
_INSTANCE_CREATE_INFO = 1

_lock = threading.Lock()
_answer: bool | None = None


class _InstanceCreateInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.c_void_p),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.c_void_p),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.c_void_p),
    ]


def _load():
    for name in _LOADERS.get(sys.platform, _DEFAULT_LOADERS):
        path = ctypes.util.find_library(name) or name
        try:
            return ctypes.WinDLL(path) if sys.platform == "win32" else ctypes.CDLL(path)
        except OSError:
            continue
    return None


def probe_vulkan() -> bool:
    """One real probe: a loader, an instance, and at least one physical device."""
    lib = _load()
    if lib is None:
        return False
    try:
        info = _InstanceCreateInfo(_INSTANCE_CREATE_INFO, None, 0, None, 0, None, 0, None)
        instance = ctypes.c_void_p()
        if lib.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(instance)) != 0:
            return False
        try:
            count = ctypes.c_uint32(0)
            lib.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None)
            return count.value > 0
        finally:
            lib.vkDestroyInstance(instance, None)
    except (OSError, AttributeError, ValueError):
        return False


def vulkan_available() -> bool:
    """The probe's answer, computed on the first call and kept for the process."""
    global _answer
    with _lock:
        if _answer is None:
            _answer = probe_vulkan()
        return _answer
