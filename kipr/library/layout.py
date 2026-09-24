"""Where a KiCad library repository keeps its parts.

The defaults are PantsForBirds/kicad-libs' layout::

    lib_fp/<Library>.pretty/<Footprint>.kicad_mod
    lib_sch/<Library>.kicad_sym
    lib_3d/<Library>/<model>.step            referenced as ${KICAD_LIBS_DIR}/lib_3d/...
    datasheets/<part>.pdf

Every directory is repo-relative; ``""`` or ``"."`` means the repository root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_FP = "lib_fp"
DEFAULT_SYM = "lib_sch"
DEFAULT_3D = "lib_3d"
DEFAULT_DATASHEETS = "datasheets"


def _norm(d: str | None) -> str:
    d = (d or "").replace("\\", "/").strip("/")
    if d in ("", "."):
        return ""
    if d.startswith("../") or "/../" in f"/{d}/":
        raise ValueError(f"library directory must stay inside the repository: {d!r}")
    return d


@dataclass(frozen=True)
class Layout:
    fp: str = DEFAULT_FP
    sym: str = DEFAULT_SYM
    models: str = DEFAULT_3D
    datasheets: str = DEFAULT_DATASHEETS

    def __post_init__(self):
        for k in ("fp", "sym", "models", "datasheets"):
            object.__setattr__(self, k, _norm(getattr(self, k)))

    @staticmethod
    def _prefix(d: str) -> str:
        return re.escape(d + "/") if d else ""

    @property
    def fp_re(self) -> re.Pattern:
        return re.compile(rf"^{self._prefix(self.fp)}(?P<lib>[^/]+)\.pretty/(?:.*/)?(?P<name>[^/]+)\.kicad_mod$")

    @property
    def sym_re(self) -> re.Pattern:
        return re.compile(rf"^{self._prefix(self.sym)}(?P<lib>[^/]+)\.kicad_sym$")

    def is_model(self, path: str) -> bool:
        if self.models:
            return path.startswith(self.models + "/")
        return path.lower().endswith((".step", ".stp", ".wrl"))

    @staticmethod
    def pathspec(d: str) -> str:
        """``git`` pathspec for a directory ('.' for the root)."""
        return d or "."

    def diff_paths(self) -> list[str]:
        return [self.pathspec(d) for d in (self.fp, self.sym, self.models)]


def add_arguments(ap, models_only: bool = False) -> None:
    """``--lib-fp/--lib-sch/--lib-3d/--datasheets`` options (``models_only``: just ``--lib-3d``)."""
    if not models_only:
        ap.add_argument("--lib-fp", default=DEFAULT_FP, help=f"footprint libraries dir (default {DEFAULT_FP})")
        ap.add_argument("--lib-sch", default=DEFAULT_SYM, help=f"symbol libraries dir (default {DEFAULT_SYM})")
    ap.add_argument("--lib-3d", default=DEFAULT_3D, help=f"3D models dir (default {DEFAULT_3D})")
    if not models_only:
        ap.add_argument("--datasheets", default=DEFAULT_DATASHEETS,
                        help=f"datasheet PDFs dir (default {DEFAULT_DATASHEETS})")


def from_args(args) -> Layout:
    return Layout(fp=getattr(args, "lib_fp", DEFAULT_FP), sym=getattr(args, "lib_sch", DEFAULT_SYM),
                  models=getattr(args, "lib_3d", DEFAULT_3D),
                  datasheets=getattr(args, "datasheets", DEFAULT_DATASHEETS))
