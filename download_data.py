"""Download Il-Kelma's Maltese language data from the project release.

The three data files are too big for the code repository, so they live in the
release "data-v1". Run once after installing:

    .venv\\Scripts\\python.exe download_data.py
"""
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "debattistacliff-commits/Il-Kelma"
TAG = "data-v1"
DATA = Path(__file__).resolve().parent / "data"
FILES = {  # file name -> sha256 of the zip
    "maltese_words.sqlite": None,
    "macocu_words.sqlite": None,
    "sentences.sqlite": None,
}
SUMS = Path(__file__).resolve().parent / "data_checksums.txt"


def checksums():
    sums = {}
    if SUMS.exists():
        for line in SUMS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, name = line.split(maxsplit=1)
                sums[name.strip()] = digest
    return sums


def progress(name):
    def hook(blocks, size, total):
        if total > 0:
            done = min(100, blocks * size * 100 // total)
            print(f"\r  {name}: {done}%", end="", flush=True)
    return hook


def main():
    DATA.mkdir(exist_ok=True)
    sums = checksums()
    for name in FILES:
        target = DATA / name
        if target.exists() and "--force" not in sys.argv:
            print(f"✓ {name} already there")
            continue
        url = f"https://github.com/{REPO}/releases/download/{TAG}/{name}.zip"
        zipped = DATA / f"{name}.zip"
        print(f"Downloading {name} …")
        urllib.request.urlretrieve(url, zipped, progress(name))
        print()
        expected = sums.get(f"{name}.zip")
        if expected:
            digest = hashlib.sha256(zipped.read_bytes()).hexdigest()
            if digest != expected:
                zipped.unlink()
                raise SystemExit(f"Checksum mismatch for {name}.zip — please try again.")
        with zipfile.ZipFile(zipped) as archive:
            archive.extract(name, DATA)
        zipped.unlink()
        print(f"✓ {name} ready")
    print("All Maltese data is ready.")


if __name__ == "__main__":
    main()
