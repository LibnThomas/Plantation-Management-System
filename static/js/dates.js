/*
 * Dates are shown and typed as DD/MM/YY everywhere (same rules as core/dates.py).
 * Every <input class="pms-date"> gets a calendar pop-up (flatpickr, bundled - works offline) the first
 * time it is focused; typing the date still works.
 */
(function () {
  "use strict";
  const pad = (n) => String(n).padStart(2, "0");

  function fullYear(yy) {
    const year = 2000 + yy;
    return year > new Date().getFullYear() + 20 ? year - 100 : year; // 15/06/65 = 1965
  }
  // text -> Date (or null): 13/10/26, 13/10/2026, 13-10-26, 13.10.2026, 2026-10-13
  function parse(text) {
    text = String(text || "").trim();
    let d, m, y, r = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (r) { y = +r[1]; m = +r[2]; d = +r[3]; }
    else {
      r = text.match(/^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$/);
      if (!r) return null;
      d = +r[1]; m = +r[2]; y = r[3].length === 2 ? fullYear(+r[3]) : +r[3];
    }
    const date = new Date(y, m - 1, d);
    return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? date : null;
  }
  const format = (date) => date ? `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${pad(date.getFullYear() % 100)}` : "";
  const iso = (date) => date ? `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` : "";

  window.pmsDate = {
    parse,
    format,
    toISO: (text) => iso(parse(text)),            // for comparing dates in scripts
    fromISO: (text) => format(parse(text)),       // 2026-10-13 -> 13/10/26
  };

  function attach(input) {
    if (input._flatpickr || !window.flatpickr) return;
    window.flatpickr(input, {
      dateFormat: "d/m/y",
      allowInput: true,
      disableMobile: true,                           // the phone's own picker would show another format
      locale: { firstDayOfWeek: 0 },                 // weeks start on Sunday
      parseDate: (text) => parse(text) || undefined,
      formatDate: (date) => format(date),
      onClose: (_, __, fp) => {                      // tidy what was typed: 1/2/2026 -> 01/02/26
        const date = parse(fp.input.value);
        if (date) fp.input.value = format(date);
      },
    });
  }
  document.addEventListener("focusin", (e) => {
    const el = e.target;
    if (el && el.matches && el.matches("input.pms-date")) {
      attach(el);
      if (el._flatpickr && !el._flatpickr.isOpen) el._flatpickr.open();
    }
  });
})();
