#!/usr/bin/env python3

from argparse import ArgumentParser
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from fnmatch import fnmatch, fnmatchcase
from pathlib import Path
from zipfile import ZipFile
import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess as sp
import sys
import tarfile
import traceback
import typing as tp

#   Python's build-in hash() function is used to tag file data for comparison
#   purposes unless the xxhash package is available. In that case, a 128-bit
#   hash is used, hopefully resulting in fewer unwanted collisions.
try:
    import xxhash
    g_got_xxhash = True

    #   A random 128-bit seed value for the xxh128 algorithm. As of Python 3.3,
    #   the built-in hash() is salted -- presumably to prevent some sort of
    #   DDoS attack -- so might as well do the same with xxhash.
    g_seed = secrets.token_bytes(16)

except ImportError:
    g_got_xxhash = False


# ---- Constants --------------------------------------------------------------


k_version: tp.Final[str] = "v1.4dev"


#   The following are used in parsing -c / --config args.

k_config_key_prefix_rx: tp.Final[tp.Pattern] = re.compile(r"no_?")

k_config_key_aliases: tp.Final[dict[str, str]] = {
    "clone": "clone_files",
    "size": "buf_size",
    "del": "delete_orig",
    "delete": "delete_orig",
    "fmt": "dmg_format",
    "format": "dmg_format",
    "val": "validate",
    "v": "verbosity",
    "verb": "verbosity",
    "xf": "expand_filter"
}

k_format_aliases: tp.Final[dict[str, str]] = {
    "RO": "UDRO",
    "READ_ONLY": "UDRO",
    "UNCMP": "UDRO",
    "GZ": "UDZO",
    "GZIP": "UDZO",
    "ZIP": "UDZO",
    "MAXCOMPAT": "UDZO",
    "FAST": "ULFO",
    "FASTCMP": "ULFO",
    "LZFSE": "ULFO",
    "7Z": "ULMO",
    "7ZIP": "ULMO",
    "XZ": "ULMO",
    "LZMA": "ULMO",
    "MAXCMP": "ULMO"
}


#   Regular expression for identifying supported archive types for the
#   -x / --expand option.

k_expand_rx: tp.Final[tp.Pattern] = re.compile(
    r"\.(dmg|sparse(bundle|image)|tar|t(ar\.)?([gx]z|bz2?|zst)|z(ip|st))$",
    re.IGNORECASE
)


k_applescript_ask_expand: tp.Final[str] = """
use scripting additions
get display dialog "Expand %s?" buttons {"No", "Yes"} default button "Yes"
return (button returned of result) is "Yes"
"""


k_applescript_option_down: tp.Final[str] = """
use scripting additions
use framework "Foundation"
use framework "AppKit" -- for NSEvent

set flags to current application's NSEvent's modifierFlags() as integer
set mask to current application's NSAlternateKeyMask as integer
return ((flags div mask) mod 2) as boolean
"""


# ---- Config Management ------------------------------------------------------


k_config_path: tp.Final[Path] = \
    Path.home()/"Library"/"Preferences"/"apfs_archive.json"


@dataclass
class Config:
    """
    The configuration attributes are described in the READ_ME file.
    """

    auto_expand: bool = True
    blk_size: int = 0x100000  # default = 1 MB
    clone_files: bool = True
    delete_orig: bool = False
    dmg_format: str = "ULMO"  # LZMA-compressed (10.15 Catalina or later)
    expand_filters: list[str] = field(default_factory=lambda: [":*"])
    validate: bool = True
    verbosity: int = 2

    def save(self):
        json_obj = {
            "auto_expand": self.auto_expand,
            "blk_size": self.blk_size,
            "clone_files": self.clone_files,
            "delete_orig": self.delete_orig,
            "dmg_format": self.dmg_format,
            "expand_filters": self.expand_filters,
            "validate": self.validate,
            "verbosity": self.verbosity
        }
        with open(k_config_path, "w") as outf:
            json.dump(json_obj, outf, indent="\t")

    def display(self, outf: tp.TextIO):
        print("auto_expand:", self.auto_expand, file=outf)
        print("blk_size:", self.blk_size, file=outf)
        print("clone_files:", self.clone_files, file=outf)
        print("delete_orig:", self.delete_orig, file=outf)
        print("dmg_format:", self.dmg_format, file=outf)
        print("expand_filters", self.expand_filters, file=outf)
        print("validate:", self.validate, file=outf)
        print("verbosity:", self.verbosity, file=outf)
        if g_got_xxhash:
            print("xxhash will be used", file=outf)


def load_config() -> Config:
    """
    If a config file can be found at ~/Library/Preferences/apfs_archive.json,
    it is loaded from there. Otherwise, this functions returns a
    default-initialized Config.
    """

    if k_config_path.is_file():
        with open(k_config_path) as inf:
            json_obj = json.load(inf)
        return config_from_json(json_obj=json_obj, default=Config())
    return Config()


def config_from_json(json_obj: dict[str, tp.Any], default: Config) -> Config:
    """
    Args:
        json_obj: a json object in dict form
            This is obtained by decoding a config file.
        default: a Config instance
            Any keys that cannot be found in json_obj are obtained from this
            instance instead. It is usually just a default-initialized Config.
    """
    expand_filters = json_obj.get("expand_filters", []) \
        + default.expand_filters
    return Config(
        auto_expand=json_obj.get("auto_expand", default.auto_expand),
        blk_size=json_obj.get("blk_size", default.blk_size),
        clone_files=json_obj.get("clone_files", default.clone_files),
        delete_orig=json_obj.get("delete_orig", default.delete_orig),
        dmg_format=json_obj.get("dmg_format", default.dmg_format),
        expand_filters=expand_filters,
        validate=json_obj.get("validate", default.validate),
        verbosity=json_obj.get("verbosity", default.verbosity)
    )


