"""Files reached through descriptors, never through names.

omayap writes in four places: its runtime directory, its config, the plugin's
own state file, and a recordings tree the user gets to point anywhere. Every
one of those is a path this process resolves again on each access, and a path
resolved twice can mean two different files. Swap a directory for a symlink
between the check and the write and a transcript lands somewhere else; swap the
pid file and a signal goes somewhere else.

So a directory is opened once, verified, and kept as a descriptor, and
everything after that is openat/unlinkat/renameat relative to it with
O_NOFOLLOW on the last component. A symlink where we expect a file is an error
rather than a redirect, and renaming a temporary file into place is two
descriptors and a name, not a path.

Getting to that first descriptor still means walking a path. The walk is done
one component at a time, refusing to descend into anything that is not owned by
us or by root, or that anyone else can write to. Symlinked components are
followed, because a home directory or a recordings tree on another disk is a
perfectly ordinary thing to have, but only out of a directory that already
passed those checks.
"""

from __future__ import annotations

import errno
import os
import stat as stat_module

# Our own directories are ours alone. The recordings tree is the user's, and
# keeps whatever mode it already had.
PRIVATE = 0o700
FILE_MODE = 0o600

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class Unsafe(OSError):
    """A directory on the way is not one we are willing to write in."""


def _inspect(fd: int, path: str, *, mode: int = PRIVATE) -> None:
    """A directory omayap owns: ours, and nobody else's business.

    Writable by anyone else and it is refused outright, because anything could
    already be sitting in it. Merely readable by others is tightened in place:
    early versions of setup.sh made ~/.config/omayap with the umask's mode, and
    nobody is served by an upgrade that stops working over it. Nothing can have
    been planted in a directory other people cannot write to.
    """
    info = os.stat(fd)
    if info.st_uid != os.getuid():
        raise Unsafe(f"{path} is owned by uid {info.st_uid}, not by you")
    if info.st_mode & 0o022:
        raise Unsafe(f"{path} is writable by group or world — omayap will not use it")
    if info.st_mode & 0o077:
        os.fchmod(fd, mode)


class Dir:
    """An open directory, and every operation that happens inside it."""

    __slots__ = ("fd", "path")

    def __init__(self, fd: int, path: str) -> None:
        self.fd = fd
        self.path = path

    # ---- lifecycle ------------------------------------------------------

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            finally:
                self.fd = -1

    @property
    def name(self) -> str:
        """The directory's own name. Session directories are known by it."""
        return os.path.basename(self.path)

    def __enter__(self) -> "Dir":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __truediv__(self, name: str) -> str:
        """Display only. Nothing in here opens the string this returns."""
        return os.path.join(self.path, name)

    def __str__(self) -> str:
        return self.path

    # ---- directories ----------------------------------------------------

    def child(self, name: str, *, create: bool = False, mode: int = PRIVATE) -> "Dir":
        """A subdirectory, as its own descriptor. Never follows a symlink."""
        _bare(name)
        if create:
            self.mkdir(name, mode=mode, exist_ok=True)
        fd = os.open(name, _OPEN_DIR, dir_fd=self.fd)
        path = os.path.join(self.path, name)
        try:
            _inspect(fd, path)
        except Unsafe:
            os.close(fd)
            raise
        return Dir(fd, path)

    def mkdir(self, name: str, *, mode: int = PRIVATE, exist_ok: bool = False) -> None:
        _bare(name)
        try:
            os.mkdir(name, mode, dir_fd=self.fd)
        except FileExistsError:
            if not exist_ok:
                raise
            return
        # mkdir's mode is whatever the umask leaves of it, and a private
        # directory that is private except on Tuesdays is not private.
        os.chmod(name, mode, dir_fd=self.fd, follow_symlinks=False)

    def names(self) -> list:
        """Everything in the directory, read from the descriptor itself."""
        return os.listdir(self.fd)

    # ---- one file -------------------------------------------------------

    def open(self, name: str, flags: int, mode: int = FILE_MODE) -> int:
        """Open a file inside this directory. Never a symlink, always ours.

        The owner check is the other half of the directory check: the
        directory cannot be written to by anyone else, so a file in it that
        belongs to somebody else is a file from a time when that was not true.
        """
        _bare(name)
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=self.fd)
        try:
            if os.fstat(fd).st_uid != os.getuid():
                raise Unsafe(f"{os.path.join(self.path, name)} is not yours")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def stat(self, name: str):
        """lstat, so a symlink reports as a symlink instead of its target."""
        _bare(name)
        try:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except OSError:
            return None

    def is_file(self, name: str) -> bool:
        info = self.stat(name)
        return bool(info and stat_module.S_ISREG(info.st_mode))

    def is_dir(self, name: str) -> bool:
        info = self.stat(name)
        return bool(info and stat_module.S_ISDIR(info.st_mode))

    def is_fifo(self, name: str) -> bool:
        info = self.stat(name)
        return bool(info and stat_module.S_ISFIFO(info.st_mode))

    def size(self, name: str) -> int:
        info = self.stat(name)
        return info.st_size if info else 0

    def read_bytes(self, name: str) -> bytes:
        fd = self.open(name, os.O_RDONLY)
        try:
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(fd)

    def read_text(self, name: str) -> str:
        return self.read_bytes(name).decode("utf-8")

    def write(self, name: str, data, *, mode: int = FILE_MODE) -> None:
        """Replace a file's contents atomically, without leaving the directory.

        The temporary lands in the same directory under a name nothing else
        uses, and the rename is descriptor-relative on both sides, so neither
        end can be redirected between the write and the rename.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else data
        tmp = f".{name}.{os.getpid()}.tmp"
        self.unlink(tmp)
        fd = self.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            _write_all(fd, payload)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            self.unlink(tmp)
            raise
        os.close(fd)
        os.replace(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def rewrite(self, name: str, data, *, mode: int = FILE_MODE) -> None:
        """Truncate and rewrite in place, keeping the inode.

        For the state file only: the QML side holds a watch on that exact
        inode, and replacing the file would leave it watching a deleted one.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else data
        fd = self.open(name, os.O_WRONLY | os.O_CREAT, mode)
        try:
            if os.fstat(fd).st_mode & 0o777 != mode:
                # An older omayap wrote this one with the umask's mode, and
                # the state file names the meeting that is being recorded.
                os.fchmod(fd, mode)
            os.ftruncate(fd, 0)
            _write_all(fd, payload)
        finally:
            os.close(fd)

    def append(self, name: str, text: str, *, mode: int = FILE_MODE) -> None:
        fd = self.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode)
        try:
            _write_all(fd, text.encode("utf-8"))
        finally:
            os.close(fd)

    def unlink(self, name: str, *, missing_ok: bool = True) -> None:
        _bare(name)
        try:
            os.unlink(name, dir_fd=self.fd)
        except FileNotFoundError:
            if not missing_ok:
                raise
        except IsADirectoryError:
            os.rmdir(name, dir_fd=self.fd)

    def rmdir(self, name: str) -> None:
        _bare(name)
        try:
            os.rmdir(name, dir_fd=self.fd)
        except OSError:
            pass

    def mkfifo(self, name: str, *, mode: int = FILE_MODE) -> None:
        _bare(name)
        os.mkfifo(name, mode, dir_fd=self.fd)
        os.chmod(name, mode, dir_fd=self.fd, follow_symlinks=False)


