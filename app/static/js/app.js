// Progressive enhancements. Every page works without this file.
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const csrf = document.body.dataset.csrf;

  // Placeholder colour (sampled from each image) shown while it loads.
  // Set via CSSOM because the CSP disallows inline style attributes.
  function applyColors(root = document) {
    $$("[data-color]", root).forEach((el) => el.style.setProperty("--c", el.dataset.color));
  }

  function fadeInImages(root = document) {
    $$(".card-media img", root).forEach((img) => {
      if (img.complete && img.naturalWidth) img.classList.add("is-loaded");
      else {
        img.addEventListener("load", () => img.classList.add("is-loaded"), { once: true });
        img.addEventListener("error", () => img.classList.add("is-loaded"), { once: true });
      }
    });
  }

  applyColors();
  fadeInImages();

  // Theme toggle
  $$("[data-theme-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) {}
    })
  );

  // Toasts: dismiss manually or automatically.
  $$(".toast").forEach((t, i) => {
    const close = () => { t.classList.add("is-leaving"); setTimeout(() => t.remove(), 300); };
    $(".toast-x", t)?.addEventListener("click", close);
    setTimeout(close, 5000 + i * 800);
  });

  // Close account menu when clicking outside.
  document.addEventListener("click", (e) => {
    $$("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });

  // Auto-submit selects (sorting).
  $$("[data-autosubmit]").forEach((el) => el.addEventListener("change", () => el.form.submit()));

  // Confirmation for destructive buttons.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-confirm]");
    if (btn && !confirm(btn.dataset.confirm)) e.preventDefault();
  });

  // Infinite scroll: fetch the next page's grid items and append them.
  const grid = $("[data-grid]");
  if (grid && "IntersectionObserver" in window) {
    let loading = false;
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => { if (entry.isIntersecting) loadMore(entry.target); });
    }, { rootMargin: "800px" });

    async function loadMore(link) {
      if (loading) return;
      loading = true;
      io.unobserve(link);
      link.textContent = "Loading…";
      try {
        const url = new URL(link.href);
        url.searchParams.set("partial", "1");
        const res = await fetch(url, { headers: { Accept: "text/html" } });
        if (!res.ok) throw new Error(res.status);
        const tpl = document.createElement("template");
        tpl.innerHTML = await res.text();
        applyColors(tpl.content);
        link.remove();
        const nodes = Array.from(tpl.content.children);
        grid.append(...nodes);
        fadeInImages(grid);
        watch();
      } catch (err) {
        link.textContent = "Load more";
      } finally {
        loading = false;
      }
    }
    function watch() {
      const link = $("[data-load-more]", grid);
      if (link) io.observe(link);
    }
    grid.addEventListener("click", (e) => {
      const link = e.target.closest("[data-load-more]");
      if (link) { e.preventDefault(); loadMore(link); }
    });
    watch();
  }

  // Appreciate (like) button.
  $$("[data-like]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const res = await fetch(btn.dataset.like, { method: "POST", headers: { "X-CSRF-Token": csrf } });
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        btn.classList.toggle("is-liked", data.liked);
        btn.setAttribute("aria-pressed", String(data.liked));
        $("[data-like-count]", btn).textContent = data.count;
        if (data.liked) { btn.classList.remove("pulse"); void btn.offsetWidth; btn.classList.add("pulse"); }
      } finally {
        btn.disabled = false;
      }
    })
  );

  // Share: native share sheet or copy link.
  $$("[data-share]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const url = btn.dataset.share;
      if (navigator.share) {
        try { await navigator.share({ title: btn.dataset.shareTitle, url }); } catch (e) {}
      } else if (navigator.clipboard) {
        await navigator.clipboard.writeText(url);
        const old = btn.textContent;
        btn.textContent = "Link copied ✓";
        setTimeout(() => (btn.textContent = old), 1800);
      }
    })
  );

  // Lightbox + keyboard navigation on artwork pages.
  const lightbox = $("[data-lightbox]");
  if (lightbox) {
    const img = $("img", lightbox);
    const open = () => {
      if (!img.src) img.src = img.dataset.src;
      lightbox.showModal();
    };
    $("[data-lightbox-open]")?.addEventListener("click", open);
    $("[data-lightbox-close]", lightbox).addEventListener("click", () => lightbox.close());
    lightbox.addEventListener("click", (e) => { if (e.target === lightbox) lightbox.close(); });
    document.addEventListener("keydown", (e) => {
      if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "f") open();
      if (e.key === "ArrowLeft") $("[data-key-prev]")?.click();
      if (e.key === "ArrowRight") $("[data-key-next]")?.click();
    });
  }

  // Studio bulk selection.
  const bulk = $("[data-bulk]");
  if (bulk) {
    const boxes = $$('input[name="ids"]', bulk);
    const all = $("[data-select-all]", bulk);
    const count = $("[data-selected-count]", bulk);
    const actions = $$(".bulk-actions button", bulk);
    const update = () => {
      const n = boxes.filter((b) => b.checked).length;
      count.textContent = `${n} selected`;
      actions.forEach((b) => (b.disabled = n === 0));
      all.checked = n > 0 && n === boxes.length;
      all.indeterminate = n > 0 && n < boxes.length;
    };
    all.addEventListener("change", () => { boxes.forEach((b) => (b.checked = all.checked)); update(); });
    boxes.forEach((b) => b.addEventListener("change", update));
    update();
  }

  // Upload: drag & drop with previews.
  const dz = $("[data-dropzone]");
  if (dz) {
    const input = $('input[type="file"]', dz);
    const previews = $("[data-previews]", dz);
    const render = () => {
      previews.innerHTML = "";
      Array.from(input.files).forEach((file) => {
        const fig = document.createElement("div");
        fig.className = "preview";
        const im = document.createElement("img");
        im.alt = "";
        im.src = URL.createObjectURL(file);
        im.onload = () => URL.revokeObjectURL(im.src);
        const cap = document.createElement("span");
        cap.textContent = file.name;
        fig.append(im, cap);
        previews.append(fig);
      });
      dz.classList.toggle("has-files", input.files.length > 0);
    };
    input.addEventListener("change", render);
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("is-over")));
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; render(); }
    });
    const form = $("[data-upload]");
    form.addEventListener("submit", () => {
      const btn = $("[data-submit-label]", form);
      btn.disabled = true;
      btn.textContent = btn.dataset.submitLabel;
    });
  }
})();
