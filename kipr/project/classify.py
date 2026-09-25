"""One rule for "did this component really change?", shared by the schematic symbol diff, the
footprint diff, the BOM, the 3D component list, the summary counts, the report and the PR comment.

A difference is one of three things:

- **noise**: not reported at all. Fields that appear or disappear empty, `Sim.*` fields (added by
  KiCad upgrades), `ki_*` keywords, whitespace-only edits, field order, text position, visibility
  and font (the parsers keep field values only), uuids.
- **minor**: listed, but collapsed, not counted as changed and never highlighted. Every field that
  is not significant (cost, description, datasheet URL, notes, generator tags, ...), the
  `exclude_from_sim` flag, a 3D model path that only swaps the file format, a footprint library
  nickname rename of an identical footprint, a library symbol's cached graphics.
- **significant**: value, footprint, DNP / exclude_from_bom / exclude_from_board and the fields
  that name the part to buy (MPN, manufacturer, LCSC / Digi-Key / Mouser / ... part numbers).
  Part-number fields compare case-insensitively.

Which fields are significant is configurable: `kipr project --significant-fields PATTERNS`
(comma-separated, case-insensitive globs matched against the field name with spaces, `-`, `_` and
`.` removed; a leading `+` adds to the defaults instead of replacing them).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

DEFAULT_SIGNIFICANT_FIELDS = (
    "mpn", "*mpn", "mpn*", "manufacturer", "manufacturer*", "mfr", "mfr*", "mfg", "mfg*",
    "partnumber", "*partnumber", "pn", "*pn", "*partno", "*partnum",
    "lcsc*", "jlc*", "digikey*", "mouser*", "farnell*", "newark*", "arrow*", "tme*", "rs*pn", "octopart*",
    "supplier*", "vendor*", "sku", "*sku",
)
NOISE_FIELD_PATTERNS = ("sim.*", "ki_*")
# what names of the diffs that never count as a real change
MINOR_WHATS = {"model_format", "footprint_library", "fields_minor", "exclude_from_sim", "library", "lib_id"}


def norm_key(name: str) -> str:
    return re.sub(r"[\s_.\-/]+", "", name).lower()


def norm_value(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


@dataclass
class Config:
    significant: tuple[str, ...] = DEFAULT_SIGNIFICANT_FIELDS
    noise: tuple[str, ...] = NOISE_FIELD_PATTERNS
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_option(cls, value: str | None) -> "Config":
        """`--significant-fields` value: "a,b*" replaces the defaults, "+a,b" adds to them."""
        if not value:
            return cls()
        extend = value.startswith("+")
        pats = tuple(norm_key(p) if "*" not in p else p.replace(" ", "").replace("_", "").replace("-", "").lower()
                     for p in value.lstrip("+").split(",") if p.strip())
        return cls(significant=(DEFAULT_SIGNIFICANT_FIELDS + pats) if extend else pats)

    def is_noise_field(self, name: str) -> bool:
        low = name.lower()
        return any(fnmatch.fnmatchcase(low, p) for p in self.noise)

    def is_significant_field(self, name: str) -> bool:
        if name not in self._cache:
            k = norm_key(name)
            self._cache[name] = any(fnmatch.fnmatchcase(k, p) for p in self.significant)
        return self._cache[name]


DEFAULT = Config()
_active = DEFAULT


def active() -> Config:
    """The configuration of the running review (set by kipr.project.review.run)."""
    return _active


def set_active(cfg: Config | None) -> Config:
    """Make `cfg` the configuration the diffs use; returns the previous one (to restore)."""
    global _active
    prev, _active = _active, cfg or DEFAULT
    return prev


@dataclass
class FieldDiff:
    significant: list[str] = field(default_factory=list)  # human-readable lines
    minor: list[str] = field(default_factory=list)
    significant_keys: list[str] = field(default_factory=list)
    minor_keys: list[str] = field(default_factory=list)

    def whats(self) -> list[str]:
        return (["fields"] if self.significant else []) + (["fields_minor"] if self.minor else [])


def field_diff(a: dict, b: dict, cfg: Config | None = None) -> FieldDiff:
    """Classified differences between two {field name: value} dicts (see the module doc)."""
    cfg = cfg or _active
    out = FieldDiff()
    for k in sorted(set(a) | set(b)):
        if cfg.is_noise_field(k):
            continue
        va, vb = norm_value(a.get(k)), norm_value(b.get(k))
        sig = cfg.is_significant_field(k)
        if (va.lower() == vb.lower()) if sig else (va == vb):
            continue  # also: appears / disappears empty, whitespace-only edits
        if not va:
            line = f"{k} added: {vb!r}"
        elif not vb:
            line = f"{k} removed (was {va!r})"
        else:
            line = f"{k} {va!r} -> {vb!r}"
        if sig:
            out.significant.append(line)
            out.significant_keys.append(k)
        else:
            out.minor.append(line)
            out.minor_keys.append(k)
    return out


def is_minor(whats) -> bool:
    """True when every difference named in `whats` is minor (and there is at least one)."""
    whats = list(whats)
    return bool(whats) and all(w in MINOR_WHATS for w in whats)