def _bare(name: str) -> None:
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"{name!r} is not a name inside one directory")


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        view = view[os.write(fd, view) :]


def open_dir(path, *, create: bool = False, mode: int = PRIVATE, private: bool = True) -> Dir:
    """Walk to a directory one component at a time and keep the descriptor.

    Every component is opened with O_NOFOLLOW out of a directory that has
    already been checked, and every directory opened on the way is checked
    before anything is opened out of it. A symlink anywhere on the path is an
    error, not something to resolve: following one means trusting whoever can
    write where it points, and the two directories omayap is pointed at are
    ones it creates itself.

    `private` is the difference between a directory omayap owns and one the
    user handed it. Ours is forced to 0700. The recordings tree keeps whatever
    mode the user gave it, as long as nobody else can write there.
    """
    parts = _components(str(path))
    fd = os.open("/", _OPEN_DIR)
    here = "/"
    try:
        while parts:
            _ancestor(fd, here)
            name = parts.pop(0)
            child = os.path.join(here, name)
            try:
                nested = os.open(name, _OPEN_DIR, dir_fd=fd)
            except OSError as error:
                # O_NOFOLLOW with O_DIRECTORY reports a symlink as ENOTDIR on
                # Linux and ELOOP elsewhere, and ENOTDIR is also what a plain
                # file gives, so the link is confirmed rather than assumed.
                if error.errno in (errno.ELOOP, errno.ENOTDIR) and _is_link(fd, name):
                    raise Unsafe(
                        f"{child} is a symlink — point omayap at the real directory"
                    ) from error
                if error.errno != errno.ENOENT or not create:
                    raise
                os.mkdir(name, mode, dir_fd=fd)
                os.chmod(name, mode, dir_fd=fd, follow_symlinks=False)
                nested = os.open(name, _OPEN_DIR, dir_fd=fd)
            os.close(fd)
            fd = nested
            here = child
        if private:
            _inspect(fd, here, mode=mode)
        else:
            _shared(fd, here)
        return Dir(fd, here)
    except BaseException:
        os.close(fd)
        raise


def _ancestor(fd: int, path: str) -> None:
    """A directory we only pass through: root's or ours, and nobody else's.

    The sticky bit is the exception, and the only one. /tmp is 1777 and always
    has been; what makes it usable is that sticky means only an entry's owner
    can rename or remove it, so nobody can swap out a directory of ours that
    sits in there.
    """
    info = os.stat(fd)
    if info.st_uid not in (0, os.getuid()):
        raise Unsafe(f"{path} is owned by uid {info.st_uid}")
    if info.st_mode & 0o022 and not info.st_mode & stat_module.S_ISVTX:
        raise Unsafe(f"{path} is writable by group or world")


def _is_link(fd: int, name: str) -> bool:
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except OSError:
        return False
    return stat_module.S_ISLNK(info.st_mode)


def _shared(fd: int, path: str) -> None:
    """The weaker check, for a directory the user chose and may share."""
    info = os.stat(fd)
    if info.st_uid != os.getuid():
        raise Unsafe(f"{path} is owned by uid {info.st_uid}, not by you")
    if info.st_mode & 0o022:
        raise Unsafe(f"{path} is writable by group or world")


def adopt(fd: int, path: str) -> Dir:
    """Take over a directory descriptor inherited from the daemon.

    Checked again on this side, cheaply and without resolving anything: an
    fstat says whether the thing handed over is a directory and whose it is,
    which is all this process can usefully know about a descriptor it did not
    open.
    """
    info = os.fstat(fd)
    if not stat_module.S_ISDIR(info.st_mode):
        raise Unsafe(f"descriptor {fd} is not a directory")
    if info.st_uid != os.getuid():
        raise Unsafe(f"descriptor {fd} is owned by uid {info.st_uid}, not by you")
    return Dir(fd, path)


def _components(path: str) -> list:
    return [part for part in path.split("/") if part not in ("", ".")]
