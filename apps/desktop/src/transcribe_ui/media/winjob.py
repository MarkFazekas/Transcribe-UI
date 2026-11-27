"""Windows job objects: a tool and every process it starts are killed together.

`Popen.kill` on Windows ends only the direct child. A package-manager shim (Chocolatey's
`ffmpeg.exe`) starts the real ffmpeg as its own child, which would survive, keep the pipes open
and keep writing the partial file. So the child is created suspended, put into a job whose
processes die when its last handle closes (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), and only then
resumed; anything it starts joins the job. Closing the job kills what is left, also when the app
itself dies, since the OS then closes the handle.

Only imported on Windows (`runner.py`).
"""

import logging
import sys
from ctypes import Structure, byref, c_int, c_int64, c_long, c_size_t, c_ulonglong, c_void_p, sizeof, wintypes

# Tells mypy (and a stray import elsewhere) that the rest is Windows-only.
assert sys.platform == "win32"

from ctypes import WinDLL, get_last_error  # noqa: E402 - exist only on Windows, so after the guard

logger = logging.getLogger(__name__)

# `SetInformationJobObject` class of `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`.
EXTENDED_LIMIT_INFORMATION = 9
# Limit flag `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: the processes die when the last handle closes.
KILL_ON_JOB_CLOSE = 0x2000
# Access right `PROCESS_SET_QUOTA`, needed to put a process into a job.
PROCESS_SET_QUOTA = 0x100
# Access right `PROCESS_TERMINATE`, also needed to put a process into a job.
PROCESS_TERMINATE = 0x1
# Access right `PROCESS_SUSPEND_RESUME`, needed to resume the suspended process.
PROCESS_SUSPEND_RESUME = 0x800
# Exit code of processes ended through the job.
KILLED_EXIT_CODE = 1


class IoCounters(Structure):
    """`IO_COUNTERS`."""

    _fields_ = (  # noqa: WPS120 - the ctypes API names this attribute
        ("ReadOperationCount", c_ulonglong),
        ("WriteOperationCount", c_ulonglong),
        ("OtherOperationCount", c_ulonglong),
        ("ReadTransferCount", c_ulonglong),
        ("WriteTransferCount", c_ulonglong),
        ("OtherTransferCount", c_ulonglong),
    )


class BasicLimits(Structure):
    """`JOBOBJECT_BASIC_LIMIT_INFORMATION`."""

    _fields_ = (  # noqa: WPS120 - the ctypes API names this attribute
        ("PerProcessUserTimeLimit", c_int64),
        ("PerJobUserTimeLimit", c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", c_size_t),
        ("MaximumWorkingSetSize", c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class ExtendedLimits(Structure):
    """`JOBOBJECT_EXTENDED_LIMIT_INFORMATION`."""

    _fields_ = (  # noqa: WPS120 - the ctypes API names this attribute
        ("BasicLimitInformation", BasicLimits),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", c_size_t),
        ("JobMemoryLimit", c_size_t),
        ("PeakProcessMemoryUsage", c_size_t),
        ("PeakJobMemoryUsage", c_size_t),
    )


def _kernel32() -> WinDLL:
    library = WinDLL("kernel32", use_last_error=True)
    library.CreateJobObjectW.argtypes = (c_void_p, wintypes.LPCWSTR)
    library.CreateJobObjectW.restype = wintypes.HANDLE
    library.SetInformationJobObject.argtypes = (wintypes.HANDLE, c_int, c_void_p, wintypes.DWORD)
    library.SetInformationJobObject.restype = wintypes.BOOL
    library.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    library.OpenProcess.restype = wintypes.HANDLE
    library.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    library.AssignProcessToJobObject.restype = wintypes.BOOL
    library.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    library.TerminateJobObject.restype = wintypes.BOOL
    library.CloseHandle.argtypes = (wintypes.HANDLE,)
    library.CloseHandle.restype = wintypes.BOOL
    return library


def _ntdll() -> WinDLL:
    library = WinDLL("ntdll")
    # Resumes every thread of a process; Popen does not keep the main thread's handle.
    library.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    library.NtResumeProcess.restype = c_long
    return library


def _last_error(call: str) -> OSError:
    """Return the last Windows error of a failed call as an OSError."""
    code = get_last_error()
    return OSError(code, f"{call} failed (Windows error {code})")


def _check(succeeded: object, call: str) -> None:
    """Raise the last Windows error when a call returned 0 or NULL.

    Raises:
        OSError: If the call failed.
    """
    if not succeeded:
        raise _last_error(call)


class KillOnCloseJob:
    """A job object whose processes die when it is terminated or closed."""

    def __init__(self) -> None:
        """Create the job with the kill-on-close limit.

        Raises:
            OSError: The job cannot be created or configured.
        """
        self._kernel32 = _kernel32()
        job = self._kernel32.CreateJobObjectW(None, None)
        _check(job, "CreateJobObjectW")
        self._job: int | None = job
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
        configured = self._kernel32.SetInformationJobObject(
            job,
            EXTENDED_LIMIT_INFORMATION,
            byref(limits),
            sizeof(limits),
        )
        if not configured:
            error = _last_error("SetInformationJobObject")
            self.close()
            raise error

    def adopt_and_resume(self, pid: int) -> None:
        """Put the suspended process into the job, then let it run.

        Raises:
            OSError: The process cannot be opened, assigned or resumed (the caller kills it).
        """
        access = PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME
        process = self._kernel32.OpenProcess(access, False, pid)  # noqa: FBT003 - Win32 BOOL argument
        _check(process, "OpenProcess")
        try:
            self._assign_and_resume(process)
        except OSError:
            self._kernel32.CloseHandle(process)
            raise
        self._kernel32.CloseHandle(process)

    def terminate(self) -> None:
        """Kill every process in the job."""
        if self._job is None:
            return
        if not self._kernel32.TerminateJobObject(self._job, KILLED_EXIT_CODE):
            logger.warning("TerminateJobObject failed (Windows error %s)", get_last_error())

    def close(self) -> None:
        """Close the job; processes still in it are killed."""
        if self._job is not None:
            self._kernel32.CloseHandle(self._job)
            self._job = None

    def _assign_and_resume(self, process: int) -> None:
        _check(self._kernel32.AssignProcessToJobObject(self._job, process), "AssignProcessToJobObject")
        status = _ntdll().NtResumeProcess(process)
        if status != 0:
            message = f"NtResumeProcess failed (status {status:#x})"
            raise OSError(status, message)
