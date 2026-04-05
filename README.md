# apfs_archive 1.4

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

    python3 apfs_archive.py /path/to/foo/

and it produces a:

    /path/to/foo.dmg

(Note that if the latter already exists, you may get a `foo_2.dmg` instead.)

The source path could also be another dmg file. Let's say you created a
`bar.dmg` earlier, but want to see if you can make it smaller by cloning away
any file duplication within it? Running:

    python3 apfs_archive.py bar.dmg

should produce a `bar_2.dmg` file that has been run through the cloning phase.

That's it for basic usage, but there are numerous configuration and command
line options you can set up to use the script in various ways. For example,
you can expand a dmg back into a folder with `-x`. These are covered next.

### Command Line Options

#### `-d DIR_PATH` or `--dst-dir=DIR_PATH`

By default, the destination path is inferred from the source. For example, if
the source path were:

    /path/to/foo/

the destination might be:

    /path/to/foo.dmg

As you can see, the file goes into the same parent directory as the source,
and its name is also derived from the source.

But you can change the destination directory. With `-d /path/to/bar/`, you
would get:

    /path/to/bar/foo.dmg

instead.

Note that if a `foo.dmg` file already exists at the destination, the script may
go with `foo_2.dmg` (or even `foo_3.dmg` if `foo_2.dmg` is also taken, and so
on).

#### `-e` or `--estimate`

With this option, no dmg is created. Rather, the source directory is scanned to
estimate how much space may be saved just from eliminating file duplication
through cloning.

