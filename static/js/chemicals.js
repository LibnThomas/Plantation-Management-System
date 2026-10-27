/* Fertilizer & field work page: the chemicals of the mix ("Mix Ratio").
 * - Saved mixes: fill the page with a mix used before (same chemicals, amounts, jar / batch, rounds)
 * - Search: tap to add one or more fertilizers / chemicals
 * - Mixed in water: each amount is for one jar (from the chemical's dosage, e.g. 30 ml per 5 L -> 3 L for a
 *   500 L jar); change the jar capacity and every amount follows.
 *   Dry mix (no water): each amount is for one batch (one bag by default); the finished mix is given per plant
 *   and can be packed into packets for later.
 * - Quantity used = amount x rounds mixed; the purchase gives the price.
 * - Mixing preview: full screen, what goes into one jar / batch, easy to read while mixing.
 * Same sums as the server (FieldOperation.chemical_qty). A row's hidden dose_water is the jar its amount is
 * for; its hidden dose_basis is "water" (jar) or "batch" (dry mix). */
(function () {
  const section = document.getElementById("chemSection");
  const dataEl = document.getElementById("form-data");
  if (!section || !dataEl) return;
  const data = JSON.parse(dataEl.textContent).chem || { items: [], op_types: {}, saved: [], default_jar: 500 };
  const text = JSON.parse(document.getElementById("chem-text").textContent);
  const chems = new Map(data.items.map((c) => [String(c.id), c]));
  const rowsBox = document.getElementById("chemRows");
  const search = document.getElementById("chemSearch");
  const menu = document.getElementById("chemMenu");
  const form = document.getElementById("mainForm");
  const $ = (name) => document.getElementById("id_" + name);
  const fill = (tpl, vals) => tpl.replace(/\{(\w+)\}/g, (_, k) => (k in vals ? vals[k] : ""));
  const money = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const qtyFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });
  const num = (el) => (el ? parseFloat(el.value) || 0 : 0);
  const q = (x, unit) => (qtyFmt.format(x) + " " + (unit || "")).trim();
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const round3 = (x) => Math.round(x * 1000) / 1000;
  const setNum = (el, x) => { if (el) el.value = x ? String(round3(x)) : ""; };

  const mixType = () => (form.querySelector('input[name="mix_type"]:checked') || {}).value || "water";
  const jar = () => num($("tank_litres"));
  const rounds = () => num($("rounds")) || 1;

  // ---------------- rows ----------------
  const field = (row, name) => row.querySelector(`[name$="-${name}"]`);
  const rowChem = (row) => chems.get(String((field(row, "agri_input") || {}).value || ""));
  const basisOf = (row) => field(row, "dose_basis").value || "water";
  const isDeleted = (row) => { const d = field(row, "DELETE"); return d && d.checked; };
  const liveRows = () => [...rowsBox.querySelectorAll("[data-chem-row]")].filter((r) => !isDeleted(r));
  const unitOf = (row) => (rowChem(row) || {}).unit || "";
  const autoQty = (row) => {
    const dose = num(field(row, "dose_qty")), basis = basisOf(row);
    if (basis === "plant" || basis === "acre") return null;  // older entries: their own quantity stays
    return round3(dose * rounds());
  };
  const purchaseOf = (row) => {
    const c = rowChem(row), id = (field(row, "purchase") || {}).value;
    return c && id ? c.purchases.find((x) => String(x.id) === String(id)) : null;
  };
  // the amount of a chemical for this mix, from its dosage
  function defaultAmount(c) {
    if (mixType() === "dry") return c.pack || c.dose;  // one bag of each (change it to your recipe)
    return c.basis === "water" && c.water && jar() ? c.dose * jar() / c.water : c.dose;
  }

  function fillPurchases(row, chem) {
    const sel = field(row, "purchase");
    const current = sel.value;
    const keep = current && !chem.purchases.some((x) => String(x.id) === current) ? [...sel.options].find((o) => o.value === current) : null;
    sel.innerHTML = "";
    sel.add(new Option("— " + text.noPurchase + " —", ""));
    for (const x of chem.purchases) sel.add(new Option(x.label, x.id));
    if (keep) sel.add(new Option(keep.text, keep.value));
    sel.value = current;
  }

  function info(c) {
    return [c.type_name, c.dosage || text.noDose, text.stock + ": " + q(c.stock, c.unit)].filter(Boolean).join(" · ");
  }

  function refresh(row) {
    const c = rowChem(row);
    if (!c) return;
    const basis = basisOf(row), dose = num(field(row, "dose_qty"));
    row.querySelector('[data-c="doselabel"]').textContent = basis === "batch" ? text.inBatch
      : basis === "plant" ? text.perPlant : basis === "acre" ? text.perAcre
      : jar() ? fill(text.inJar, { jar: qtyFmt.format(jar()) }) : text.inJarNo;
    row.querySelector('[data-c="calc"]').textContent = dose && autoQty(row) !== null ? fill(text.xRounds, { qty: q(dose, c.unit), n: qtyFmt.format(rounds()) }) : "";
    const qty = num(field(row, "quantity"));
    row.querySelector('[data-c="cost"]').textContent = money.format(qty * num(field(row, "unit_cost")));
    const warn = row.querySelector('[data-c="warn"]'), p = purchaseOf(row);
    let msg = "";
    if (p && qty > p.left + 1e-9) msg = fill(text.short, { left: q(Math.max(p.left, 0), c.unit) });
    else if (qty > c.stock + 1e-9) msg = fill(text.shortStock, { left: q(c.stock, c.unit) });
    warn.textContent = msg;
    warn.hidden = !msg;
  }

  function recalc(row) {
    const a = autoQty(row);
    if (!row.dataset.manual && a !== null) setNum(field(row, "quantity"), a);
    refresh(row);
  }

  function setup(row) {
    const c = rowChem(row);
    if (!c) return;
    row.querySelector('[data-c="name"]').textContent = c.name;
    row.querySelector('[data-c="info"]').textContent = info(c);
    row.querySelector('[data-c="unit"]').textContent = c.unit || "–";
    row.querySelector('[data-c="qunit"]').textContent = c.unit ? "(" + c.unit + ")" : "";
    fillPurchases(row, c);
    // an amount saved for another jar size is shown for this jar (same ratio)
    const w = num(field(row, "dose_water"));
    if (basisOf(row) === "water" && jar() && w && Math.abs(w - jar()) > 1e-9) {
      setNum(field(row, "dose_qty"), num(field(row, "dose_qty")) * jar() / w);
    }
    if (basisOf(row) === "water") setNum(field(row, "dose_water"), jar());
    const qty = num(field(row, "quantity")), a = autoQty(row);
    row.dataset.manual = a === null || (qty && Math.abs(qty - a) > 0.0005) ? "1" : "";
    refresh(row);
  }

  function showMixType() {
    const t = mixType();
    for (const box of section.querySelectorAll("[data-mix-only]")) box.hidden = box.dataset.mixOnly !== t;
  }

  function summary() {
    const rows = liveRows();
    if (!rows.length) return "";
    if (mixType() === "water") {
      return jar() ? fill(text.waterSum, { jar: qtyFmt.format(jar()), n: qtyFmt.format(rounds()), total: qtyFmt.format(jar() * rounds()) }) : "";
    }
    // dry mix: weight of one batch (kg / g / L shown per unit when units differ)
    const byUnit = {};
    for (const r of rows) byUnit[unitOf(r)] = (byUnit[unitOf(r)] || 0) + num(field(r, "dose_qty"));
    const batch = Object.entries(byUnit).map(([u, x]) => q(x, u)).join(" + ");
    const total = Object.entries(byUnit).map(([u, x]) => q(x * rounds(), u)).join(" + ");
    const parts = [fill(text.drySum, { batch, n: qtyFmt.format(rounds()), total })];
    const kg = byUnit.kg || 0, dose = num($("mix_dose")), packet = num($("packet_size"));
    if (kg && dose) parts.push(fill(text.plantsSum, { dose: qtyFmt.format(dose), per: qtyFmt.format(Math.floor(kg / dose)), all: qtyFmt.format(Math.floor(kg * rounds() / dose)) }));
    if (kg && packet) parts.push(fill(text.packetsSum, { n: qtyFmt.format(round3(kg * rounds() / packet)), size: qtyFmt.format(packet) }));
    return parts.join(" · ");
  }

  function totals() {
    let sum = 0;
    for (const r of liveRows()) sum += num(field(r, "quantity")) * num(field(r, "unit_cost"));
    document.getElementById("chemTotal").textContent = text.currency + " " + money.format(sum);
    document.getElementById("chemEmpty").hidden = liveRows().length > 0;
    document.getElementById("mixSummary").textContent = summary();
    return sum;
  }
  window.PMSChem = { total: totals };
  const changed = () => { totals(); form.dispatchEvent(new Event("input")); };

  function pickPurchase(chem) {
    const date = $("date") && window.pmsDate ? window.pmsDate.toISO($("date").value) : null;
    const before = chem.purchases.filter((x) => !date || x.date <= date);  // newest first
    return before.find((x) => x.left > 0) || before[0] || chem.purchases.find((x) => x.left > 0) || chem.purchases[0] || null;
  }

  function addRow(chem, amount) {
    if (mixType() === "water" && !jar()) setNum($("tank_litres"), data.default_jar || 500);
    if (!num($("rounds"))) $("rounds").value = "1";
    const total = document.getElementById("id_chem-TOTAL_FORMS");
    const i = Number(total.value);
    rowsBox.insertAdjacentHTML("beforeend", document.getElementById("chemTemplate").innerHTML.replace(/__prefix__/g, String(i)));
    total.value = i + 1;
    const row = rowsBox.lastElementChild;
    field(row, "agri_input").value = chem.id;
    field(row, "dose_basis").value = mixType() === "dry" ? "batch" : "water";
    setNum(field(row, "dose_qty"), amount == null ? defaultAmount(chem) : amount);
    setNum(field(row, "dose_water"), mixType() === "water" ? jar() : 0);
    fillPurchases(row, chem);
    const p = pickPurchase(chem);
    field(row, "purchase").value = p ? p.id : "";
    field(row, "unit_cost").value = p ? p.cost : (chem.cost || "");
    field(row, "quantity").value = "";
    setup(row);
    row.dataset.manual = "";
    row.dataset.amountTyped = amount == null ? "" : "1";
    recalc(row);
    return row;
  }

  function removeRow(row) {
    const del = field(row, "DELETE");
    if (del) del.checked = true;
    row.hidden = true;
  }

  rowsBox.addEventListener("input", (e) => {
    const row = e.target.closest("[data-chem-row]");
    if (!row) return;
    const name = (e.target.name || "").split("-").pop();
    if (name === "quantity") row.dataset.manual = e.target.value === "" ? "" : "1";
    if (name === "dose_qty") { row.dataset.manual = ""; row.dataset.amountTyped = "1"; recalc(row); }
    refresh(row);
    changed();
  });
  rowsBox.addEventListener("change", (e) => {
    const row = e.target.closest("[data-chem-row]");
    if (!row) return;
    if ((e.target.name || "").endsWith("-purchase")) {
      const p = purchaseOf(row), c = rowChem(row);
      field(row, "unit_cost").value = p ? p.cost : (c ? c.cost : 0);
    }
    refresh(row);
    changed();
  });
  rowsBox.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-c="remove"]');
    if (!btn) return;
    removeRow(btn.closest("[data-chem-row]"));
    changed();
    renderMenu();
  });

  // in water <-> dry: every row follows (amounts not typed by hand go back to their default)
  for (const radio of form.querySelectorAll('input[name="mix_type"]')) {
    radio.addEventListener("change", () => {
      showMixType();
      if (mixType() === "water" && !jar() && liveRows().length) setNum($("tank_litres"), data.default_jar || 500);
      for (const r of liveRows()) {
        const c = rowChem(r);
        field(r, "dose_basis").value = mixType() === "dry" ? "batch" : "water";
        setNum(field(r, "dose_water"), mixType() === "water" ? jar() : 0);
        if (c && !r.dataset.amountTyped) setNum(field(r, "dose_qty"), defaultAmount(c));
        r.dataset.manual = "";
        recalc(r);
      }
      changed();
    });
  }
  // jar capacity: every amount follows it (same ratio)
  $("tank_litres").addEventListener("input", () => {
    const now = jar();
    for (const r of liveRows()) {
      if (basisOf(r) !== "water" || !now) continue;  // keep the amounts while the box is being retyped
      const was = num(field(r, "dose_water"));
      if (was) setNum(field(r, "dose_qty"), num(field(r, "dose_qty")) * now / was);
      setNum(field(r, "dose_water"), now);
      r.dataset.manual = "";
      recalc(r);
    }
    changed();
  });
  for (const name of ["rounds", "mix_dose", "packet_size"]) {
    const el = $(name);
    if (el) el.addEventListener("input", () => { for (const r of liveRows()) recalc(r); changed(); });
  }

  // ---------------- saved mixes ----------------
  const load = document.getElementById("mixLoad");
  for (const mix of data.saved || []) load.add(new Option(mix.label, mix.id));
  load.addEventListener("change", () => {
    const mix = (data.saved || []).find((x) => String(x.id) === load.value);
    if (!mix) return;
    for (const r of liveRows()) removeRow(r);
    const radio = form.querySelector(`input[name="mix_type"][value="${mix.mix_type}"]`);
    if (radio) radio.checked = true;
    showMixType();
    setNum($("tank_litres"), mix.tank);
    setNum($("mix_dose"), mix.mix_dose);
    setNum($("packet_size"), mix.packet);
    setNum($("rounds"), mix.rounds || 1);
    if (mix.name) $("mix_name").value = mix.name;
    for (const line of mix.lines) {
      const c = chems.get(String(line.chem));
      if (!c) continue;
      let amount = line.dose;
      if (line.basis === "water" && line.water && mix.tank && line.water !== mix.tank) amount = line.dose * mix.tank / line.water;
      addRow(c, amount);
    }
    load.value = "";
    changed();
    renderMenu();
  });

  // ---------------- search & add ----------------
  let active = -1;
  const usualType = () => ($("operation_type") ? data.op_types[$("operation_type").value] || null : null);
  function matches() {
    const term = search.value.trim().toLowerCase(), usual = usualType();
    const list = data.items.filter((c) => (c.active || term) && (!term || (c.name + " " + c.type_name).toLowerCase().includes(term)));
    list.sort((a, b) => (usual ? (b.type === usual) - (a.type === usual) : 0) || a.name.localeCompare(b.name));
    return list.slice(0, 40);
  }
  function renderMenu() {
    if (menu.hidden) return;
    const used = new Set(liveRows().map((r) => String(field(r, "agri_input").value)));
    const usual = usualType(), list = matches();
    active = Math.min(active, list.length - 1);
    menu.innerHTML = list.length ? list.map((c, i) => {
      const tags = [];
      if (used.has(String(c.id))) tags.push('<span class="badge text-bg-success"><i class="bi bi-check2"></i> ' + esc(text.added) + "</span>");
      else if (usual && c.type === usual) tags.push('<span class="badge text-bg-light border">' + esc(text.usual) + "</span>");
      if (!c.active) tags.push('<span class="badge text-bg-secondary">' + esc(text.inactive) + "</span>");
      return '<button type="button" role="option" class="chem-opt' + (i === active ? " active" : "") + (used.has(String(c.id)) ? " used" : "") + '" data-id="' + c.id + '">'
        + '<span class="d-flex justify-content-between gap-2"><strong>' + esc(c.name) + "</strong><span>" + tags.join(" ") + "</span></span>"
        + '<span class="small text-muted">' + esc(info(c)) + "</span></button>";
    }).join("") : '<div class="p-3 small text-muted">' + esc(text.noMatch) + "</div>";
  }
  const openMenu = () => { menu.hidden = false; search.setAttribute("aria-expanded", "true"); renderMenu(); };
  const closeMenu = () => { menu.hidden = true; search.setAttribute("aria-expanded", "false"); active = -1; };
  search.addEventListener("focus", openMenu);
  search.addEventListener("input", () => { active = 0; openMenu(); });
  search.addEventListener("keydown", (e) => {
    const opts = [...menu.querySelectorAll(".chem-opt")];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (menu.hidden) openMenu();
      active = Math.max(0, Math.min(opts.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
      renderMenu();
    } else if (e.key === "Enter") {
      e.preventDefault();  // never submit the whole form from the search box
      const pick = opts[active >= 0 ? active : 0];
      if (pick) { pick.click(); search.value = ""; active = -1; renderMenu(); }
    } else if (e.key === "Escape") closeMenu();
  });
  menu.addEventListener("mousedown", (e) => e.preventDefault());  // keep the focus (and the keyboard) in the box
  menu.addEventListener("click", (e) => {
    const opt = e.target.closest(".chem-opt");
    if (!opt) return;
    const existing = liveRows().find((r) => String(field(r, "agri_input").value) === opt.dataset.id);
    if (existing) existing.scrollIntoView({ block: "center", behavior: "smooth" });
    else { addRow(chems.get(opt.dataset.id)); changed(); }
    renderMenu();
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".chem-picker")) closeMenu(); });
  if ($("operation_type")) $("operation_type").addEventListener("change", renderMenu);

  // ---------------- full-screen mixing preview: what goes into one jar / batch ----------------
  function preview() {
    const rows = liveRows().filter(rowChem);
    const sel = (name) => { const el = $(name); return el && el.selectedIndex > 0 ? el.options[el.selectedIndex].text : ""; };
    let html = '<div class="mix-head">' + esc([sel("operation_type"), sel("block"), $("date") ? $("date").value : "", $("mix_name").value].filter(Boolean).join(" · ")) + "</div>";
    if (!rows.length) return html + '<p class="mix-empty">' + esc(text.nothing) + "</p>";
    const dry = mixType() === "dry";
    let head;
    if (dry) {
      const byUnit = {};
      for (const r of rows) byUnit[unitOf(r)] = (byUnit[unitOf(r)] || 0) + num(field(r, "dose_qty"));
      head = fill(text.dryHead, { batch: Object.entries(byUnit).map(([u, x]) => q(x, u)).join(" + "), n: qtyFmt.format(rounds()) });
    } else {
      head = jar() ? fill(text.jarHead, { jar: qtyFmt.format(jar()), n: qtyFmt.format(rounds()) }) : "";
    }
    const sub = summary();
    if (head) html += '<div class="mix-rounds"><div class="mix-big">' + esc(head) + "</div>" + (dry && sub ? '<div class="mix-sub">' + esc(sub.split(" · ").slice(1).join(" · ")) + "</div>" : "") + "</div>";
    html += '<h3 class="mix-h"><i class="bi ' + (dry ? "bi-bag" : "bi-droplet-half") + '"></i> ' + esc(dry ? text.intoBatch : text.intoJar) + '</h3><ul class="mix-list">';
    for (const r of rows) {
      const c = rowChem(r);
      html += '<li class="mix-item"><span class="mix-name">' + esc(c.name) + '</span><span class="mix-amt">' + esc(q(num(field(r, "dose_qty")), c.unit)) + "</span>"
        + '<span class="mix-sub-line"><span class="mix-type-name">' + esc(c.type_name) + "</span>"
        + (c.dosage ? '<span class="mix-dosage">· ' + esc(c.dosage) + "</span>" : "")
        + '<span class="mix-stock' + (c.stock < num(field(r, "quantity")) ? " low" : "") + '">· ' + esc(text.stock + ": " + q(c.stock, c.unit)) + "</span></span></li>";
    }
    return html + "</ul>";
  }
  const modalEl = document.getElementById("mixModal");
  let modal = null;
  document.getElementById("mixPreviewBtn").addEventListener("click", () => {
    document.getElementById("mixBody").innerHTML = preview();
    modal = modal || new bootstrap.Modal(modalEl);
    modal.show();
  });

  // ---------------- start ----------------
  // 500.00 -> 500, 1650.000 -> 1650 (easier to read; same value)
  for (const el of form.querySelectorAll('input[inputmode="decimal"]')) {
    if (/^-?\d+\.\d*0$/.test(el.value)) el.value = String(parseFloat(el.value));
  }
  showMixType();
  const all = [...rowsBox.querySelectorAll("[data-chem-row]")];
  if (mixType() === "water" && !jar()) {  // an older entry without a jar size: the one its amounts were for
    const w = all.map((r) => num(field(r, "dose_water"))).find((x) => x > 0);
    if (w) setNum($("tank_litres"), w);
  }
  for (const r of all) {
    setup(r);
    r.dataset.amountTyped = "1";  // saved amounts are kept when switching the mix type
    if (isDeleted(r)) r.hidden = true;
  }
  totals();
})();
