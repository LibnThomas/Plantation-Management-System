/*
 * Search, column filters and sorting for the rows already on the page - done in the browser,
 * no request to the server. Added automatically to every <table class="table-data"> and to any
 * element with data-client-filter (its children with data-row are filtered).
 *
 *  - one search box: every word must appear somewhere in the row (any column), case-insensitive;
 *    numbers match with or without thousands separators (12,345 = 12345)
 *  - "Column filters": one box per column (text contained, or >100 / <100 / =100 for numbers)
 *  - click a column heading to sort (numbers, dates DD/MM/YY and text), click again to reverse
 *  - while filtering, a "Shown rows" total line adds up the numeric columns of the visible rows
 */
(function () {
  "use strict";
  const T = Object.assign({
    search: "Search these rows…", columns: "Column filters", shown: "{n} of {total} rows",
    subtotal: "Shown rows", clear: "Clear", none: "No rows match.", sort: "Sort",
    hideCols: "Columns", showAll: "Show all columns", hiddenN: "{n} hidden",
  }, JSON.parse((document.getElementById("table-tools-text") || {}).textContent || "{}"));
  const MIN_ROWS = 4; // small tables don't need a search box
  const MANY_COLUMNS = 6; // ... but a wide one still gets "show / hide columns"

  const fill = (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => (k in v ? v[k] : ""));
  const norm = (s) => (s || "").toLocaleLowerCase().replace(/\s+/g, " ").trim();
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  function cellText(cell) {
    // form fields inside a cell (e.g. the labels table) count with their current value
    let text = cell.textContent;
    cell.querySelectorAll("input, textarea, select").forEach((f) => {
      text += " " + (f.tagName === "SELECT" ? (f.selectedOptions[0] || {}).textContent || "" : f.value);
    });
    return text;
  }
  function toNumber(text) {
    const s = (text || "").replace(/[^\d.\-]/g, "");
    if (!s || s === "-" || s === ".") return null;
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : null;
  }
  function toDate(text) {
    const m = (text || "").trim().match(/^(\d{2})[/\-](\d{2})[/\-](\d{4}|\d{2})\b/);
    if (!m) return null;
    if (window.pmsDate) { const d = window.pmsDate.parse(`${m[1]}/${m[2]}/${m[3]}`); return d ? d.getTime() : null; }
    const y = m[3].length === 2 ? 2000 + +m[3] : +m[3];
    return new Date(y, +m[2] - 1, +m[1]).getTime();
  }
  function decimals(values) {
    return values.some((v) => Math.round(v * 100) % 100 !== 0) ? 2 : 0;
  }
  const fmt = (n, places) => n.toLocaleString("en-IN", { minimumFractionDigits: places, maximumFractionDigits: places });

  // toolbar elements are built with the DOM (texts never go through innerHTML: labels can be edited)
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    });
    children.forEach((c) => node.append(c));
    return node;
  }
  function searchBox() {
    const input = el("input", { type: "search", class: "form-control", placeholder: T.search, "aria-label": T.search });
    const group = el("div", { class: "input-group input-group-sm table-tools-search" },
      el("span", { class: "input-group-text" }, el("i", { class: "bi bi-search" })), input);
    return { group, input };
  }

  function columnMatch(filter, text, number) {
    const f = filter.trim();
    if (!f) return true;
    const cmp = f.match(/^(>=|<=|>|<|=)\s*(-?[\d,.]+)$/);
    if (cmp && number !== null) {
      const v = parseFloat(cmp[2].replace(/,/g, ""));
      return { ">": number > v, "<": number < v, ">=": number >= v, "<=": number <= v, "=": number === v }[cmp[1]];
    }
    const t = text, q = norm(f); // text is already normalised
    return t.includes(q) || t.replace(/,/g, "").includes(q.replace(/,/g, ""));
  }

  // ------------------------------------------------------------------ show / hide columns
  // Remembered in this browser for this page (per table); only the screen and printing change,
  // the CSV / PDF downloads keep every column.
  let tableNo = 0;
  function store(key, value) {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem(key) || "[]");
      if (value.length) localStorage.setItem(key, JSON.stringify(value)); else localStorage.removeItem(key);
    } catch (e) { /* storage off: just for this visit */ }
    return [];
  }
  function columnPicker(table, headers, skip) {
    table.id = table.id || "tt-table-" + (++tableNo);
    const labels = headers.map((th) => th.textContent.trim().replace(/\s+/g, " "));
    const key = "pms-cols:" + location.pathname + ":" + labels.join("|");
    const hidden = new Set(store(key).filter((l) => labels.includes(l)));
    const btn = el("button", { type: "button", class: "btn btn-sm btn-outline-secondary dropdown-toggle",
      "data-bs-toggle": "dropdown", "data-bs-auto-close": "outside", "aria-expanded": "false" },
    el("i", { class: "bi bi-layout-three-columns" }), " " + T.hideCols);
    const list = el("div", { class: "dropdown-menu p-2 table-tools-cols" });
    const boxes = [];
    labels.forEach((label, i) => {
      if (skip[i] || !label) return;  // e.g. the Actions column
      const box = el("input", { type: "checkbox", class: "form-check-input", id: `${table.id}-col-${i}` });
      box.checked = !hidden.has(label);
      box.addEventListener("change", () => {
        if (box.checked) hidden.delete(label); else hidden.add(label);
        if (hidden.size >= boxes.length) { hidden.delete(label); box.checked = true; }  // keep one column
        apply();
      });
      boxes.push(box);
      list.append(el("div", { class: "form-check" }, box,
        el("label", { class: "form-check-label", for: box.id, text: label })));
    });
    const showAll = el("button", { type: "button", class: "btn btn-sm btn-link px-0", text: T.showAll });
    showAll.addEventListener("click", () => { hidden.clear(); boxes.forEach((b) => (b.checked = true)); apply(); });
    list.append(el("hr", { class: "my-1" }), showAll);
    const badge = el("span", { class: "badge text-bg-secondary ms-1" });

    function apply() {
      // walk every row by column position, so cells spanning several columns ("no rows", totals) shrink too
      const off = labels.map((l) => hidden.has(l));
      for (const tr of table.rows) {
        let pos = 0;
        for (const cell of tr.cells) {
          if (!cell.dataset.span) cell.dataset.span = cell.colSpan || 1;
          const span = +cell.dataset.span;
          let shown = 0;
          for (let c = pos; c < pos + span; c++) if (!off[c]) shown++;
          cell.classList.toggle("tt-hide", shown === 0);
          if (span > 1 && shown) cell.colSpan = shown;
          pos += span;
        }
      }
      badge.textContent = hidden.size ? fill(T.hiddenN, { n: hidden.size }) : "";
      badge.hidden = !hidden.size;
      store(key, [...hidden]);
    }
    btn.append(badge);
    apply();
    const menu = el("div", { class: "dropdown" }, btn, list);
    if (boxes.length < 2) menu.hidden = true;
    return { menu, apply };
  }

  // ------------------------------------------------------------------ tables
  function enhanceTable(table) {
    const body = table.tBodies[0];
    const head = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
    if (!body || !head || table.dataset.tableTools === "off") return;
    const rows = Array.from(body.rows).filter((r) => !(r.cells.length === 1 && r.cells[0].colSpan > 1));
    const headers = Array.from(head.cells);
    if (rows.length < MIN_ROWS && headers.length < MANY_COLUMNS) return;  // small table: nothing to add
    const skip = headers.map((th) => th.classList.contains("no-print") || th.hasAttribute("data-no-filter"));
    const numeric = headers.map((th, i) => th.classList.contains("num") ||
      (rows[0] && rows[0].cells[i] && rows[0].cells[i].classList.contains("num")));
    const data = rows.map((r, idx) => {
      const texts = Array.from(r.cells).map(cellText);
      const all = norm(texts.filter((_, i) => !skip[i]).join(" "));
      return { row: r, idx, texts, low: texts.map(norm), all, plain: all.replace(/,/g, ""), nums: texts.map(toNumber) };
    });

    // toolbar
    const { group, input: search } = searchBox();
    const columnsBtn = el("button", { type: "button", class: "btn btn-sm btn-outline-secondary" },
      el("i", { class: "bi bi-funnel" }), " " + T.columns);
    const clearBtn = el("button", { type: "button", class: "btn btn-sm btn-link d-none", text: T.clear });
    const count = el("span", { class: "small text-muted ms-auto" });
    const cols = columnPicker(table, headers, skip);
    const bar = el("div", { class: "table-tools d-flex flex-wrap align-items-center gap-2 px-2 py-2 no-print" },
      group, columnsBtn, cols.menu, clearBtn, count);
    const holder = table.closest(".table-responsive") || table;
    holder.parentNode.insertBefore(bar, holder);

    // column filter row (created on first use)
    let filterRow = null;
    const colFilters = headers.map(() => "");
    function toggleColumns() {
      if (filterRow) {
        filterRow.classList.toggle("d-none");
        if (filterRow.classList.contains("d-none")) { filterRow.querySelectorAll("input").forEach((i) => (i.value = "")); colFilters.fill(""); apply(); }
        return;
      }
      filterRow = table.tHead.insertRow();
      filterRow.className = "table-tools-filters no-print";
      headers.forEach((th, i) => {
        const cell = document.createElement("th");
        filterRow.appendChild(cell);
        if (skip[i]) return;
        const inp = document.createElement("input");
        inp.type = "search";
        inp.className = "form-control form-control-sm";
        inp.placeholder = numeric[i] ? ">0" : "…";
        inp.setAttribute("aria-label", `${T.columns}: ${th.textContent.trim()}`);
        inp.addEventListener("input", debounce(() => { colFilters[i] = inp.value; apply(); }, 150));
        cell.appendChild(inp);
      });
      cols.apply();
      filterRow.querySelector("input")?.focus();
    }

    // totals of the visible rows
    let subtotalRow = null;
    function subtotal(visible) {
      const sums = headers.map((_, i) => (numeric[i] ? visible.map((d) => d.nums[i]).filter((v) => v !== null) : null));
      if (!sums.some((s) => s && s.length)) return;
      if (!subtotalRow) {
        const foot = table.tFoot || table.createTFoot();
        subtotalRow = foot.insertRow(0);
        subtotalRow.className = "table-tools-subtotal";
      }
      setTimeout(cols.apply);
      subtotalRow.innerHTML = "";
      headers.forEach((_, i) => {
        const td = subtotalRow.insertCell();
        if (i === 0) td.textContent = T.subtotal;
        if (sums[i] && sums[i].length) {
          td.className = "num";
          td.textContent = fmt(sums[i].reduce((a, b) => a + b, 0), decimals(sums[i]));
        }
      });
    }

    let noneRow = null;
    function apply() {
      const words = norm(search.value).split(" ").filter(Boolean);
      const active = words.length || colFilters.some((f) => f.trim());
      const visible = [];
      for (const d of data) {
        const ok = words.every((w) => d.all.includes(w) || d.plain.includes(w.replace(/,/g, ""))) &&
          colFilters.every((f, i) => !f || columnMatch(f, d.low[i], d.nums[i]));
        d.row.hidden = !ok;
        if (ok) visible.push(d);
      }
      count.textContent = active ? fill(T.shown, { n: visible.length, total: data.length }) : "";
      clearBtn.classList.toggle("d-none", !active);
      if (active) subtotal(visible);
      if (subtotalRow) subtotalRow.hidden = !active;
      if (!visible.length && !noneRow) {
        noneRow = body.insertRow();
        const td = noneRow.insertCell();
        td.colSpan = headers.length;
        td.className = "text-center text-muted py-3";
        td.textContent = T.none;
        cols.apply();
      }
      if (noneRow) noneRow.hidden = visible.length > 0;
    }

    search.addEventListener("input", debounce(apply, 120));
    columnsBtn.addEventListener("click", toggleColumns);
    clearBtn.addEventListener("click", () => {
      search.value = "";
      colFilters.fill("");
      if (filterRow) filterRow.querySelectorAll("input").forEach((i) => (i.value = ""));
      apply();
      search.focus();
    });

    // sorting: click a heading (headings that contain links keep their own behaviour)
    let sortCol = -1, sortDir = 1;
    const sortKeys = [];
    const sortKey = (d, i) => {
      const t = d.texts[i].trim();
      const date = toDate(t);
      if (date !== null) return date;
      return numeric[i] ? (d.nums[i] ?? -Infinity) : norm(t);
    };
    headers.forEach((th, i) => {
      if (skip[i] || th.querySelector("a, button, input")) return;
      th.classList.add("sortable");
      th.tabIndex = 0;
      th.title = T.sort;
      const go = () => {
        sortDir = sortCol === i ? -sortDir : 1;
        sortCol = i;
        headers.forEach((h) => h.removeAttribute("aria-sort"));
        th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending");
        if (!sortKeys[i]) sortKeys[i] = data.map((d) => sortKey(d, i)); // worked out once per column
        const keys = sortKeys[i];
        const sorted = [...data].sort((a, b) => {
          const x = keys[a.idx], y = keys[b.idx];
          return (x < y ? -1 : x > y ? 1 : a.idx - b.idx) * sortDir;
        });
        const frag = document.createDocumentFragment(); // one re-layout instead of one per row
        sorted.forEach((d) => frag.appendChild(d.row));
        if (noneRow) frag.appendChild(noneRow);
        body.appendChild(frag);
      };
      th.addEventListener("click", go);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }

  // ------------------------------------------------------------------ other lists (cards / rows)
  function enhanceList(box) {
    const items = Array.from(box.querySelectorAll("[data-row]"));
    if (items.length < MIN_ROWS) return;
    const { group, input } = searchBox();
    const count = el("span", { class: "small text-muted ms-auto" });
    const bar = el("div", { class: "table-tools d-flex align-items-center gap-2 mb-2 no-print" }, group, count);
    box.parentNode.insertBefore(bar, box);
    const texts = items.map((el) => norm(el.textContent));
    input.addEventListener("input", debounce(() => {
      const words = norm(input.value).split(" ").filter(Boolean);
      let n = 0;
      items.forEach((el, i) => { const ok = words.every((w) => texts[i].includes(w)); el.hidden = !ok; n += ok; });
      count.textContent = words.length ? fill(T.shown, { n, total: items.length }) : "";
    }, 120));
  }

  function init(root) {
    root.querySelectorAll("table.table-data").forEach((t) => { if (!t.dataset.tt) { t.dataset.tt = "1"; enhanceTable(t); } });
    root.querySelectorAll("[data-client-filter]").forEach((b) => { if (!b.dataset.tt) { b.dataset.tt = "1"; enhanceList(b); } });
  }
  window.pmsTableTools = init;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => init(document));
  else init(document);
})();
