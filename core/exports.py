"""CSV downloads shared by all modules."""

import contextvars
import csv
from decimal import Decimal, InvalidOperation

from django.http import HttpResponse, StreamingHttpResponse

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe(value):
    """Stop spreadsheet apps from running text that looks like a formula (CSV injection)."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        try:
            Decimal(value)  # a negative number is fine
        except InvalidOperation:
            return "'" + value
    return value


class SafeWriter:
    def __init__(self, stream):
        self._w = csv.writer(stream)

    def writerow(self, row):
        self._w.writerow([_safe(v) for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def csv_response(filename):
    """(response, writer). Starts with a BOM so Excel opens UTF-8 (₹, Malayalam ...) correctly."""
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write("﻿")
    return resp, SafeWriter(resp)


class _Echo:
    def write(self, value):
        return value


def csv_stream(filename, header, rows):
    """A streamed CSV download: rows are written while they are read from the database, so a big
    export never sits in memory as a whole."""
    writer = csv.writer(_Echo())

    def lines():
        yield "\ufeff" + writer.writerow([_safe(v) for v in header])
        for row in rows:
            yield writer.writerow([_safe(v) for v in row])

    # the rows are read after the view has returned: keep this request's language and data set (demo)
    ctx = contextvars.copy_context()

    def in_request_context():
        gen = lines()
        while True:
            try:
                yield ctx.run(next, gen)
            except StopIteration:
                return

    resp = StreamingHttpResponse(in_request_context(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