# ---- Other APFSArchive Support Classes --------------------------------------


@dataclass(frozen=True)
class FileSig:
    """
    This is the key type used by the `scanned` dictionary that hopefully
    uniquely identifies file contents.

    Attributes:
        size: size of the file data in bytes
        hash_val: a hash of the file data
            This may be generated by the built-in hash() function (the int
            case) or by an xxhash algorithm (the bytes digest case).
    """
    size: int
    hash_val: tp.Union[int, bytes]


@dataclass
class RunOutput:
    """
    See run_output attribute of APFSArchive for a description of this.
    """

    total_bytes: int = 0
    cloned_bytes: int = 0
    scanned_files: dict[FileSig, list[Path]] = field(default_factory=dict)
    expand_filter: Callable[[str], bool] = lambda p: True

    def clear(self):
        self.total_bytes = 0
        self.cloned_bytes = 0
        self.scanned_files.clear()


@dataclass
class FindUnusedDstRes:
    """
    Return type of the APFSArchive.find_unused_dst() method.
    """

    path: Path
    used_paths: list[Path]


# ---- APFSArchive ------------------------------------------------------------


@dataclass
class APFSArchive:
    """
    If you import this module from another Python script, you can use an
    instance of APFSArchive and call its run() method to get the same
    functionality as the command line.

    Attributes:
        config:
            This is either read from the Preferences or default-initialized.
        dst_dir:
            If left unassigned, the get_dst_dir() method will return the parent
            directory of the src_path you pass it instead.
        estimate:
            Estimate mode simply scans the source folder in an attempt to
            determine how much storage could be saved just by cloning
            duplicate files alone. No dmg is created in this case.
            WARNING: xxhash is required for this option.
        clone_in_place:
            This is like estimate, except it actually goes ahead and replaces
            duplication through cloning, but without creating any dmg archives.
        expand:
            In this case, the src_path you pass to run() should be a dmg file,
            and run() will expand its contents out into a folder. If the
            clone_files config is selected, it will alse run a clone-in-place
            on the expanded folder. In fact, if the destination folder is
            something like foo_2 because foo was already in use, it will clone
            both together in hopes of finding a lot of overlap.
        outf:
            All program output should be directed here. It defaults to stdout,
            but you could send it to a log file, for example.
        version:
            If True, this prints version info to outf when you call run().
            (You can pass any Path into the method and it will be ignored
            other than being used as the return value.)
        run_output:
            This data structure contains attributes that are updates as the
            run() method progresses. It is a good idea to call its clear()
            method before starting another run with the same APFSArchive
            instance, unless you want to accumulate on top of data from the
            previous run.
    """
    config: Config = field(default_factory=load_config)
    dst_dir: tp.Optional[Path] = None
    estimate: bool = False
    clone_in_place: bool = False
    expand: bool = False
    outf: tp.TextIO = sys.stdout
    errf: tp.TextIO = sys.stderr
    version: bool = False
    run_output: RunOutput = field(default_factory=RunOutput)

    def run(self, src_path: Path) -> Path:
        """
        This method handles whatever processing needs to be done as specified
        by APFSArchive's attributes.

        Args:
            src_path: the source directory

        Returns: the destination path
            This may be the dmg file that is created. In estimate mode, it will
            simply be src_path.
        """

        if self.version:
            print("apfs_archive", k_version, file=self.outf)
            return src_path

        src_path = src_path.resolve()
        src_stat = src_path.stat()

        if self.estimate:
            if self.config.verbosity == 1:
                print("scanning:", src_path, file=self.outf, flush=True)
            self._estimate(src_path)
            return src_path

        if self.clone_in_place:
            if self.config.verbosity == 1:
                print(
                    "cloning in place:", src_path, file=self.outf, flush=True
                )
            self._clone_in_place(src_path)
            return src_path

        if self.expand:
            dst_path = self._expand(src_path)
        else:
            if self.config.verbosity == 1:
                print(
                    "creating dmg archive from:", src_path,
                    file=self.outf, flush=True
                )
            if self.config.clone_files:
                dst_path = self._archive(src_path)
            else:
                dst_path = self._make_dmg(
                    src_path=src_path, format=self.config.dmg_format
                )
            if self.config.verbosity == 1:
                print("created:", dst_path, file=self.outf, flush=True)

        if self.config.validate and dst_path.is_file():
            if self.config.verbosity >= 2:
                print("validating:", dst_path, file=self.outf, flush=True)
            self._sp_run("/usr/bin/hdiutil", "verify", dst_path)

        if self.config.verbosity >= 2:
            self._print_from_to("syncing times", src_path, dst_path)
        os.utime(dst_path, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))

        if self.config.delete_orig:
            if self.config.verbosity >= 2:
                print("deleting:", src_path, file=self.outf, flush=True)
            if src_path.is_dir():
                shutil.rmtree(str(src_path))
            else:
                src_path.unlink()
        return dst_path

    def get_dst_dir(self, src_path: Path) -> Path:
        return self.dst_dir or src_path.parent

    def find_unused_dst(
        self, parent_dir: Path, name: str
    ) -> FindUnusedDstRes:
        """
        Ideally, the destination path would simply be parent_dir/name. But if
        something already exists at that location, this method adds a "_2"
        suffix to the name (before the file extension), followed by a "_3" and
        so on until it finds an unsused path. This path is returned in the
        .path field of a FindUnusedDstRes The .used_paths list is populated by
        all the rejected paths.
        """
        path0 = parent_dir/name
        path = path0
        used_paths: list[Path] = []
        index = 2
        while path.exists():
            used_paths.append(path)
            path = path0.parent/f"{path0.stem}_{index}{path0.suffix}"
            index += 1
        if used_paths and self.config.verbosity >= 2:
            self._print_from_to("changing destination", path0, path)
        return FindUnusedDstRes(path, used_paths)

    def scan_path(
        self, path: Path,
        handle_file_sig_match: Callable[[Path, int, list[Path]], None]
    ):
        """
        This method scans a directory in search of file duplication by calling
        os.walk() on it. Whenever it finds something that looks like it may be
        a duplicate, it invokes the supplied callback function.

        Args:
            path: path to a file or directory
                In the directory case, its contents will be recursed by
                os.walk() in search of files to examine. Note that within a
                source directory, symlinks are ignored (be it to files or
                subdirectories).
            handle_file_sig_match: a callback functor
                This should take 3 positional args and return none:
                    Path: path to candidate file
                    int: size of file data
                    list[Path]: list of files which may contain identical data
                        In a perfect world, the file signature alone (which
                        contains the file size and a hash of its data) should
                        be sufficient to uniquely identify a file. With
                        xxhash's 128-bit digests, there is a strong chance that
                        this would be the case. The list, then, would only ever
                        contain one path.

                        But if you are worried about hash collisions, you can
                        call file_data_matches() on the candidate and each of
                        the listed files. If the new file does not match any of
                        the others, you can add it to the list. Otherwise, you
                        can clone it from the matching file.
        """

        def check_file(file_path: Path):
            file_sig = self.scan_file_data(path=file_path)
            if file_sig.size == 0:
                return
            self.run_output.total_bytes += file_sig.size
            try:
                matching_files = self.run_output.scanned_files[
                    file_sig
                ]
            except KeyError:
                self.run_output.scanned_files[file_sig] = [file_path]
            else:
                handle_file_sig_match(
                    file_path, file_sig.size, matching_files
                )

        path = path.resolve()
        if self.config.verbosity >= 2:
            print(
                "scanning for file duplication:", path,
                file=self.outf, flush=True
            )
        if path.is_dir():
            for base_dir, dir_names, file_names in os.walk(path):
                for file_name in file_names:
                    file_path = Path(base_dir, file_name)
                    if not file_path.is_file() or file_path.is_symlink():
                        continue
                    check_file(file_path)
        elif path.is_file():
            check_file(path)

    def scan_file_data(self, path: Path) -> FileSig:
        """
        Reads the file data and generates a hash as it goes.

        Args:
            path: path to a file

        Returns:
            FileSig contain data size and hash value
        """
        size = 0
        with open(path, "rb") as inf:
            if g_got_xxhash:

                #   With xxhash, it is easy to start from the seed and update
                #   it with file data as we go.
                xxh = xxhash.xxh128(g_seed)
                buf = inf.read(self.config.blk_size)
                while buf:
                    size += len(buf)
                    xxh.update(buf)
                    buf = inf.read(self.config.blk_size)
                return FileSig(size=size, hash_val=xxh.digest())

            #   With the built-in hash, I am not aware of any way to build it
            #   up in piecemeal fashion. So we hash each block, and then make
            #   a hash from the hashes.
            hashes: list[int] = []
            buf = inf.read(self.config.blk_size)
            while buf:
                size += len(buf)
                hashes.append(hash(buf))
                buf = inf.read(self.config.blk_size)

        return FileSig(
            size=size,
            hash_val=hashes[0] if len(hashes) == 1 else hash(tuple(hashes))
        )

    def file_data_matches(self, path1: Path, path2: Path) -> bool:
        """
        This method reads 2 files a blkSize at a time, and compares them to
        check if they contain identical data. It is meant to be called after
        a file signature comparison generates a hit, just to be sure that the
        files truly are identical. (If the hash function is really good, it
        may not be necessary, though in my experience, it doesn't add a huge
        amount of overhead in most cases.)
        """

        with open(path1, "rb") as inf1:
            with open(path2, "rb") as inf2:
                buf1 = inf1.read(self.config.blk_size)
                buf2 = inf2.read(self.config.blk_size)
                while buf1:
                    if buf1 != buf2:
                        return False
                    buf1 = inf1.read(self.config.blk_size)
                    buf2 = inf2.read(self.config.blk_size)
        return buf1 == buf2

    def clone_file(self, target: Path, with_file: Path):
        """
        Deletes the target file and replaces it with a clone of the other.
        """

        if target == with_file:
            print(
                "WARNING: attempt to clone", quoted_path(target), "to itself",
                file=self.errf
            )
            return
        target.unlink()
        self._sp_run("/usr/bin/ditto", "--clone", with_file, target)

    def compile_expand_filter(self):
        """
        This method compiles an expand filter function calculated from
        `config.expand_filters` and stores it at `run_output.expand_filter`.
        It is called automatically by `run` if `expand` is selected.
        """

        def ask_user(path_str: str) -> bool:
            res = self._sp_run(
                "osascript", "-",
                input=k_applescript_ask_expand % (path_str,), stdout=sp.PIPE
            )
            return res.stdout.strip() == "true"

        self.run_output.expand_filter = compile_path_filter(
            filters=self.config.expand_filters, catch_all=ask_user
        )

    def print_run_report(self, dst_path: Path):
        """
        During a run(), some metrics are collected in run_output. This method
        prints a report to outf.

        Args:
            dst_path: this should be whatever run() returns
        """

        ro = self.run_output

        print("total file data bytes scanned:", ro.total_bytes, file=self.outf)

        if self.estimate:
            self.outf.write("estimated ")
        self.outf.write(f"bytes cloned: {ro.cloned_bytes}")
        if ro.total_bytes:
            percentage = ro.cloned_bytes * 100.0 / ro.total_bytes
            self.outf.write(f" ({percentage:.2f}%)")
        print(file=self.outf)

        if dst_path.is_file() and not (self.estimate or self.clone_in_place):
            arc_size = dst_path.stat().st_size
            self.outf.write(f"{quoted_path(dst_path)} file size: {arc_size}")
            if ro.total_bytes:
                percentage = arc_size * 100.0 / ro.total_bytes
                self.outf.write(f" ({percentage:.2f}%)")
            print(file=self.outf)

    def _archive(self, src_path: Path) -> Path:
        with self._make_tmp_dmg(src_path) as tmp_dmg:
            with self._mount_dmg(tmp_dmg) as mount_path:
                self.scan_path(Path(mount_path), self._clone_if_data_match)

            name = f"{src_path.name}.dmg" if src_path.is_dir() \
                else src_path.name
            return self._make_dmg(
                tmp_dmg, format=self.config.dmg_format,
                name=name
            )

    def _clone_in_place(self, src_path: Path):
        self.scan_path(src_path, self._clone_if_data_match)

    def _estimate(self, src_dir: Path):
        def cb(file_path: Path, size: int, match_paths: list[Path]):
            match0 = match_paths[0]
            if self.config.verbosity >= 3:
                self._print_from_to(
                    "cloning may be possible", file_path, match0
                )
            self.run_output.cloned_bytes += size

        #   Since cb doesn't go so far as to call file_data_matches(), we want
        #   a quality hash here to prevent collisions (where 2 files with of
        #   the same size but with different data get the same hash value).
        if not g_got_xxhash:
            print(
                "please install xxhash (python3 -m pip xxhash)",
                file=self.errf
            )
            raise NotImplementedError(
                "estimating clone savings requires xxhash package"
            )

        self.scan_path(src_dir, cb)

    def _expand(self, src_path: Path) -> Path:
        self.compile_expand_filter()

        #   If src_path is a directory, we want to call the recursive
        #   _expand_dir() method on it to copy/clone it with all the archives
        #   found inside it expanded.
        if src_path.is_dir():
            dst = self.find_unused_dst(
                self.get_dst_dir(src_path), src_path.name
            )
            if self.config.verbosity >= 1:
                self._print_from_to(
                    "expanding directory contents", src_path, dst.path
                )
            self._expand_dir(src_path, dst.path)
            if self.config.clone_files and dst.used_paths:
                #   In the event that the destination path was bumped by a
                #   pre-existing directory, run a clone-in-place on the most
                #   recent such directory before doing it again on our new
                #   destination. That way, any shared files between the 2
                #   directories should get cloned.
                self._clone_in_place(dst.used_paths[-1])
                self._clone_in_place(dst.path)
            return dst.path

        match = k_expand_rx.search(src_path.name)
        if not match:
            raise ValueError(f"cannot expand {quoted_path(src_path)}")
        return self._expand_archive(
            src_path, self.get_dst_dir(src_path), match
        )

    def _expand_archive(
        self, src_path: Path, dst_dir: Path, match: re.Match
    ) -> Path:
        #   This is a lower-level method that only handles archive and not
        #   directory expansion.
        #
        #   Args:
        #       src_path: path to a confirmed archive file
        #       dst_dir: candidate destination directory
        #           If it already exists, a new one may be chosen with a "_2"
        #           or greater ending appended to its name. Also note that if
        #           the directory name differs from the root level directory
        #           name within the archive, the contents should appear in an
        #           inner directory with the root's name.
        #
        #   Returns: the destination directory path that was actually chosen
        #       This would be the one with the "_2" or whatever where
        #       necessary.

        outer_dst = self.find_unused_dst(
            dst_dir, src_path.name[: match.start()]
        )
        if match.group() in (".dmg", ".sparsebundle", ".sparseimage"):
            self._expand_dmg(src_path, outer_dst)
        elif match.group() == ".zip":
            self._expand_zip(src_path, outer_dst)
        else:
            self._expand_tar(src_path, outer_dst)
        if self.config.clone_files:
            #   As with the directory expansion case in _expand(), we want to
            #   scan any pre-existing directory ahead of the new one to
            #   eliminate the likely duplication between them.
            if outer_dst.used_paths:
                self._clone_in_place(outer_dst.used_paths[-1])

            #   But in this case, we want to clone the new directory
            #   regardless. (This wasn't necessary in the other case because
            #   _expand_dir() should have cloned everything out of the source
            #   directory already.)
            self._clone_in_place(outer_dst.path)
        return outer_dst.path

    def _expand_dir(self, src_dir: Path, dst_dir: Path) -> int:
        #   This is a recursive function that transfers the contents of a
        #   source directory to a destination. Regular files are copied using
        #   the ditto tool with cloning where possible (i.e. the destination
        #   files should be cloned provided the 2 directories lie within the
        #   same APFS volume). When archives are encountered, however, they
        #   get expanded into the destination directory.
        #
        #   Args:
        #       src_dir: the source directory
        #       dst_dir: the destination directory
        #           This is assumed not to exist yet, since find_unused_dst()
        #           should have been called to generate the outermost
        #           directory.
        #
        #   Returns: number of archives encountered

        arc_matches: list[tuple[Path, re.Match]] = []
        arcs_found = 0
        if self.config.verbosity >= 3:
            print("making directory:", dst_dir)
        dst_dir.mkdir(parents=True)

        #   This initial loop handles the clone/copying of regular files, but
        #   defers the archive expansion for later. This is to ensure that the
        #   archive-level find_unused_dst() logic kicks in after the regular
        #   files and directories have found their new homes.
        for src_path in src_dir.iterdir():
            dst_path = dst_dir/src_path.name
            if src_path.is_dir(follow_symlinks=False):
                arcs_found += self._expand_dir(src_path, dst_path)
            elif src_path.is_symlink():
                #   Symlinks should be copied as-is.
                if self.config.verbosity >= 3:
                    self._print_from_to("copying symlink", src_path, dst_path)
                shutil.copy2(src_path, dst_path, follow_symlinks=False)
            else:
                match = k_expand_rx.search(src_path.name)
                if match and self.run_output.expand_filter(str(src_path)):
                    arc_matches.append((src_path, match))
                else:
                    if self.config.verbosity >= 3:
                        self._print_from_to("copy/cloning", src_path, dst_path)
                    self._sp_run(
                        "/usr/bin/ditto", "--clone", src_path, dst_path
                    )

        #   Now, we go ahead and expand any archives encountered earlier.
        for arc_path, match in arc_matches:
            self._expand_archive(arc_path, dst_dir, match)

        arcs_found += len(arc_matches)
        return arcs_found

    def _expand_dmg(self, src_path: Path, outer_dst: FindUnusedDstRes):
        with self._mount_dmg(src_path) as mount_path:
            inner_name = mount_path.name
            if inner_name != outer_dst.path.name:
                if self.config.verbosity >= 3:
                    print("making directory:", outer_dst.path, file=self.outf)
                outer_dst.path.mkdir(parents=True)
                inner_dir = outer_dst.path/inner_name
            else:
                inner_dir = outer_dst.path
            if self.config.verbosity >= 2:
                self._print_from_to("copying", mount_path, inner_dir)
            elif self.config.verbosity == 1:
                self._print_from_to("expanding", src_path, inner_dir)
            v_opt = ["-V"] if self.config.verbosity >= 3 else []
            self._sp_run("/usr/bin/ditto", *v_opt, mount_path, inner_dir)

    def _expand_tar(self, src_path: Path, outer_dst: FindUnusedDstRes):
        if self.config.verbosity >= 3:
            print("scanning:", src_path, file=self.outf)
        has_root_dir = True
        root_name = ""
        has_metadata = False
        with tarfile.open(src_path) as tf:
            for name in tf.getnames():
                path = Path(name)
                if path.name.startswith("._"):
                    has_metadata = True
                    continue
                root = path.parts[0]
                if not root_name:
                    root_name = root
                elif root != root_name:
                    has_root_dir = False
            if has_metadata and self.config.verbosity >= 3:
                print(
                    "Mac-specific file system metadata found",
                    file=self.outf
                )
            if has_root_dir and root_name == outer_dst.path.name:
                tar_dir = outer_dst.path.parent
            else:
                if self.config.verbosity >= 3:
                    print(
                        "making directory:", quoted_path(outer_dst.path),
                        file=self.outf
                    )
                outer_dst.path.mkdir(parents=True)
                tar_dir = outer_dst.path
            if self.config.verbosity >= 1:
                if has_root_dir:
                    self._print_from_to(
                        "expanding", src_path, tar_dir/root_name
                    )
                else:
                    self._print_from_to(
                        "expanding contents", src_path, tar_dir
                    )
            if not has_metadata:
                tf.extractall(path=str(tar_dir))
                return
        self._sp_run("/usr/bin/tar", "-xvf", src_path, "-C", tar_dir)

    def _expand_zip(self, src_path: Path, outer_dst: FindUnusedDstRes):
        if self.config.verbosity >= 3:
            print("scanning:", src_path, file=self.outf)
        has_root_dir = True
        root_name = ""
        has_metadata = False
        with ZipFile(src_path) as zf:
            for name in zf.namelist():
                path = Path(name)
                if path.name.startswith("._"):
                    has_metadata = True
                    continue
                root = path.parts[0]
                if root == "__MACOSX":
                    has_metadata = True
                elif not root_name:
                    root_name = root
                elif root != root_name:
                    has_root_dir = False
            if has_metadata:
                if self.config.verbosity >= 3:
                    print(
                        "Mac-specific file system metadata found",
                        file=self.outf
                    )
            if has_root_dir and root_name == outer_dst.path.name:
                unzip_dir = outer_dst.path.parent
            else:
                if self.config.verbosity >= 3:
                    print(
                        "making directory:", quoted_path(outer_dst.path),
                        file=self.outf
                    )
                outer_dst.path.mkdir(parents=True)
                unzip_dir = outer_dst.path
            if self.config.verbosity >= 1:
                if has_root_dir:
                    self._print_from_to(
                        "expanding", src_path, unzip_dir/root_name
                    )
                else:
                    self._print_from_to(
                        "expanding contents", src_path, unzip_dir
                    )
            if not has_metadata:
                zf.extractall(path=str(unzip_dir))
                return
        self._sp_run("/usr/bin/ditto", "-xkV", src_path, unzip_dir)

    def _make_dmg(
        self, src_path: Path,
        format: str = "UDRW",
        name: str = "{}.dmg"
    ) -> Path:
        """
        This method runs hdiutil to make a new dmg file.

        Args:
            src_path: path to a source directory or dmg file
                In the latter case, `hdutil convert` will be run instead of
                `hdiutil create`.
            format: hdiutil's --format arg
            name: name of the dmg file (without parent path)
                If this contains "{}", that will replaced by src_path's name
                using the str.format() function. So if your src_path were
                /path/to/foo, the default name would be "foo.dmg".

        Returns: path to the new dmg file

        The function may raise an exception if anything goes wrong.
        """

        if name.find("{}") >= 0:
            name = name.format(
                src_path.name if src_path.is_dir() else src_path.stem
            )
        dmg_path = self.find_unused_dst(self.get_dst_dir(src_path), name).path
        if src_path.is_dir():
            if self.config.verbosity >= 2:
                print(
                    "creating", quoted_path(dmg_path),
                    "from directory", quoted_path(src_path),
                    file=self.outf, flush=True
                )
            self._sp_run(
                "/usr/bin/hdiutil", "create",
                "-srcfolder", src_path,
                "-fs", "APFS",
                "-format", format,
                "-volname", src_path.name,
                dmg_path
            )
        else:
            if self.config.verbosity >= 2:
                print(
                    "converting", quoted_path(dmg_path),
                    "from archive", quoted_path(src_path),
                    file=self.outf, flush=True
                )
            self._sp_run(
                "/usr/bin/hdiutil", "convert", src_path,
                "-format", format,
                "-o", dmg_path
            )
        return dmg_path

    @contextmanager
    def _make_tmp_dmg(
        self, src_path: Path,
        format: str = "UDRW",
        name: str = "{}_tmp.dmg"
    ) -> Iterator[Path]:
        """
        This is much like _make_dmg(), except its context manager automatically
        deletes the temporary dmg file when you leave the with block.
        """

        dmg_path = self._make_dmg(src_path, format, name)
        try:
            yield dmg_path
        finally:
            if self.config.verbosity >= 2:
                print("deleting", quoted_path(dmg_path), file=self.outf)
            dmg_path.unlink()

    @contextmanager
    def _mount_dmg(
        self, dmg_path: Path
    ) -> Iterator[Path]:
        """
        A context manager for mounting a dmg. It yields the mounted path
        (something like /Volumes/foo). Then it automatically unmounts it as
        you leave the with block.
        """

        if self.config.verbosity >= 2:
            print(
                "temporarily mounting:", dmg_path,
                file=self.outf, flush=True
            )
        res = self._sp_run(
            "/usr/bin/hdiutil", "attach", dmg_path, stdout=sp.PIPE
        )
        device = ""
        volume = ""
        for line in res.stdout.splitlines():
            if not device and line.strip().startswith("/dev/"):
                device = line.split()[0]
            i = line.find("/Volumes")
            if i >= 0:
                volume = line[i:].strip()
        if not device or not volume:
            raise ValueError("unexpected output from `hdituil attach`")
        if self.config.verbosity >= 3:
            print("mounted device:", device, file=self.outf)
            print("mounted volume:", volume, file=self.outf)

        try:
            yield Path(volume)
        finally:
            if self.config.verbosity >= 2:
                print("unmounting:", dmg_path, file=self.outf)
            self._sp_run("/usr/bin/hdiutil", "detach", device)

    def _clone_if_data_match(
        self, target: Path, size: int, candidates: list[Path]
    ):
        for candidate in candidates:
            if self.file_data_matches(target, candidate):
                if self.config.verbosity >= 3:
                    self._print_from_to(
                        f"cloning {size}-byte file", candidate, target)
                self.clone_file(target, candidate)
                self.run_output.cloned_bytes += size
                break
        else:
            print("WARNING: same hash matches multiple files", file=self.errf)
            candidates.append(target)

    def _sp_run(self, *args, **kwargs) -> sp.CompletedProcess:
        if self.config.verbosity >= 3:
            print(">", shlex.join(map(str, args)), file=self.outf)
        kwargs.setdefault("check", True)
        kwargs.setdefault(
            "stdout", self.outf if self.config.verbosity >= 2 else sp.DEVNULL
        )
        kwargs.setdefault(
            "stderr", sp.STDOUT if self.config.verbosity >= 2 else self.errf
        )
        kwargs.setdefault("text", True)
        return sp.run([str(a) for a in args], **kwargs)

    def _print_from_to(self, prompt: str, from_path: Path, to_path: Path):
        print(prompt, "from:", file=self.outf)
        print(f"\t{from_path}", file=self.outf)
        print(f"to:\t{to_path}", file=self.outf)
        if self.config.verbosity <= 2:
            self.outf.flush()


