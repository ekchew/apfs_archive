# apfs_archive 1.2.2

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
| delete_orig | delete original after successfully processing       | false   |
| dmg_format  | hdiutil format code to select .dmg compression type | "ULMO"  |
| validate    | run hdiutil verify on new dmgs                      | true    |

Note that within a JSON file, the keys would need to be enclosed in "".

The validate option is not strictly necessary, but may provide some peace of
mind when using it in conjunction with delete_orig, as the the deletion will
not take place until validation has completed successfully.

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

(Unlike the -C option, it will not change the default format. Enter
`python3 apfs_archive.py -h` for more details.)

### Automator Support

**UPDATE**: The Automator archive has been removed from this project, on account
of it coming to my attention that it will be rejected by macOS when copied to
other computers due to its not being digitally signed. In all honesty, GUI
binaries are not a great fit within git repositories to begin with, so I am
replacing it with instructions on how you can create the app yourself.
It's not that hard!

#### Creating the Automator App

1. In Automator, go File Menu → New, and choose to create a new Application.
2. Drag a Run Shell Script action into your workflow.
3. Select the `/usr/local/bin/python3` shell.
4. Replace the sample script with everything in apfs_archive.py.
5. Save the application as APFS Archive and you're done!

(You may also be able to create other Automator tools like folder actions and
what not. I have not experimented with this personally.)

#### Using the Automator App

When you drag a folder onto the app, it should create a dmg in the same parent
folder. It also opens the Console utility so that you can watch its progress.

There is no graphic interface for configuring the app, but it reads its config
from `~/Library/Preferences/apfs_archive.json`. You can install your own config
file to that location by running:

    python3 apfs_archive.py -C /path/to/my_config.json

#### Python3

As mentioned under System Requirements, the script requires Python 3.7 or later.
Running apfs_archive.py directly on the command line should work as long as
`python3` exists somewhere in your `PATH` directories.

Automator, on the other hand, is very specific about where the python3 executable
must be installed:

    /usr/local/bin/python3

If you are having trouble running Automater due to a lack of python, you might
consider running:

    bash install_python3.sh

I included this script to look for python3 at the usual locations and make sure
it is symlinked to `/usr/local/bin/python3`. If it can't find the executable
anywhere, it may ask you to install the Xcode command line tools first. They
come with a version of python3 that tends to be a tad dated but sufficient
for the purpose at hand.

For something more modern, you might want to consider installing it through
a package manager such as [Homebrew](https://brew.sh) or
[MacPorts](https://www.macports.org).

### xxhash

When present, the xxhash Python package may improve scanning directories for
duplicate files. You can install it with:

    python3 -m pip install xxhash

(It is also required if you want to use the script's -e option. Enter
`python3 apfs_archive.py -h` for more details.)

## Revision History

Note: Starting with v1.2.2, new development will be done in separate git
branches that are periodically merged into the main one. That is when this
READ_ME will be updated and a new version number assigned. I aspire to make
these numbered releases stable builds.

1.2.2 (2026-03-13)

* added validate config option
* added --version command line option
* removed Automator app from git repository (see above)
* minor bug fixes and code refactoring ahead of adding new functionality

1.2.1 (2026-03-07)

* delete_orig config option to delete original after successful operation
* error processing one source path no longer terminates remainder of batch
* -d option should now work properly

1.2 (2026-03-07)

* added clone_files config that can be set false to disable cloning phase
* added -e option to estimate how much effect cloning phase would have
* minor fix to script that made it require Python 3.10 and not 3.7

1.1 (2026-03-06)

* Automator app added
* added a note in this README about installing xxhash

1.0 (2026-03-05)

* initial release
