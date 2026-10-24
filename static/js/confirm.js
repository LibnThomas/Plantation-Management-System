/*
 * Delete (and other dangerous) links ask in a pop-up instead of opening a separate page:
 *   <a href="/m/expenses/5/delete/" data-confirm data-name="12/10/26 - Diesel" data-kind="Expense">
 * "Yes, delete" sends the POST the delete page would have sent and comes back to this page.
 * Without JavaScript the link still opens the normal confirmation page.
 */
(function () {
  "use strict";
  const T = Object.assign(
    { title: "Delete this record?", warn: "This cannot be undone.", yes: "Yes, delete", cancel: "Cancel" },
    JSON.parse((document.getElementById("confirm-text") || {}).textContent || "{}")
  );
  let modal = null, el = null, target = null;

  function build() {
    el = document.createElement("div");
    el.className = "modal fade confirm-modal";
    el.tabIndex = -1;
    el.setAttribute("aria-hidden", "true");
    el.innerHTML = `<div class="modal-dialog modal-dialog-centered modal-sm"><div class="modal-content">
      <div class="modal-body text-center p-4">
        <div class="confirm-icon mx-auto mb-3"><i class="bi bi-trash3"></i></div>
        <h5 class="mb-1" data-c="title"></h5>
        <div class="small text-muted" data-c="kind"></div>
        <div class="fw-semibold my-2 text-break" data-c="name"></div>
        <div class="small text-danger" data-c="warn"></div>
      </div>
      <div class="modal-footer border-0 pt-0 justify-content-center gap-2">
        <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal" data-c="cancel"></button>
        <button type="button" class="btn btn-danger" data-c="yes"></button>
      </div></div></div>`;
    document.body.appendChild(el);
    const set = (k, v) => (el.querySelector(`[data-c="${k}"]`).textContent = v);
    set("title", T.title); set("warn", T.warn); set("yes", T.yes); set("cancel", T.cancel);
    el.querySelector('[data-c="yes"]').addEventListener("click", submit);
    modal = new bootstrap.Modal(el);
  }

  function submit() {
    if (!target) return;
    const btn = el.querySelector('[data-c="yes"]');
    btn.disabled = true;
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
    el.querySelector('[data-c="kind"]').textContent = a.dataset.kind || "";
    el.querySelector('[data-c="name"]').textContent = a.dataset.name || "";
    el.querySelector('[data-c="yes"]').disabled = false;
    modal.show();
  });
})();