# ---- Stand-Alone Utility Functions ------------------------------------------


def compile_path_filter(
    filters: Iterable[str],
    catch_all: Callable[[str], bool] = lambda _: True
) -> Callable[[str], bool]:
    """
    The logic that compiles apfs_archive's expand filter has been spun out into
    its own stand-alone function in case you find it useful in other contexts?

    The compilation involves reducing the invariant parts of each filter's
    algorithm using closures.

    Args:
        filters:
            Each string in this iterable represents a filter as described in
            the READ_ME (e.g. "x:*.txt" if you wanted to exclude text files
            for some reason).
        catch_all:
            A callback that handle the case in which none of the filters
            match the path string. The default callback simply returns `True`.

    Returns: a single function that takes a path and returns a boolean
        The boolean indicates whether the path should be accepted or rejected.
        Note that the path needs to be in string form.
    """

    # ---- Filter Function Factories ------------------------------------------
    #
    #   8 factories are defined here to cover every possible combination of
    #   flags, of which there are 3.
    #
    #   Each factory takes a pattern string. That's the part following the the
    #   ":" in the filter definition. The return value is a function that takes
    #   a path in string form and returns an integer. The integer may be 1 for
    #   accept, -1 for reject, or 0 for filter did not match.
    #
    #   The # in make_fn# indicates the index at which the factory will reside
    #   within the upcoming `factories` tuple. The factories are ordered in
    #   such a way as to line up with the integer flags value returned by
    #   `calc_fn_index`.

    def make_fn0(pattern: str) -> Callable[[str], int]:  # flags: ""

        def fn(path_str: str) -> int:
            return 1 if fnmatch(path_str, pattern) else 0

        return fn

    def make_fn1(pattern: str) -> Callable[[str], int]:  # flags: "x"

        def fn(path_str: str) -> int:
            return -1 if fnmatch(path_str, pattern) else 0

        return fn

    def make_fn2(pattern: str) -> Callable[[str], int]:  # flags: "c"

        def fn(path_str: str) -> int:
            return 1 if fnmatchcase(path_str, pattern) else 0

        return fn

    def make_fn3(pattern: str) -> Callable[[str], int]:  # flags: "xc"

        def fn(path_str: str) -> int:
            return -1 if fnmatchcase(path_str, pattern) else 0

        return fn

    def make_fn4(pattern: str) -> Callable[[str], int]:  # flags: "r"
        rx = re.compile(pattern, re.IGNORECASE)

        def fn(path_str: str) -> int:
            return 1 if rx.search(path_str) else 0

        return fn

    def make_fn5(pattern: str) -> Callable[[str], int]:  # flags: "xr"
        rx = re.compile(pattern, re.IGNORECASE)

        def fn(path_str: str) -> int:
            return -1 if rx.search(path_str) else 0

        return fn

    def make_fn6(pattern: str) -> Callable[[str], int]:  # flags: "cr"
        rx = re.compile(pattern)

        def fn(path_str: str) -> int:
            return 1 if rx.search(path_str) else 0

        return fn

    def make_fn7(pattern: str) -> Callable[[str], int]:  # flags: "xcr"
        rx = re.compile(pattern)

        def fn(path_str: str) -> int:
            return -1 if rx.search(path_str) else 0

        return fn

    factories = (
        make_fn0,
        make_fn1,
        make_fn2,
        make_fn3,
        make_fn4,
        make_fn5,
        make_fn6,
        make_fn7
    )

    # --- Filter Function Look-Up ---------------------------------------------

    k_flag_list = "xcr"

    def calc_fn_index(flags: str) -> int:
        #   Given the flags in string form (e.g. "xc"), this function returns
        #   them as an integer index that should line up with the appropriate
        #   factory function in `factories` (e.g. 3 for "xc").
        #
        #   Args:
        #       flags: flags in string form
        #   Returns: flags in integer form
        index = 0
        for flag in flags:

            #   The `k_flag_list` string gives the possible flag characters in
            #   such an order that the position specifies a bit number within
            #   the index integer.
            pos = k_flag_list.find(flag)
            if pos < 0:
                raise ValueError(
                    f'unrecognized flag "{flag}" in expand filter'
                )

            #   Turn the position into a bit mask and set the appropriate bit
            #   within `index`.
            index |= 1 << pos
        return index

    # ---- Build List of Functions Matching Each Filter -----------------------
    #
    #   Each filter string in `filters` needs to get a corresponding function
    #   that can be called to apply the filter.

    filter_fns: list[Callable[[str], int]] = []
    for fltr in filters:

        #   Ideally, every filter string should have the format
        #   "FLAGS:PATTERN", but it is possible we may only have "PATTERN".
        parts = fltr.split(":", maxsplit=1)
        if len(parts) == 1:
            parts = ("", parts[0])
        flags, pattern = parts

        #   A blank pattern indicates that any further filters are to be
        #   discarded, and the catch-all should handle anything that makes it
        #   this far.
        if not pattern:
            break

        #   Use the flags to determine the appropriate factory function to
        #   create the filter function, and build it around the pattern.
        filter_fns.append(factories[calc_fn_index(flags)](pattern))

    # ---- Define the Combined Filter Function --------------------------------
    #
    #   This function should run through all the `filter_fns` looking for a
    #   match to the given path. Failing that, it should fall back on the
    #   catch-all function.

    def combined_filter_fn(path_str: str) -> bool:
        for fn in filter_fns:
            res = fn(path_str)
            if res > 0:
                return True
            if res < 0:
                return False
        return catch_all(path_str)

    return combined_filter_fn


