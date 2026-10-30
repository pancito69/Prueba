"""One running application per data directory; locks are released by the OS.

The lock file is deliberately kept after shutdown. Deleting a live lock file can
create a new inode and allow two processes to own what appears to be one lock.
Its presence alone does not mean that another process is running.
"""
from contextlib import contextmanager
from pathlib import Path
import os


class InstanceAlreadyRunning(RuntimeError):
    pass


@contextmanager
def exclusive_instance(data_dir):
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / ".talla-instance.lock").open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstanceAlreadyRunning(
                "Ya hay otra instancia usando esta carpeta de datos: "
                f"{directory.resolve()}. Cierra esa aplicación con Ctrl+C y espera "
                "a que termine antes de iniciar de nuevo. No borres archivos de la base."
            ) from exc
        acquired = True
        handle.seek(1)
        handle.truncate()
        handle.write(f"\nPID={os.getpid()}\n".encode("ascii"))
        handle.flush()
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
