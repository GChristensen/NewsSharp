"""The optional wake-up pipe between the web and worker processes (worker/wakeup.py), R0.17.
It carries single bytes as "check your inbox" signals; losing it only degrades latency to the
200 ms polling interval (R0.16)."""
import os
import threading

ENV_READ = "NEWSSHARP_WAKE_R"
ENV_WRITE = "NEWSSHARP_WAKE_W"

_writer_event = threading.Event()
_writer_started = False
_lock = threading.Lock()


def make_pipe() -> tuple[int, int, list[int]]:
    """Create an inheritable pipe in the supervisor. Returns (read id, write id, fds to pass);
    the ids are file descriptors on POSIX and OS handles on Windows."""
    read_fd, write_fd = os.pipe()
    if os.name == "nt":
        import msvcrt

        read_handle, write_handle = msvcrt.get_osfhandle(read_fd), msvcrt.get_osfhandle(write_fd)
        os.set_handle_inheritable(read_handle, True)
        os.set_handle_inheritable(write_handle, True)
        return read_handle, write_handle, []
    os.set_inheritable(read_fd, True)
    os.set_inheritable(write_fd, True)
    return read_fd, write_fd, [read_fd, write_fd]


def _open(env_name: str, flags: int) -> int | None:
    value = os.environ.get(env_name)
    if not value:
        return None
    try:
        if os.name == "nt":
            import msvcrt

            return msvcrt.open_osfhandle(int(value), flags)
        return int(value)
    except (ValueError, OSError):
        return None


def start_reader(callback) -> bool:
    """Call `callback` (from a daemon thread) whenever the other process signals."""
    fd = _open(ENV_READ, os.O_RDONLY)
    if fd is None:
        return False

    def loop():
        while True:
            try:
                if not os.read(fd, 256):
                    return
            except OSError:
                return
            try:
                callback()
            except Exception:
                pass

    threading.Thread(target=loop, name="wake-reader", daemon=True).start()
    return True


def notify() -> None:
    """Signal the other process. Never blocks the caller: the write happens in a helper thread."""
    global _writer_started
    if not os.environ.get(ENV_WRITE):
        return
    with _lock:
        if not _writer_started:
            fd = _open(ENV_WRITE, os.O_WRONLY)
            if fd is None:
                os.environ.pop(ENV_WRITE, None)
                return

            def loop():
                while True:
                    _writer_event.wait()
                    _writer_event.clear()
                    try:
                        os.write(fd, b"x")
                    except OSError:
                        return

            threading.Thread(target=loop, name="wake-writer", daemon=True).start()
            _writer_started = True
    _writer_event.set()
