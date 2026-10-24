"""
PDF reports (fpdf2). Noto fonts cover English, Malayalam, Hindi and Tamil; with `uharfbuzz`
installed the Indic scripts are shaped correctly.

Tables are drawn cell by cell (much faster than fpdf's table helper): a 2,000-row list takes a
few seconds. Text shaping stays on for the whole document: switching it on and off inside one
PDF mixes up glyphs of the embedded fonts.
"""

import contextlib
import io
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone

from . import appsettings, dates
from .i18n import ui

FONT_DIR = Path(settings.BASE_DIR) / "static" / "fonts"
FONTS = [
    ("pms", "NotoSans"),
    ("pmsml", "NotoSansMalayalam"),
    ("pmshi", "NotoSansDevanagari"),
    ("pmsta", "NotoSansTamil"),
]
# right-to-left letters, Arabic digits and direction marks: only text containing one of these needs the
# full bidirectional analysis; everything else (English and the Indic scripts) is one left-to-right run
NEEDS_BIDI = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff\u200e\u200f\u202a-\u202e\u2066-\u2069]")
LIGHT = (241, 246, 243)
GREY = (90, 100, 95)


@lru_cache(maxsize=1)
def _pdf_class():
    from fpdf import FPDF  # imported lazily: only needed when a PDF is requested
    from fpdf.enums import TextDirection
    from fpdf.errors import FPDFException

    try:
        import uharfbuzz  # noqa: F401

        can_shape = True
    except ImportError:  # Latin text is still fine without it
        can_shape = False

    class ReportPDF(FPDF):
        def __init__(self, title, subtitle="", orientation="P"):
            super().__init__(orientation=orientation, unit="mm", format="A4")
            self.report_title, self.report_subtitle = title, subtitle
            self._widths = {}
            self.app = appsettings.current()
            h = self.app.primary_color.lstrip("#")
            self.accent = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
            for family, file in FONTS:
                self.add_font(family, "", str(FONT_DIR / f"{file}-Regular.ttf"))
                self.add_font(family, "B", str(FONT_DIR / f"{file}-Bold.ttf"))
            self.set_fallback_fonts([f for f, _ in FONTS[1:]], exact_match=False)
            if can_shape:
                self.set_text_shaping(True)
            self.set_auto_page_break(True, margin=14)
            self.set_margins(10, 10, 10)
            self.alias_nb_pages()
            self.add_page()

        def cell(self, *args, **kwargs):
            # fpdf2 2.8 bug: after text drawn with a fallback font (Malayalam / Tamil ...) the next text
            # in the main font can be written with the fallback font's glyphs. Re-select it every time.
            key = self.font_family + self.font_style
            if key in self.fonts:
                self.current_font = self.fonts[key]
                self.current_font_is_set_on_page = False
            return super().cell(*args, **kwargs)

        def _preload_bidirectional_text(self, text, markdown):
            # Speed-up (about 2x for big tables): skip the bidi algorithm for left-to-right-only text,
            # where it always returns the whole text as one LTR fragment.
            if not self.text_shaping or self.text_shaping["direction"] or NEEDS_BIDI.search(text):
                return super()._preload_bidirectional_text(text, markdown)
            self.text_shaping["paragraph_direction"] = self.text_shaping["fragment_direction"] = TextDirection.LTR
            emphasis = ("B" in self.font_style, "I" in self.font_style, self.strikethrough, self.underline)
            return tuple(self._preload_font_styles(text, markdown, _initial_emphasis=emphasis))

        def get_string_width(self, s, normalized=False, markdown=False):
            # the same short texts (dates, names, statuses) come back again and again in a table
            if normalized or markdown:
                return super().get_string_width(s, normalized, markdown)
            key = (s, self.font_family, self.font_style, self.font_size_pt)
            width = self._widths.get(key)
            if width is None:
                width = self._widths[key] = super().get_string_width(s)
            return width

        def header(self):
            line = self.app.estate_name + (f"  ·  {self.report_subtitle}" if self.report_subtitle else "")
            if self.app.logo_type:
                with contextlib.suppress(Exception):  # a broken image must not stop the report
                    self.image(
                        io.BytesIO(bytes(self.app.logo)),
                        x=self.w - self.r_margin - 30,
                        y=7,
                        h=12,
                        keep_aspect_ratio=True,
                        w=30,
                    )
            self.set_font("pms", "B", 13)
            self.set_text_color(*self.accent)
            self.cell(0, 7, self.report_title, new_x="LMARGIN", new_y="NEXT")
            self.set_font("pms", "", 8.5)
            self.set_text_color(*GREY)
            self.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
            self.set_draw_color(*self.accent)
            self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
            self.ln(4)
            self.set_text_color(0, 0, 0)

        def footer(self):
            generated = f"{ui('Generated')}: {dates.fmt(timezone.now())}"
            self.set_y(-10)
            self.set_font("pms", "", 7.5)
            self.set_text_color(120, 120, 120)
            self.cell(0, 5, generated, align="L")
            self.set_x(self.l_margin)
            self.cell(0, 5, f"{self.page_no()} / {{nb}}", align="R")

        def section(self, title):
            if self.get_y() > self.h - 40:
                self.add_page()
            self.ln(2)
            self.set_font("pms", "B", 11)
            self.set_text_color(*self.accent)
            self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)

        def kpis(self, items):
            """Row of label / value boxes."""
            if not items:
                return
            width = (self.w - self.l_margin - self.r_margin) / len(items)
            y = self.get_y()
            for i, (label, value) in enumerate(items):
                x = self.l_margin + i * width
                self.set_fill_color(*LIGHT)
                self.rect(x + 1, y, width - 2, 15, style="F")
                self.set_xy(x + 3, y + 1.5)
                self.set_font("pms", "", 7.5)
                self.cell(width - 6, 4, str(label))
                self.set_xy(x + 3, y + 6.5)
                self.set_font("pms", "B", 11)
                self.cell(width - 6, 6, str(value))
            self.set_xy(self.l_margin, y + 18)

        # ------------------------------------------------------------------
        def _lines(self, text, width):
            """Text wrapped to a column width (most cells fit on one line: checked cheaply first)."""
            if not text or self.get_string_width(text) <= width:
                return [text]
            try:
                return self.multi_cell(width, 4, text, dry_run=True, output="LINES", padding=0) or [text]
            except FPDFException:  # column narrower than one character: cut instead
                return [text[: max(1, int(len(text) * width / self.get_string_width(text)))]]

        def _row(self, cells, widths, align, line_h, fill=None, bold=False, color=(0, 0, 0)):
            pad = 1
            self.set_font("pms", "B" if bold else "", self.font_size_pt)
            wrapped = [self._lines(t, w - 2 * pad) for t, w in zip(cells, widths, strict=True)]
            height = max(len(lines) for lines in wrapped) * line_h + 2 * pad
            if self.get_y() + height > self.page_break_trigger:
                self.add_page()
                if self._table_header:
                    self._row(*self._table_header)
            self.set_font("pms", "B" if bold else "", self.font_size_pt)
            self.set_text_color(*color)
            x, y = self.l_margin, self.get_y()
            if fill:
                self.set_fill_color(*fill)
                self.rect(x, y, sum(widths), height, style="F")
            for lines, w, a in zip(wrapped, widths, align, strict=True):
                for i, line in enumerate(lines):
                    self.set_xy(x + pad, y + pad + i * line_h)
                    self.cell(w - 2 * pad, line_h, line, align=a)
                x += w
            self.set_xy(self.l_margin, y + height)

        def data_table(self, headers, rows, numeric=None, totals=None, font_size=None):
            if not headers:
                return
            headers = [str(h) for h in headers]
            rows = [["" if v is None else str(v) for v in r] for r in rows]
            totals = ["" if v is None else str(v) for v in totals] if totals else None
            numeric = numeric or [False] * len(headers)
            size = font_size or (8.5 if len(headers) <= 6 else 7.5 if len(headers) <= 10 else 6.5)
            line_h = size * 0.45
            usable = self.w - self.l_margin - self.r_margin
            # width ~ longest text in the column (capped), scaled to the page
            sample = [headers, *rows[:200], *([totals] if totals else [])]
            lengths = [min(max(max(len(r[i]) for r in sample), 4), 38) for i in range(len(headers))]
            widths = [usable * n / sum(lengths) for n in lengths]
            cell_margin, self.c_margin = self.c_margin, 0  # cells are padded by _row
            align = ["R" if n else "L" for n in numeric]
            self.set_font("pms", "", size)
            self._table_header = None
            header_args = (headers, widths, ["L"] * len(headers), line_h, self.accent, True, (255, 255, 255))
            self._row(*header_args)
            self._table_header = header_args  # repeated at the top of every new page
            for i, r in enumerate(rows):
                self._row(r, widths, align, line_h, LIGHT if i % 2 else None)
            if totals:
                self.set_draw_color(*self.accent)
                self.line(self.l_margin, self.get_y(), self.l_margin + usable, self.get_y())
                self._row(totals, widths, align, line_h, None, True)
            self._table_header = None
            self.c_margin = cell_margin
            self.set_text_color(0, 0, 0)
            self.ln(3)

        def note(self, text):
            self.set_font("pms", "", 8)
            self.set_text_color(*GREY)
            self.set_x(self.l_margin)
            self.multi_cell(0, 4.5, text, new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)

        def response(self, filename):
            resp = HttpResponse(bytes(self.output()), content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="{filename}"'
            return resp

    return ReportPDF


def new_pdf(title, subtitle="", columns=0):
    """A4 report; landscape when the table is wide."""
    return _pdf_class()(title, subtitle, orientation="L" if columns > 7 else "P")