Since this option is meant to use only a quick single pass scan of the file
data, it requires the 128-bit hash function you get with the
[xxhash](https://xxhash.com) package. This can be installed with:

    python3 -m pip install xxhash

Note that the true amount of space saved will likely differ from the estimate
because:

* it only consider a file's data size
  * in actuality, there is additional file system overhead
* for dmg archiving:
  * it does not account for what the compression phase may do
* for `--clone-in-place` operations:
  * it can't tell if any of the files have already been cloned

#### `-C FILE_PATH.json` or `--config-file=FILE_PATH.json`

This sets the default configuration options to those loaded from the specified
JSON file. Technically, it installs your file at:

    ~/Library/Preferences/apfs_archive.json

Besides the `apfs_archive.py` script itself, if you build the Automator app,
it too will grab the default configuration parameters from there.

See the Configuration section further down for more info on what parameters
you can adjust.

#### `-c ARG` or `--config=ARG`

You can supply 1 or more `-c` options to override configuration defaults.
For example, if you wanted to delete the original after archiving in just this
once instance, you could write `-c delete_orig`.

In the Configuration section, there is a subsection that covers the short-hand
syntax you can use with this option.

#### `-p` or `--clone-in-place`

As with the `-e` option, no dmg is created with this option. Rather, the source
directory is scanned, and duplicate files found within it are replaced by
clones in the hopes of freeing up space.

As mentioned with `-e`, the actual amount of space you recover may depend on
whether any of the files had already been cloned.

#### `-x` or `--expand`

Rather than creating dmg archives, this option expands them back out into
folders at the destination. In other word, `foo.dmg` turns back into `foo/`.
The script can also expand tar and zip files.

Once expanded, the destination folder gets a clone-in-place pass (unless you
suppress this by setting the `clone_files` flag false).

If you try to expand a directory instead of an archive file, it will attempt
to clone everything in the source directory to the destination except for any
archive files it encounters. These will get expanded to the destination.

So that's the executive summary. Now onto the nitty gritty details.

* archive files are recognized by any of the following file extensions:
  * `.dmg`
  * `.sparseimage`
  * `.sparsebundle`
  * `.tar`
  * `.tar.gz` or `.tgz`
  * `.tar.bz` or `.tbz`
  * `.tar.bz2` or `.tbz2`
  * `.tar.xz` or `.txz`
  * `.tar.zst` or `.zst`
  * `.zip`
* archive files are scanned first before being expanded
  * consider a file named `foo.dmg` containing a volume called `foo`
    * this should expand into a `foo/` directory at the destination
  * now say the volume was called `bar` instead
    * this should then expand into a `foo/bar/` directory
    * so both the archive name and internal volume name are preserved
  * now say the volume is `foo` again but a `foo` directory already exists
    * in this case, the volume should expand into `foo_2/foo/`
  * tar and zip archives may not even contain a root directory
    * say `foo.zip` contained `bar.txt` and `baz.dat`
      * you would get a `foo/` directory containing those 2 files
  * the script also looks for Mac metadata while scanning tar and zip archives
    * if found:
      * the `usr/bin/tar` tool is used to expand tar files
      * the `usr/bin/ditto` tool is used to expand zip files
      * these should handle the metadata properly if run on a Mac
    * if not found, Python's standard library handles the expansion
* in the clone-in-place phase
  * say your image expanded into `foo_2` because `foo` already existed
    * both directories would then be scanned together for clone-in-place
    * the idea is that it's likely they duplicate each other a lot

#### `--version`

Simply prints a version string and quits.

### Configuration

Configuration options can either be loaded from a JSON file or set individually
on the `apfs_archive.py` command line using the `-c` switch. They include

| Key         | Value                                               | Default |
| :---------- | :-------------------------------------------------- | ------: |
| auto_expand | Automator expands files and archives directories    | true    |
| buf_size    | maximum bytes read from a file at a time            | 1048576 |
| clone_files | do actually scan for duplicate files and clone them | true    |
| delete_orig | delete original after successfully processing       | false   |
| dmg_format  | hdiutil format code to select .dmg compression type | "ULMO"  |
| validate    | run hdiutil verify on new dmgs                      | true    |
| verbosity   | affects how much info gets logged during script run | 2       |

Note that within a JSON file, the keys would need to be enclosed in "".

If auto_expand is set false, Automator will remake the dmgs instead to try to
clone the files within them (assuming clone_files is true).

The validate option is not strictly necessary, but may provide some peace of
mind when using it in conjunction with delete_orig, as the the deletion will
not take place until validation has completed successfully.

The verbosity levels work as follows:

0. stdout is effectively disabled, but stderr is still functional.
1. Logs a message for each general operation like archiving, expanding, etc.
2. More steps in terms of making temporary dmgs and such are logged.
3. Logging goes down to the individual file level for cloning, etc.

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
  * it has also been supported since the earliest versions of Mac OS X
* "ULFO" lzfse compression applied
  * essentially Apple's version of .zip
  * it reputedly has similar compression levels but runs faster
  * requires Mac OS X 10.11 (El Capitan) or later
* "ULMO" lzma compression applied (the default)
  * this is the tightest compression available
  * requires macOS 10.15 (Catalina) or later

#### -c / --config Arg Short-Hand

What you have seen seen above describes the rigid format needed in a JSON
config file. The example.json file shows what this would look like with all the
keys assigned default values.

With the `-c` option on the command line, you can shorten some of this syntax
for the sake of convenience. Take the following examples:

    -c '"dmg_format": "UDZO"'
    -c '"delete_orig": true'
    -c '"clone_files": false'

They show exactly how things need to appear in a config file. But on the
command line, you could shorten them to:

    -c fmt:zip
    -c del
    -c noclone

respectively.

To begin with, you need not supply the double-quotes around the keys. For
`dmg_format`, the value may also omit the double-quotes and be given in lower
case.

Aliases for config keys include:

| Key         | Aliases     |
| :---------- | :---------- |
| buf_size    | size        |
| clone_files | clone       |
| delete_orig | del, delete |
| dmg_format  | fmt, format |
| validate    | val         |
| verbosity   | verb, v     |

For boolean key values, you can even omit the `:true/false` part. By default,
it will be considered true, but if you prefix the key with `no` or `no_`, it
will be set false instead.

For the `dmg_format` key, there are also a number of aliases on the value side,
since it can be rather difficult to remember a code like `UDZO`.

| dmg_format Value | Aliases                    |
| :--------------- | :------------------------- |
| UDRO             | ro, read_only, uncmp       |
| UDZO             | gz, gzip, zip, maxcompat   |
| ULFO             | fast, fastcmp, lzfse       |
| ULMO             | 7z, 7zip, xz, lzma, maxcmp |

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

## Known Issues

* occasionally, the script tries to clone a file to itself
  * I have not identified the cause, but block the attempt and log a warning
* the direct dmg re-encoding sometimes fails on a error from `hdiutil`
  * it seems you can work around this by expanding to folder and re-archiving?
* the re-encoding can occasionally result in a *bigger* dmg
  * this may have something to do with extra file system overhead introduced?
  * this only tends to happen when cloning opportunities are meagre
  * at any rate, it's best to check if new dmg is really smaller than the old

## Revision History

Note: Starting with v1.2.2, new development will be done in separate git
branches that are periodically merged into the main one. That is when this
READ_ME will be updated and a new version number assigned. I aspire to make
these numbered releases stable builds.

1.4 (2026-03-21)

* `-x` option added to expand archives
* minor bug fixes

1.3.1 (2026-03-16)

* fixed a bug that prevented multiple `-c` args on the command line
  * made a better parser for such args (see above)

1.3 (2026-03-15)

* source path can now be a dmg file
  * in that case, it is remade after files inside have been cloned
* creates a foo_2.dmg if foo.dmg already exists
  * the script used to simply overwrite foo.dmg
* added -p (--clone-in-place) option to clone files without making dmgs
* added -x (--expand) option to expand dmg back into folder
  * runs clone-in-place on folder (unless disabled)
  * if expanding to foo_2, foo and foo_2 will get clone-in-place together
* auto_expand config defaults true
  * only relevant to Automator runs
  * archive folders to dmg, expand dmg files
  * if set false, dmg files are remade instead

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
