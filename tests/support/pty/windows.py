import ctypes
import shutil
import subprocess
import typing
from ctypes import wintypes
from pathlib import Path

from .contract import PtyEndOfFile
from .contract import TerminalSize


_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_ERROR_BROKEN_PIPE = 109
_ERROR_INVALID_HANDLE = 6
_ERROR_NOT_FOUND = 1168
_ERROR_OPERATION_ABORTED = 995
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_PSEUDOCONSOLE_RESIZE_QUIRK = 0x00000002
_STARTF_USESTDHANDLES = 0x00000100
_STILL_ACTIVE = 259


class _Coord(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD),
        ("y_size", wintypes.DWORD),
        ("x_count_chars", wintypes.DWORD),
        ("y_count_chars", wintypes.DWORD),
        ("fill_attribute", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show_window", wintypes.WORD),
        ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.POINTER(ctypes.c_ubyte)),
        ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE),
        ("stderr", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("startup_info", _StartupInfo),
        ("attribute_list", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("process_id", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _BasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


def _handle_value(handle: int | wintypes.HANDLE) -> int:
    """把有效 Win32 HANDLE 收窄为整数。"""
    if isinstance(handle, int):
        return handle
    value = handle.value
    if value is None:
        raise OSError("Win32 returned an empty handle")
    return int(value)


def _raise_hresult(operation: str, result: int) -> typing.NoReturn:
    """把 HRESULT 转为包含操作名的 OSError。"""
    error = result & 0xFFFF
    raise OSError(error, f"{operation} failed: {ctypes.FormatError(error)}")


class _WindowsJob:
    """使用 Job Object 取得并清理完整进程树所有权。"""

    def __init__(self, kernel32: ctypes.WinDLL) -> None:
        self._kernel32 = kernel32
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle: int | None = int(handle)
        try:
            information = _ExtendedLimitInformation()
            information.basic_limit_information.limit_flags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not kernel32.SetInformationJobObject(
                self._handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError:
            self.close()
            raise

    @property
    def handle(self) -> int:
        """返回用于原子进程创建属性的 Job 句柄。"""
        return self._required_handle()

    def terminate(self) -> None:
        """终止 Job 中的所有进程。"""
        handle = self._required_handle()
        if not self._kernel32.TerminateJobObject(handle, 1):
            error = ctypes.get_last_error()
            if error != _ERROR_INVALID_HANDLE:
                raise ctypes.WinError(error)

    def close(self) -> None:
        """关闭 Job，按 kill-on-close 清理仍存活的进程。"""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        if not self._kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def _required_handle(self) -> int:
        handle = self._handle
        if handle is None:
            raise RuntimeError("Windows job is closed")
        return handle


class WindowsPtyBackend:
    """直接使用 Windows ConPTY 管理终端、同步管道和进程树。"""

    def __init__(
        self,
        argv: typing.Sequence[str],
        *,
        cwd: Path,
        env: typing.Mapping[str, str],
        size: TerminalSize,
    ) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_functions()
        self._job = _WindowsJob(self._kernel32)
        self._input_write: int | None = None
        self._output_read: int | None = None
        self._pseudo_console: int | None = None
        self._process: int | None = None
        self._pid: int | None = None
        self._closed = False
        self._spawn(argv, cwd=cwd, env=env, size=size)

    @property
    def pid(self) -> int:
        """返回 ConPTY 根进程标识。"""
        pid = self._pid
        if pid is None:
            raise RuntimeError("ConPTY process was not created")
        return pid

    def read(self, size: int) -> bytes:
        """通过独立同步管道完整读取 ConPTY 原始 UTF-8 输出。"""
        output = self._required_output()
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not self._kernel32.ReadFile(
            output,
            buffer,
            size,
            ctypes.byref(read),
            None,
        ):
            error = ctypes.get_last_error()
            if error in {
                _ERROR_BROKEN_PIPE,
                _ERROR_INVALID_HANDLE,
                _ERROR_OPERATION_ABORTED,
            }:
                raise PtyEndOfFile
            raise ctypes.WinError(error)
        if read.value == 0:
            raise PtyEndOfFile
        return buffer.raw[:read.value]

    def write(self, data: bytes) -> int:
        """完整写入 ConPTY 同步输入管道。"""
        input_handle = self._required_input()
        total = 0
        while total < len(data):
            chunk = data[total:]
            buffer = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            if not self._kernel32.WriteFile(
                input_handle,
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if written.value == 0:
                raise OSError("ConPTY input write made no progress")
            total += written.value
        return total

    def resize(self, size: TerminalSize) -> None:
        """更新 ConPTY 缓冲区行列。"""
        result = self._kernel32.ResizePseudoConsole(
            self._required_console(),
            _Coord(size.columns, size.rows),
        )
        if result != 0:
            _raise_hresult("ResizePseudoConsole", result)

    def interrupt(self) -> None:
        """向 Win32 Input Mode 写入带 Ctrl 修饰状态的 C 键事件。"""
        self.write(b"\x1b[67;46;3;1;8;1_\x1b[67;46;3;0;8;1_")

    def is_alive(self) -> bool:
        """通过进程退出码判断根进程是否仍存活。"""
        process = self._process
        if process is None:
            return False
        exit_code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(
            process,
            ctypes.byref(exit_code),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return exit_code.value == _STILL_ACTIVE

    def wait(self) -> int:
        """返回已退出根进程的退出码，并关闭 ConPTY 输出端。"""
        process = self._required_process()
        exit_code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(
            process,
            ctypes.byref(exit_code),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value == _STILL_ACTIVE:
            raise RuntimeError("ConPTY process is still active")
        self._close_pseudoconsole()
        return int(exit_code.value)

    def terminate(self) -> None:
        """终止 ConPTY Job 中的完整进程树。"""
        if self.is_alive():
            self._job.terminate()

    def kill(self) -> None:
        """强制终止 ConPTY Job 中的完整进程树。"""
        self._job.terminate()

    def close(self) -> None:
        """幂等释放伪终端、同步管道、进程和 Job 句柄。"""
        if self._closed:
            return
        self._closed = True
        try:
            if self.is_alive():
                self._job.terminate()
            self._close_input()
            self._close_pseudoconsole()
            self._cancel_output()
        finally:
            self._close_output()
            self._close_process()
            self._job.close()

    def _spawn(
        self,
        argv: typing.Sequence[str],
        *,
        cwd: Path,
        env: typing.Mapping[str, str],
        size: TerminalSize,
    ) -> None:
        """创建同步管道、HPCON 和原子加入 Job 的根进程。"""
        executable = shutil.which(argv[0], path=env.get("PATH"))
        if executable is None:
            raise FileNotFoundError(f"PTY executable was not found: {argv[0]}")

        input_read: int | None = None
        output_write: int | None = None
        thread_handle: int | None = None
        attribute_list: ctypes.Array | None = None
        try:
            input_read, self._input_write = self._create_pipe()
            self._output_read, output_write = self._create_pipe()
            pseudo_console = wintypes.HANDLE()
            result = self._kernel32.CreatePseudoConsole(
                _Coord(size.columns, size.rows),
                input_read,
                output_write,
                _PSEUDOCONSOLE_RESIZE_QUIRK,
                ctypes.byref(pseudo_console),
            )
            if result != 0:
                _raise_hresult("CreatePseudoConsole", result)
            self._pseudo_console = _handle_value(pseudo_console)

            attribute_size = ctypes.c_size_t()
            self._kernel32.InitializeProcThreadAttributeList(
                None,
                2,
                0,
                ctypes.byref(attribute_size),
            )
            attribute_list = ctypes.create_string_buffer(attribute_size.value)
            attribute_pointer = ctypes.cast(attribute_list, ctypes.c_void_p)
            if not self._kernel32.InitializeProcThreadAttributeList(
                attribute_pointer,
                2,
                0,
                ctypes.byref(attribute_size),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            pseudo_console_value = ctypes.c_void_p(self._required_console())
            if not self._kernel32.UpdateProcThreadAttribute(
                attribute_pointer,
                0,
                _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                pseudo_console_value,
                ctypes.sizeof(pseudo_console_value),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            job_values = (wintypes.HANDLE * 1)(self._job.handle)
            if not self._kernel32.UpdateProcThreadAttribute(
                attribute_pointer,
                0,
                _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.cast(job_values, ctypes.c_void_p),
                ctypes.sizeof(job_values),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            startup = _StartupInfoEx()
            startup.startup_info.cb = ctypes.sizeof(startup)
            startup.startup_info.flags = _STARTF_USESTDHANDLES
            invalid_handle = wintypes.HANDLE(-1).value
            startup.startup_info.stdin = invalid_handle
            startup.startup_info.stdout = invalid_handle
            startup.startup_info.stderr = invalid_handle
            startup.attribute_list = attribute_pointer
            process_information = _ProcessInformation()
            command_line = ctypes.create_unicode_buffer(
                subprocess.list2cmdline(list(argv))
            )
            environment = ctypes.create_unicode_buffer(
                "\0".join(
                    f"{key}={value}"
                    for key, value in sorted(
                        env.items(),
                        key=lambda item: item[0].casefold(),
                    )
                ) + "\0\0"
            )
            flags = _CREATE_UNICODE_ENVIRONMENT | _EXTENDED_STARTUPINFO_PRESENT
            if not self._kernel32.CreateProcessW(
                executable,
                command_line,
                None,
                None,
                False,
                flags,
                environment,
                str(cwd),
                ctypes.byref(startup),
                ctypes.byref(process_information),
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            self._process = _handle_value(process_information.process)
            self._pid = int(process_information.process_id)
            thread_handle = _handle_value(process_information.thread)
        except Exception:
            if self._process is not None:
                self._kernel32.TerminateProcess(self._process, 1)
            self._close_pseudoconsole()
            self._close_input()
            self._close_output()
            self._close_process()
            self._job.close()
            raise
        finally:
            if attribute_list is not None:
                self._kernel32.DeleteProcThreadAttributeList(
                    ctypes.cast(attribute_list, ctypes.c_void_p)
                )
            for handle in (input_read, output_write, thread_handle):
                if handle is not None:
                    self._close_handle(handle)

    def _configure_functions(self) -> None:
        """固定本 adapter 使用的 Win32 函数签名。"""
        kernel32 = self._kernel32
        kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.CreatePipe.restype = wintypes.BOOL
        kernel32.CreatePseudoConsole.argtypes = [
            _Coord,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        kernel32.CreatePseudoConsole.restype = ctypes.c_long
        kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, _Coord]
        kernel32.ResizePseudoConsole.restype = ctypes.c_long
        kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        kernel32.ClosePseudoConsole.restype = None
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.DeleteProcThreadAttributeList.restype = None
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(_ProcessInformation),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.CancelIoEx.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

    def _create_pipe(self) -> tuple[int, int]:
        """创建 ConPTY 要求的同步匿名管道。"""
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        if not self._kernel32.CreatePipe(
            ctypes.byref(read_handle),
            ctypes.byref(write_handle),
            None,
            0,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return _handle_value(read_handle), _handle_value(write_handle)

    def _cancel_output(self) -> None:
        """解除 reader 线程中的同步 ReadFile。"""
        output = self._output_read
        if output is None:
            return
        if self._kernel32.CancelIoEx(output, None):
            return
        error = ctypes.get_last_error()
        if error not in {_ERROR_INVALID_HANDLE, _ERROR_NOT_FOUND}:
            raise ctypes.WinError(error)

    def _close_input(self) -> None:
        """关闭父侧 ConPTY 输入句柄。"""
        handle = self._input_write
        if handle is not None:
            self._input_write = None
            self._close_handle(handle)

    def _close_output(self) -> None:
        """关闭父侧 ConPTY 输出句柄。"""
        handle = self._output_read
        if handle is not None:
            self._output_read = None
            self._close_handle(handle)

    def _close_process(self) -> None:
        """关闭根进程查询句柄。"""
        handle = self._process
        if handle is not None:
            self._process = None
            self._close_handle(handle)

    def _close_pseudoconsole(self) -> None:
        """释放 HPCON 资源。"""
        handle = self._pseudo_console
        if handle is not None:
            self._pseudo_console = None
            self._kernel32.ClosePseudoConsole(handle)

    def _close_handle(self, handle: int) -> None:
        """关闭普通 Win32 HANDLE。"""
        if not self._kernel32.CloseHandle(handle):
            error = ctypes.get_last_error()
            if error != _ERROR_INVALID_HANDLE:
                raise ctypes.WinError(error)

    def _required_input(self) -> int:
        handle = self._input_write
        if handle is None:
            raise RuntimeError("ConPTY input is closed")
        return handle

    def _required_output(self) -> int:
        handle = self._output_read
        if handle is None:
            raise PtyEndOfFile
        return handle

    def _required_console(self) -> int:
        handle = self._pseudo_console
        if handle is None:
            raise RuntimeError("ConPTY is closed")
        return handle

    def _required_process(self) -> int:
        handle = self._process
        if handle is None:
            raise RuntimeError("ConPTY process handle is closed")
        return handle
