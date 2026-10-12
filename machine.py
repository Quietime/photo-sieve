"""This computer, detected once: cores (performance and efficiency), memory and disk type,
and the decoding, buffering and cache limits derived from them."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import shutil
import sys

GB = 1024 ** 3
MB = 1024 ** 2


@dataclass(frozen=True)
class Profile:
    logical: int        # hardware threads
    physical: int       # cores
    performance: int    # cores in the highest efficiency class; every core on a non-hybrid CPU
    efficiency: int     # E-cores on a hybrid CPU
    memory: int         # bytes of physical memory

    def decoders(self):
        """Decoder processes: physical cores less two, at most 12. Beyond about 12 a laptop's package power limit gives
        little more throughput while lowering the interface core's clock; each process may need ~250 MB."""
        override = os.environ.get('PHOTOSIEVE_DECODERS', '')
        if override.isdigit() and int(override) > 0:
            return int(override)
        by_cores = max(2, min(12, self.physical - (1 if self.physical <= 6 else 2)))
        by_memory = max(2, int(self.memory * .15 // (250 * MB))) if self.memory else by_cores
        return min(by_cores, by_memory)

    def interface_threads(self):
        """Threads in the interface process that turn cached previews into pane-sized images. Few on purpose:
        they share the interface's GIL, and the heavy decoding happens in the decoder processes."""
        return max(2, min(4, self.performance // 2))

    def buffer(self, preview_bytes):
        """Decoded previews kept ready ahead: up to 200, within 4% of memory (at most 3 GB)."""
        budget = min(self.memory * .04, 3 * GB) if self.memory else 600 * MB
        return max(24, min(200, int(budget // max(1, preview_bytes))))

    def describe(self):
        cores = f'{self.physical} 核（{self.performance}P+{self.efficiency}E）' if self.efficiency else f'{self.physical} 核'
        return f'{cores} · {self.memory / GB:.0f} GB 内存'

    def preview_capacity(self,requested,preview_bytes,gpu=False):
        """Bound custom preload by physical RAM and renderer headroom."""
        budget=min(self.memory*.10,2*GB) if self.memory else 512*MB
        if gpu:budget=min(budget,512*MB)
        return max(16,min(requested,int(budget//max(1,preview_bytes))))


def _windows_cores():
    """Count cores and their efficiency classes with GetLogicalProcessorInformationEx(RelationProcessorCore)."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    size = ctypes.c_ulong(0)
    kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not kernel32.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(size)):
        raise OSError(ctypes.get_last_error())
    return parse_core_records(buffer.raw[:size.value])


def parse_core_records(raw):
    """SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX records: Relationship (DWORD), Size (DWORD), then
    PROCESSOR_RELATIONSHIP with Flags (BYTE, LTP_PC_SMT=1) and EfficiencyClass (BYTE) at offsets 8 and 9."""
    classes, threads, offset = [], 0, 0
    while offset + 10 <= len(raw):
        relationship = int.from_bytes(raw[offset:offset + 4], 'little')
        record = int.from_bytes(raw[offset + 4:offset + 8], 'little')
        if record <= 0:
            break
        if relationship == 0:
            classes.append(raw[offset + 9]); threads += 2 if raw[offset + 8] & 1 else 1
        offset += record
    if not classes:
        raise ValueError('no processor cores reported')
    top = max(classes); performance = classes.count(top)
    return threads, len(classes), performance, len(classes) - performance


def _linux_cores():
    cores = set()
    for cpu in Path('/sys/devices/system/cpu').glob('cpu[0-9]*'):
        try:
            cores.add((cpu / 'topology/physical_package_id').read_text().strip() + ':' + (cpu / 'topology/core_id').read_text().strip())
        except OSError:
            pass
    logical = os.cpu_count() or 4
    physical = len(cores) or logical
    return logical, physical, physical, 0


def _memory():
    if os.name == 'nt':
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong),
                        ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        status = MEMORYSTATUSEX(); status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys
        return 0
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError, AttributeError):
        return 0


def detect():
    try:
        logical, physical, performance, efficiency = _windows_cores() if os.name == 'nt' else _linux_cores()
    except Exception:
        logical = os.cpu_count() or 4; physical = performance = max(1, logical // 2 if logical > 4 else logical); efficiency = 0
    return Profile(logical, physical, performance, efficiency, _memory())


PROFILE = detect()


def cache_default_mb(directory):
    """Disk preview cache: 5% of free space on the cache drive, between 2 GB and 20 GB."""
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return 2048
    return int(max(2 * GB, min(20 * GB, free * .05)) // MB)


def seek_penalty(path):
    """True for a spinning disk, False for SSD and NVMe, None when unknown (network shares, unsupported systems)."""
    try:
        return _windows_seek_penalty(str(path)) if os.name == 'nt' else _linux_rotational(Path(path))
    except Exception:
        return None


def _windows_seek_penalty(path):
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    root = ctypes.create_unicode_buffer(260)
    if not kernel32.GetVolumePathNameW(path, root, 260):
        return None
    mount = root.value
    if mount.startswith('\\\\') and not mount.startswith('\\\\?\\'):
        return None   # network share
    if len(mount) <= 3 and mount[1:2] == ':':   # a drive root such as E:\ (a disk mounted on a folder has its own volume)
        device = '\\\\.\\' + mount[:2]
    else:
        name = ctypes.create_unicode_buffer(64)
        if not kernel32.GetVolumeNameForVolumeMountPointW(mount, name, 64):
            return None
        device = name.value.rstrip('\\').replace('\\\\?\\', '\\\\.\\', 1)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(device, 0, 3, None, 3, 0, None)   # no access rights needed; share read+write; OPEN_EXISTING
    if handle in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        class Query(ctypes.Structure):
            _fields_ = [('PropertyId', ctypes.c_int), ('QueryType', ctypes.c_int), ('AdditionalParameters', ctypes.c_ubyte * 1)]
        class SeekPenalty(ctypes.Structure):
            _fields_ = [('Version', wintypes.DWORD), ('Size', wintypes.DWORD), ('IncursSeekPenalty', ctypes.c_ubyte)]
        query = Query(7, 0)   # StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
        result = SeekPenalty(); returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(handle, 0x2D1400, ctypes.byref(query), ctypes.sizeof(query),   # IOCTL_STORAGE_QUERY_PROPERTY
                                        ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned), None):
            return None
        return bool(result.IncursSeekPenalty)
    finally:
        kernel32.CloseHandle(handle)


def _linux_rotational(path):
    device = os.stat(path).st_dev
    block = Path(f'/sys/dev/block/{os.major(device)}:{os.minor(device)}').resolve()
    for candidate in (block, block.parent):
        flag = candidate / 'queue' / 'rotational'
        if flag.is_file():
            return flag.read_text().strip() == '1'
    return None


def _kernel32():
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]; kernel32.SetPriorityClass.restype = wintypes.BOOL
    kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetProcessInformation.restype = wintypes.BOOL
    return kernel32


EXECUTION_SPEED, IGNORE_TIMER_RESOLUTION = 0x1, 0x4


def set_power_throttling(*attempts):
    """SetProcessInformation(ProcessPowerThrottling). Each call replaces the whole state, so every flag goes into
    one call; attempts are (ControlMask, StateMask) pairs tried in order until one is accepted."""
    if os.name != 'nt':
        return False
    class PowerThrottling(ctypes.Structure):
        _fields_ = [('Version', ctypes.c_ulong), ('ControlMask', ctypes.c_ulong), ('StateMask', ctypes.c_ulong)]
    try:
        kernel32 = _kernel32()
        for control, state in attempts:
            value = PowerThrottling(1, control, state)
            if kernel32.SetProcessInformation(kernel32.GetCurrentProcess(), 4, ctypes.byref(value), ctypes.sizeof(value)):   # ProcessPowerThrottling
                return True
    except (AttributeError, OSError):
        pass
    return False   # Windows before 10 1709


def decoder_qos():
    """Decoder processes: never EcoQoS (which would hold them at the most efficient clock on efficient cores, as
    Windows does to the descendants of a minimized or covered window), and no timer-resolution requests of their own."""
    return set_power_throttling((EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION, IGNORE_TIMER_RESOLUTION), (EXECUTION_SPEED, 0))


def interface_qos():
    """The interface process: high QoS even when visible but unfocused; timer behaviour stays system-managed."""
    return set_power_throttling((EXECUTION_SPEED, 0))


def fine_timer():
    """1 ms timer resolution for the interface process (Windows 11 drops it by itself while the window is hidden).
    Without it a GIL hand-off between threads waits up to one 15.6 ms tick, whatever sys.setswitchinterval says."""
    if os.name != 'nt':
        return False
    try:
        winmm = ctypes.WinDLL('winmm')
        winmm.timeBeginPeriod.argtypes = winmm.timeEndPeriod.argtypes = [ctypes.c_uint]
        if winmm.timeBeginPeriod(1) == 0:   # TIMERR_NOERROR
            import atexit
            atexit.register(winmm.timeEndPeriod, 1)
            return True
    except (AttributeError, OSError):
        pass
    return False


def below_normal():
    if os.name == 'nt':
        try:
            kernel32 = _kernel32(); kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL_PRIORITY_CLASS
        except (AttributeError, OSError):
            pass
    else:
        try:
            os.nice(5)
        except OSError:
            pass


if __name__ == '__main__':
    print(PROFILE, PROFILE.describe(), 'decoders', PROFILE.decoders(), 'interface threads', PROFILE.interface_threads(),
          'buffer at 6 MB', PROFILE.buffer(6 * MB), file=sys.stdout)
