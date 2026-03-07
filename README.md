# apfs_archive 1.1.1

A utility for creating compressed .dmg files on the macOS platform that uses
APFS cloning to further reduce archive size.

## Minimum Requirements

* Mac OS X 10.13 (High Sierra) or later (for APFS file system support)
* Python 3.7 or later interpreter

## Overview

Apple's APFS file system supports cloning files. Two files with identical
contents can share a single storage area, saving considerable disk space.

Unfortunately, when you try to create a disk image of cloned files, they
expand out to have separate copies of the same data. This utility attempts to
find those identical files and clone them once more before creating a final,
compressed .dmg archive.

## Usage

### apfs_archive.py Script

You can enter:

    python3 apfs_archive.py -h

to see usage notes on this script. Generally, you just enter:

    python3 apfs_archive.py /path/to/foo_dir

and it produces a:

    /path/to/foo_dir.dmg

(Note that if the latter already exists, it will get overwritten.)

### Configuration

Configuration options can either be loaded from a JSON file or set individually
on the `apfs_archive.py` command line using the `-c` switch. They include

| Key         | Value                                               | Default |
| :---------- | :-------------------------------------------------- | ------: |
| buf_size    | maximum bytes read from a file at a time            | 1048576 |
| clone_files | do actually scan for duplicate files and clone them | true    |
| dmg_format  | hdiutil format code to select .dmg compression type | "ULMO"  |

Note that within a JSON file, the keys would need to be enclosed in "".

#### buf_size

To keep the memory footprint of the script reasonable, this limits how much
data may be loaded from a file into memory at a time. It defaults to 1 MB.
Note that up to 2 files may be open simultaneously, making the effective
memory footprint 2 MB.

#### dmg_format

Disk images can come in a variety of different formats, but for this
application, you would most likely want to use one of these:

* "UDRO" uncompressed read-only image
  * good choice when your files are already compressed
  * photos, music, videos, and even pdfs may fall into this category
* "UDZO" zlib compression applied
  * this is similar to what you would get with a .zip file
* "ULFO" lzfse compression applied
  * essentially Apple's version of .zip
  * it reputedly has similar compression levels but runs faster
  * requires Mac OS X 10.11 (El Capitan) or later
* "ULMO" lzma compression applied (the default)
  * this is the tightest compression available
  * requires macOS 10.15 (Catalina) or later

If you want to use say "UDRO" just once, you can try:

    python3 apfs_archive.py -c 'dmg_format:"UDRO"' /path/to/foo_dir

(Unlike the -C option, it will not change the default format.)

### APFS Archive

An Automator app called "APFS Archive" compresses folders you drop on it into
dmg files in much the same way as the apfs_archive.py script does. If you want
to change its configuration settings, you can run:

    python3 apfs_archive.py -C /path/to/my_config.json

### xxhash

When present, the xxhash Python package may improve scanning directories for
duplicate files. You can install it with:

    python3 -m pip install xxhash

## Revision History

1.2 (2026-03-07)

* added clone_files config that can be set false to disable cloning phase
* added -e option to estimate how much effect cloning phase would have
* minor fix to make script that made it require Python 3.10 and not 3.7

1.1 (2026-03-06)

* Automator app added
* added a note in this README about installing xxhash

1.0 (2026-03-05)

* initial release

## To-Do

* investigate sub-file level cloning