def quoted_path(path: Path) -> str:
    return shlex.quote(str(path))


# ---- Self-Executing Entry Points --------------------------------------------


def command_line_run():
    """
    This function should run when apfs_archive.py is run directly. In the
    Automator case, there is a separate automator_run() function.
    """

    def parse_c_opts(parse_res: tp.Any) -> dict[str, tp.Any]:
        #   This function is designed to parse the -c / --config args on the
        #   command line (if any).
        #
        #   Args:
        #       parse_res: return value of argparse.parse_args()
        #
        #   Returns:
        #       json object in dict form
        #
        #   Raises:
        #       ValueError if parsing opts string fails

        first = True
        try:
            iof = io.StringIO()
            iof.write("{")
            if parse_res.config:
                expand_filters: list[str] = []
                for arg in parse_res.config:
                    tup = arg.split(":", 1)
                    key = tup[0].strip().strip('"')
                    if len(tup) == 1:
                        match = k_config_key_prefix_rx.match(key)
                        if match:
                            key = key[match.end():]
                            val = "false"
                        else:
                            val = "true"
                    else:
                        val = tup[1].strip().strip('"')
                    key = k_config_key_aliases.get(key, key)
                    if key == "dmg_format":
                        val = val.upper()
                        val = k_format_aliases.get(val, val)
                        val = f'"{val}"'
                    elif key == "expand_filter":
                        expand_filters.append(val)
                        continue
                    elif key == "expand_filters":
                        expand_filters.extend(json.loads(val))
                        continue
                    if first:
                        first = False
                    else:
                        iof.write(", ")
                    iof.write(f'"{key}": {val}')
                if expand_filters:
                    if not first:
                        iof.write(", ")
                    iof.write('"expand_filters": ')
                    json.dump(expand_filters, iof)
            iof.write("}")
            iof.seek(0)
            print(iof.getvalue())
            return json.load(iof)
        except Exception as ex:
            raise ValueError(f"could not parse -c/--config arg ({ex})")

    ap = ArgumentParser(
        description="""
            This script creates a compressed disk image (.dmg) from a
            directory. The internal format within the image is APFS, and before
            the final image is created, any duplicate files are cloned to
            reduce storage size.
            """
    )
    ap.add_argument(
        "src_paths", metavar="SRC_PATH", nargs="*",
        help="""
            Source path(s) to process. In the default mode of creating dmg
            files, the path would typically be the directory to archive. It
            could also be another dmg file. In that case, it will be remade
            after a cloning pass (assuming clone_files is selected in config).
            Note that the path(s) should not be symlinks.
            """
    )
    ap.add_argument(
        "-d", "--dst-dir", default="",
        help="""
            Destination directory, where relevant. If needed but not supplied,
            the destination directory will be the parent directory of each
            SRC_PATH."""
    )
    ap.add_argument(
        "-e", "--estimate", action="store_true",
        help="""
            In estimate mode, no dmg is created. Rather, the source directory
            is scanned to estimate how much space may be saved just from
            cloning duplicate files alone.  NB: This option requires the
            xxhash package. Install with: python3 -m pip install xxhash
            """
    )
    ap.add_argument(
        "-C", "--config-file", default="",
        help=f"""
            Use a json config file to set run parameters. These will also be
            saved to {quoted_path(k_config_path)} to use as future defaults.
            """
    )
    ap.add_argument(
        "-c", "--config", action="append",
        help="""
            This option allows you to override a single configuration parameter
            such as -c fmt:zip (to create an zip-compressed dmg). You may
            specify more than one -c option to override multiple parameters.
            Unlike -C, these options do not overwrite the defaults. See the
            READ_ME file for more info on configuration parameters.
            """
    )
    ap.add_argument(
        "-p", "--clone-in-place", action="store_true",
        help="""
            Rather than archiving each SRC_PATH as a dmg, this option looks for
            file duplication and attempts to clone files in-place. Clearly, the
            volume needs to be formatted AFPS for this to have any useful
            effect. If more than one SRC_PATH is supplied, cloning candidates
            will be searched both within and between them. The latter can be
            useful in situation where you suspect a "foo" and "foo copy"
            directory to contain similar--if not identical--contents. (Note
            that this option will perform the cloning even if the clone_files
            config is set false. It would have nothing to do otherwise.)
            """
    )
    ap.add_argument(
        "-x", "--expand", action="store_true",
        help="""
            With the expand option, a dmg file's contents are expanded out into
            a directory. apfs_archive can also expand other formats like zip
            and tar, and if you pass in a directory, it clones the directory
            and expands any archives within it as it goes. See the READ_ME for
            more details.
            """
    )
    ap.add_argument(
        "--version", action="store_true",
        help="Prints a version string and exits."
    )

    res = ap.parse_args()

    if res.version:
        APFSArchive(version=True).run(Path())
        return

    dst_dir = Path(res.dst_dir) if res.dst_dir else None
    try:

        #   With the -C (--config-file) option, we first load the config into
        #   memory and then save it back to:
        #       ~/Library/Preferences/apfs_archive.json
        if res.config_file:
            with open(res.config_file) as inf:
                json_obj = json.load(inf)
            config_from_json(json_obj, Config()).save()

        #   APFSArchive's initializer should then try to load any config in
        #   Library/Preferences.
        arc = APFSArchive(dst_dir=dst_dir)

        #   Next, override the defaults with anything specified through
        #   -c (--config) options.
        arc.config = config_from_json(parse_c_opts(res), arc.config)

        arc.estimate = res.estimate
        arc.clone_in_place = res.clone_in_place
        arc.expand = res.expand
        if arc.config.verbosity >= 2:
            arc.config.display(outf=sys.stdout)
            print("estimate mode:", arc.estimate)
            print(
                "clone_in_place mode:",
                not arc.estimate and arc.clone_in_place
            )
            print(
                "expand mode:",
                not (arc.estimate or arc.clone_in_place) and
                arc.expand
            )
        collect_output = arc.estimate or arc.clone_in_place

        if res.src_paths:
            dst_path = Path()
            err: tp.Optional[Exception] = None
            for src_path in map(Path, res.src_paths):
                try:
                    if not collect_output:
                        arc.run_output.clear()
                    dst_path = arc.run(src_path=src_path)
                    if not collect_output:
                        if arc.estimate or arc.config.verbosity >= 2:
                            arc.print_run_report(dst_path)
                except Exception as err0:
                    err = err or err0
            if err:
                raise err
            if collect_output and (arc.estimate or arc.config.verbosity >= 2):
                arc.print_run_report(dst_path)
        else:
            print("WARNING: no directories specified", file=arc.errf)

        if arc.config.verbosity >= 2:
            print("complete", file=arc.outf)
    except Exception as err:
        print("ERROR:", err, file=sys.stderr)
        if k_version.find("dev") >= 0:
            traceback.print_exc()
        sys.exit(1)


def automator_run():
    log_path = Path.home()/"Library"/"Logs"/"apfs_archive.log"
    with open(log_path, "w") as outf:
        cmd = ["open", "-a", "Console", str(log_path)]
        sp.run(cmd, stdout=outf, stderr=sp.STDOUT, text=True)
        arc = APFSArchive(outf=outf)
        if arc.config.verbosity >= 2:
            arc.config.display(outf=outf)
        err: tp.Optional[Exception] = None
        for src_path in map(Path, sys.argv[1:]):
            try:
                arc.run_output.clear()
                if arc.config.auto_expand:
                    arc.expand = not src_path.is_dir()
                if arc.config.verbosity >= 2:
                    arc.print_run_report(arc.run(src_path=src_path))
            except Exception as err0:
                err = err or err0
        if err:
            raise err
        print("complete", file=outf)
    sp.run(['osascript', '-e', 'tell application "Finder" to activate\nbeep'])


if __name__ == "__main__":
    if sys.argv[0].find("-c") >= 0:
        automator_run()
    else:
        command_line_run()
