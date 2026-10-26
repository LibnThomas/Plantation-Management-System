/*
 * Delete links ask in a pop-up instead of opening a separate page:
 *   <a href="/m/expenses/5/delete/" data-confirm data-name="12/10/26 - Diesel" data-kind="Expense">
 * The pop-up first asks the server what else the delete affects (href?preview=1) and lists it:
 * linked records deleted too, links that are only emptied, or - for shared lists still in use - why it
 * can't be deleted. "Yes, delete" sends the POST and comes back to this page.
 * Without JavaScript the link still opens the normal confirmation page.
 */
(function () {
  "use strict";
  const T = Object.assign(
    {
      title: "Delete this record?", warn: "This cannot be undone.", yes: "Yes, delete", cancel: "Cancel",
      yesAll: "Yes, delete all", checking: "Checking linked records…", alsoDelete: "Also deleted:",
      cleared: "Kept, only the link is emptied:", blocked: "It cannot be deleted - still used by:",
      blockedHelp: "Mark it inactive instead (edit it and switch off Active).", more: "+{n} more", close: "Close",
      failed: "Could not check the linked records.",
    },
    JSON.parse((document.getElementById("confirm-text") || {}).textContent || "{}")
  );
  let modal = null, el = null, target = null, request = 0;

  const q = (k) => el.querySelector(`[data-c="${k}"]`);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function build() {
    el = document.createElement("div");
    el.className = "modal fade confirm-modal";
    el.tabIndex = -1;
    el.setAttribute("aria-hidden", "true");
    el.innerHTML = `<div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" data-c="dialog"><div class="modal-content">
      <div class="modal-body p-4">
        <div class="text-center">
          <div class="confirm-icon mx-auto mb-3"><i class="bi bi-trash3"></i></div>
          <h5 class="mb-1" data-c="title"></h5>
          <div class="small text-muted" data-c="kind"></div>
          <div class="fw-semibold my-2 text-break" data-c="name"></div>
        </div>
        <div data-c="impact" class="confirm-impact small"></div>
        <div class="small text-danger text-center mt-2" data-c="warn"></div>
      </div>
      <div class="modal-footer border-0 pt-0 justify-content-center gap-2">
        <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal" data-c="cancel"></button>
        <button type="button" class="btn btn-danger" data-c="yes"></button>
      </div></div></div>`;
    document.body.appendChild(el);
    q("title").textContent = T.title;
    q("warn").textContent = T.warn;
    q("yes").addEventListener("click", submit);
    modal = new bootstrap.Modal(el);
  }

  function group(g) {
    const items = g.items.map((i) => `<li>${esc(i)}</li>`).join("");
    const more = g.more ? `<li class="text-muted">${esc(T.more.replace("{n}", g.more))}</li>` : "";
    return `<div class="impact-group impact-${g.action}"><div class="fw-semibold">${esc(g.label)}
      <span class="badge rounded-pill ms-1">${g.count}</span></div><ul class="mb-0">${items}${more}</ul></div>`;
  }

  function render(data) {
    const by = (a) => data.groups.filter((g) => g.action === a);
    const blocks = by("block"), deletes = by("delete"), clears = by("clear");
    let html = "";
    if (blocks.length) {
      html += `<div class="text-danger fw-semibold mt-3 mb-1"><i class="bi bi-shield-lock"></i> ${esc(T.blocked)}</div>`
        + blocks.map(group).join("") + `<div class="text-muted mt-2">${esc(T.blockedHelp)}</div>`;
    } else {
      if (deletes.length) html += `<div class="text-danger fw-semibold mt-3 mb-1"><i class="bi bi-exclamation-triangle"></i> ${esc(T.alsoDelete)}</div>` + deletes.map(group).join("");
      if (clears.length) html += `<div class="text-muted fw-semibold mt-3 mb-1"><i class="bi bi-link-45deg"></i> ${esc(T.cleared)}</div>` + clears.map(group).join("");
    }
    q("impact").innerHTML = html;
    q("dialog").classList.toggle("modal-sm", !html);
    const yes = q("yes");
    yes.hidden = !data.can_delete;
    yes.textContent = deletes.length ? T.yesAll : T.yes;
    yes.disabled = !data.can_delete;
    q("warn").hidden = !data.can_delete;
    q("cancel").textContent = data.can_delete ? T.cancel : T.close;
  }

  function submit() {
    if (!target) return;
    q("yes").disabled = true;
    const form = document.createElement("form");
    form.method = "post";
    form.action = target.getAttribute("href");
    const token = document.querySelector('input[name="csrfmiddlewaretoken"]');
    const add = (name, value) => {
      const i = document.createElement("input");
      i.type = "hidden"; i.name = name; i.value = value;
      form.appendChild(i);
    };
    if (token) add("csrfmiddlewaretoken", token.value);
    add("next", target.dataset.next || location.pathname + location.search);
    document.body.appendChild(form);
    form.submit();
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[data-confirm]");
    if (!a || !window.bootstrap) return;
    e.preventDefault();
    if (!modal) build();
    target = a;
    const mine = ++request;
    q("kind").textContent = a.dataset.kind || "";
    q("name").textContent = a.dataset.name || "";
    q("impact").innerHTML = `<div class="text-center text-muted mt-3"><span class="spinner-border spinner-border-sm"></span> ${esc(T.checking)}</div>`;
    q("dialog").classList.add("modal-sm");
    q("yes").hidden = false;
    q("yes").disabled = true;  // until we know what it affects
    q("yes").textContent = T.yes;
    q("cancel").textContent = T.cancel;
    q("warn").hidden = false;
    modal.show();
    const url = a.getAttribute("href") + (a.getAttribute("href").includes("?") ? "&" : "?") + "preview=1";
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => { if (mine === request) render(data); })
      .catch(() => {
        if (mine !== request) return;
        q("impact").innerHTML = `<div class="text-center text-muted mt-3">${esc(T.failed)}</div>`;
        q("yes").disabled = false;  // the server still checks before deleting
      });
  });
})();
