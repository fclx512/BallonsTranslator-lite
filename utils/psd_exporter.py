"""Unified PSD export interface — strategy pattern.

One concrete strategy:
- ``PsJsxExporter``  — ExtendScript .jsx generation (``utils.psd_jsx_exporter``)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from .proj_imgtrans import ProjImgTrans


@dataclass
class ExportOptions:
    """User choices from the export dialog."""

    output_dir: str
    page_filter: Optional[List[str]] = None  # None = all pages


class AbstractPsdExporter(ABC):
    """Interface shared by both export strategies."""

    @abstractmethod
    def check_availability(self, passive: bool = False) -> Tuple[bool, str]:
        """Return ``(is_available, reason_string)``.

        Called on dialog open to:
        - Disable COM radio when PS isn't found.
        - Show a human-readable reason in the UI.

        Args:
            passive: If True, only check — never launch PS.
        """

    @abstractmethod
    def get_available_fonts(self) -> Set[str]:
        """Get font names available in the target environment.

        COM: enumerates PS fonts.
        ExtendScript: returns an empty set (can't know remote PS fonts).
        """

    @abstractmethod
    def export_page(
        self,
        proj: ProjImgTrans,
        page_name: str,
        options: ExportOptions,
    ) -> str:
        """Export a single page.  Returns the path to the created file."""

    @abstractmethod
    def cleanup(self):
        """Release resources (e.g. disconnect from PS COM)."""

    @staticmethod
    def collect_project_fonts(proj: ProjImgTrans) -> Set[str]:
        """Return the set of unique ``font_family`` strings across all pages."""
        families: Set[str] = set()
        for blk_list in proj.pages.values():
            for blk in blk_list:
                fam = blk.fontformat.font_family
                if fam:
                    families.add(fam)
        return families


def create_exporter() -> AbstractPsdExporter:
    """Factory — returns a PsJsxExporter."""
    from .psd_jsx_exporter import PsJsxExporter

    return PsJsxExporter()
