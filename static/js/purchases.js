/* Fertilizer / chemical page: its purchases table (add a row, live total and cost per unit). */
(function () {
  const rows = document.getElementById("buyRows");
  if (!rows) return;
  const fmt = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmt4 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 4 });
  const num = (el) => (el ? parseFloat(el.value) || 0 : 0);
  const get = (tr, name) => tr.querySelector(`[name$="-${name}"]`);

  function update(tr) {
    const bags = num(get(tr, "bags")), pack = num(get(tr, "pack_size")), price = num(get(tr, "price_per_bag"));
    const total = tr.querySelector('[data-b="total"]'), unit = tr.querySelector('[data-b="unit"]');
    if (total) total.textContent = fmt.format(bags * price);
    if (unit) unit.textContent = pack ? fmt4.format(price / pack) : (price ? fmt4.format(price) : "");
  }
  // 25.000 -> 25 (easier to read; same value)
  for (const el of document.querySelectorAll('#mainForm input[inputmode="decimal"]')) {
    if (/^-?\d+\.\d*0$/.test(el.value)) el.value = String(parseFloat(el.value));
  }
  rows.addEventListener("input", (e) => { const tr = e.target.closest("[data-buy]"); if (tr) update(tr); });

  document.getElementById("addPurchase").addEventListener("click", () => {
    const total = document.getElementById("id_buy-TOTAL_FORMS");
    const i = Number(total.value);
    const empty = rows.querySelector("[data-empty]");
    if (empty) empty.remove();
    rows.insertAdjacentHTML("beforeend", document.getElementById("buyTemplate").innerHTML.replace(/__prefix__/g, String(i)));
    total.value = i + 1;
    const tr = rows.lastElementChild;
    const date = get(tr, "date"), pack = get(tr, "pack_size"), chemPack = document.getElementById("id_pack_size");
    if (date && !date.value && window.pmsDate) {
      const d = new Date(), iso = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
      date.value = window.pmsDate.fromISO(iso);
    }
    if (pack && !num(pack) && chemPack) pack.value = chemPack.value;
    const bags = get(tr, "bags");
    if (bags) { bags.value = ""; bags.focus(); }
    update(tr);
  });
})();
