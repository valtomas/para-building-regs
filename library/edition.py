"""Keeps the published edition's date on a rebuilt library that holds nothing new.

Every release and every Monday the library is built again and stamped with the day's date. Most days nothing in
the documents has changed, and a new date alone made installed apps offer "a newer regulations library" that was
the same one. This compares the new library with the published one, words and images, and if they match gives the
new one the published edition and build time, so no app sees an update.

python3 library/edition.py library/dist/regs.sqlite previous/regs.sqlite
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from para.store import Store  # noqa: E402


def keep(new_path, old_path):
    """True if the new library was given the published one's edition (they hold the same). The files beside it
    (regs.sqlite.gz, library.json) are written again either way, so they always describe the library there."""
    old = Store(old_path, "r", readonly=True)
    old_meta, old_hashes = old.meta(), old.content_hashes()
    old.close()
    new = Store(new_path, "r")
    hashes = new.content_hashes()
    same = hashes == old_hashes and bool(old_meta.get("version"))
    if same:
        new.set_meta(version=old_meta["version"], built=old_meta.get("built", ""))
    new.set_meta(content_hash=hashes[0], images_hash=hashes[1])
    version = new.meta()["version"]
    new.close()
    import build  # here, not at the top: it needs the build's libraries, which reading two libraries doesn't
    build.write_release(new_path, version, *hashes)
    return same


if __name__ == "__main__":
    kept = keep(sys.argv[1], sys.argv[2])
    print("Nothing differs from the published library: it keeps that edition." if kept
          else "The library differs from the published one: it is a new edition.")
