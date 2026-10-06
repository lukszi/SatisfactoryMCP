"""The Real-ESRGAN binary: downloaded once into the user cache, verified, smoke-tested, run."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ENHANCE_EXE_NAME",
    "ENHANCE_MODEL",
    "ENHANCE_OVERLAP_PX",
    "ENHANCE_SCALE",
    "ENHANCE_SHA256",
    "ENHANCE_TILE_PX",
    "ENHANCE_URL",
    "EnhanceError",
    "Upscaler",
    "check_array_stack",
    "ensure_upscaler",
    "ready_upscaler",
    "run_upscaler",
    "sha256_of",
    "upscaler_cache_dir",
]

#: One immutable GitHub release asset, and the digest of the bytes this file was written
#: against. A download that hashes to anything else is not unzipped and not run.
ENHANCE_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
    "realesrgan-ncnn-vulkan-20220424-windows.zip"
)
ENHANCE_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
ENHANCE_EXE_NAME = "realesrgan-ncnn-vulkan.exe"

#: The anime-tuned model of the five the archive ships: this artwork is flat colour and
#: drawn linework, and x4plus keeps photographic texture the map does not have.
ENHANCE_MODEL = "realesrgan-x4plus-anime"
ENHANCE_SCALE = 4

#: Source-side tiling, which is mandatory rather than an optimisation: handed the whole
#: 32768 px output the binary segfaults on a signed 32-bit index into its output buffer.
#: The overlap is context the model sees and the crop throws away, and 96 rather than 64
#: because 64 left one seam of eight reading 2.4x its own in-tile control.
ENHANCE_TILE_PX = 1024
ENHANCE_OVERLAP_PX = 96


class EnhanceError(RuntimeError):
    """The GPU enhancement stage cannot run or did not finish, and says what to do about it.

    Raised rather than degraded from: giving back Lanczos levels would write a sidecar
    that says ``enhanced`` over pixels that are not.
    """


@dataclass(frozen=True)
class Upscaler:
    """The verified binary: where it and its models are, and the release it came from."""

    exe: Path
    models: Path
    home: Path
    archive: Path
    url: str
    sha256: str


def upscaler_cache_dir() -> Path:
    """Where the GPU binary lives: the machine's cache, never this repository.

    Built the way ``src/satisfactory_mcp/config.py`` builds ``cache_dir``, with ``bin/``
    beneath it, because it is a property of this machine's GPU rather than of this clone
    and N worktrees should share one copy. ``prune_cache`` next door only ever deletes
    ``save-*.pkl``, so nothing sweeps this away behind the reader's back.
    """
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("satisfactory-mcp", appauthor=False)) / "bin"


def sha256_of(path: Path) -> str:
    """The digest of a file, read in chunks -- the archive is 45 MB."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_upscaler(exe: Path, models: Path, src: Path, dst: Path, scale: int) -> tuple[int, str]:
    """One invocation of the ncnn binary. ``src``/``dst`` are both files or both directories.

    ``-m`` is passed explicitly: the binary finds ``models/`` beside itself only by
    resolving its own path, which breaks through a symlink or a copied exe.
    """
    proc = subprocess.run(
        [
            str(exe),
            "-i",
            str(src),
            "-o",
            str(dst),
            "-m",
            str(models),
            "-n",
            ENHANCE_MODEL,
            "-s",
            str(scale),
            "-f",
            "png",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stderr or proc.stdout or "")[-2000:]


def ensure_upscaler(cache: Path | None = None, *, smoke: bool = True) -> Upscaler:
    """Download, verify and unpack the upscaler once, and prove the GPU will run it.

    The digest is checked before the archive is opened: an executable is not unpacked on
    the strength of having arrived. The smoke test is the only cheap way to tell "no GPU"
    from "the map broke it" -- the 12 KB sample the archive ships, about a second against a
    run of several minutes.
    """
    cache = Path(cache) if cache is not None else upscaler_cache_dir()
    stem = ENHANCE_URL.rsplit("/", 1)[-1][: -len(".zip")]
    archive = cache / f"{stem}.zip"
    home = cache / stem
    exe = home / ENHANCE_EXE_NAME
    models = home / "models"

    cache.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        print(f"downloading the upscaler once to {archive}")
        partial = archive.with_suffix(".zip.part")
        try:
            with (
                urllib.request.urlopen(ENHANCE_URL, timeout=180) as response,
                partial.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise EnhanceError(
                f"could not download the upscaler from {ENHANCE_URL}: {exc}\n"
                f"Fetch it by hand into {archive} and run this again -- the download is the "
                "only part of --enhance that needs the network, and it happens once."
            ) from exc
        partial.replace(archive)

    digest = sha256_of(archive)
    if digest != ENHANCE_SHA256:
        raise EnhanceError(
            f"{archive} hashes to {digest}, not the pinned {ENHANCE_SHA256}.\n"
            "That asset is an immutable GitHub release, so a different digest means the "
            "download was truncated or intercepted, not that upstream changed its mind. "
            f"Delete it and run this again; if it keeps happening, this file's pin is what "
            "to look at rather than the check."
        )

    if not exe.is_file():
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(home)
    if not exe.is_file() or not models.is_dir():
        raise EnhanceError(
            f"{archive} unpacked into {home} without {ENHANCE_EXE_NAME} or models/ in it. "
            "Delete both and run this again."
        )
    if not (models / f"{ENHANCE_MODEL}.param").is_file():
        raise EnhanceError(
            f"{models} has no {ENHANCE_MODEL}.param -- this archive does not ship the model "
            "this file was measured against, so the run would be a different pipeline."
        )

    if smoke:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "smoke.png"
            code, log = run_upscaler(exe, models, home / "input.jpg", probe, ENHANCE_SCALE)
            if code != 0 or not probe.is_file():
                raise EnhanceError(
                    f"{exe} could not upscale its own 12 KB sample image (exit {code}), so "
                    "the GPU stage will not run on this machine.\n"
                    f"{log}\n"
                    "This binary needs a Vulkan 1.1 device and its driver -- on a laptop, "
                    "check that it is not being handed the integrated GPU. Re-run without "
                    "--enhance for the plain z0..z5 pyramid; nothing else about this tool "
                    "needs a GPU."
                )

    return Upscaler(exe, models, home, archive, ENHANCE_URL, digest)


def check_array_stack() -> tuple[str, str]:
    """numpy and scipy, and the check that they came out of the same environment.

    A scipy compiled against a different numpy than the one that wins the import fails as a
    segfault or a wrong answer rather than an ImportError, which is cheaper to check than
    to debug.
    """
    try:
        import numpy
        import scipy
    except ImportError as exc:
        raise EnhanceError(
            "--enhance needs numpy and scipy, which are dependencies of this project "
            "outright: run this through `uv run` rather than a bare python."
        ) from exc
    files = [module.__file__ for module in (numpy, scipy)]
    homes = {Path(file).resolve().parents[1] for file in files if file is not None}
    if len(homes) != 1:
        raise EnhanceError(
            "numpy and scipy are imported from different environments -- "
            + " and ".join(sorted(str(home) for home in homes))
            + ".\nThat happens when something ahead of the project environment on sys.path "
            "carries its own numpy: it wins the import and scipy is left compiled against "
            "another one. Run this as `uv run --extra gen python tools/gen_map_image.py`, "
            "out of one environment."
        )
    return numpy.__version__, scipy.__version__


def ready_upscaler(cache: Path | None = None) -> Upscaler:
    """``ensure_upscaler`` and ``check_array_stack``: the GPU stage proved before any decode."""
    upscaler = ensure_upscaler(cache)
    numpy_version, scipy_version = check_array_stack()
    print(
        f"  upscaler: {upscaler.exe} ({ENHANCE_MODEL}, sha256 {upscaler.sha256[:16]}...), "
        f"numpy {numpy_version} / scipy {scipy_version}"
    )
    return upscaler
