(() => {
  const scope = document.body.dataset.noticePage;
  const pages = {home: "报销首页", invoice: "日常报销", travel: "差旅报销"};
  if (!pages[scope]) return;

  const dialog = document.createElement("dialog");
  dialog.className = "entry-notice";
  dialog.setAttribute("aria-labelledby", "entry-notice-title");
  dialog.innerHTML = `
    <div class="entry-notice-layout">
      <header class="entry-notice-heading">
        <span class="entry-notice-page"></span>
        <h2 id="entry-notice-title" tabindex="-1">报销注意事项</h2>
      </header>
      <div class="entry-notice-body" tabindex="0" aria-label="注意事项正文"></div>
      <footer class="entry-notice-actions">
        <button type="button" class="entry-notice-retry" hidden>重新读取</button>
        <button type="button" class="entry-notice-continue" disabled></button>
      </footer>
    </div>`;
  document.body.append(dialog);
  const title = dialog.querySelector("h2");
  const body = dialog.querySelector(".entry-notice-body");
  const next = dialog.querySelector(".entry-notice-continue");
  const retry = dialog.querySelector(".entry-notice-retry");
  dialog.querySelector(".entry-notice-page").textContent = scope === "home" ? "报销注意事项" : pages[scope];
  next.textContent = scope === "home" ? "选择报销类型" : `进入${pages[scope]}`;
  let request;

  async function showNotice() {
    request?.abort();
    const controller = new AbortController();
    request = controller;
    title.textContent = "报销注意事项";
    body.textContent = "正在读取注意事项…";
    body.setAttribute("aria-busy", "true");
    retry.hidden = true;
    next.disabled = true;
    if (!dialog.open) dialog.showModal();
    title.focus({preventScroll: true});
    const timer = window.setTimeout(() => controller.abort(), 6000);
    try {
      const response = await fetch(`/v1/notices/${scope}`, {cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error();
      const notice = await response.json();
      if (request !== controller) return;
      if (typeof notice.title !== "string" || typeof notice.body !== "string") throw new Error();
      title.textContent = notice.title;
      // 管理员输入按纯文本展示，保留分段和换行。
      body.replaceChildren(...notice.body.split(/\n\s*\n/).map(paragraph => {
        const p = document.createElement("p");
        p.textContent = paragraph;
        return p;
      }));
      body.scrollTop = 0;
    } catch {
      if (request !== controller) return;
      body.textContent = "暂时无法读取本页的注意事项。可以重试，也可以先进入页面，稍后从导航栏查看。";
      retry.hidden = false;
    } finally {
      clearTimeout(timer);
      if (request === controller) {
        next.disabled = false;
        body.removeAttribute("aria-busy");
      }
    }
  }
  next.addEventListener("click", () => dialog.close());
  retry.addEventListener("click", showNotice);
  dialog.addEventListener("close", () => {
    request?.abort();
    request = null;
  });
  document.querySelectorAll("[data-open-notice]").forEach(button => button.addEventListener("click", showNotice));
  window.addEventListener("pageshow", event => { if (event.persisted) showNotice(); });
  showNotice();
})();
