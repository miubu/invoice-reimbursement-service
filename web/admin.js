const loginPanel = document.querySelector("#login-panel");
const adminConsole = document.querySelector("#admin-console");
const loginForm = document.querySelector("#login-form");
const loginError = document.querySelector("#login-error");
const logoutButton = document.querySelector("#logout-button");
const ruleList = document.querySelector("#rule-list");
const siteTextFields = document.querySelector("#site-text-fields");
const imageManager = document.querySelector("#image-manager");
const toast = document.querySelector("#toast");

const credentialKey = "invoice-admin-basic-v1";
let authorization = sessionStorage.getItem(credentialKey) || "";
let reimbursementTypes = [];
let toastTimer;

const siteLabels = {
  site_name: "站点名称",
  site_tagline: "站点副标题",
  hero_kicker: "首屏英文小标",
  hero_title: "首屏主标题",
  hero_description: "首屏说明",
  upload_title: "上传区标题",
  upload_primary: "上传区主提示",
  upload_secondary: "上传区副提示",
  profile_title: "个人信息区标题",
  action_title: "操作区标题",
  action_description: "操作区说明",
  preview_button: "预览按钮",
  export_button: "导出按钮",
  print_button: "打印按钮",
};

function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  toastTimer = setTimeout(() => { toast.className = "toast"; }, 4200);
}

function basicCredential(username, password) {
  const bytes = new TextEncoder().encode(`${username}:${password}`);
  let binary = "";
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  return `Basic ${btoa(binary)}`;
}

async function parseError(response) {
  try {
    const payload = await response.json();
    return typeof payload.detail === "string" ? payload.detail : `请求失败（HTTP ${response.status}）`;
  } catch {
    return `请求失败（HTTP ${response.status}）`;
  }
}

async function adminFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", authorization);
  const response = await fetch(url, { ...options, headers, cache: "no-store" });
  if (!response.ok) throw new Error(await parseError(response));
  return response;
}

function renderRules(data) {
  reimbursementTypes = data.reimbursement_types || reimbursementTypes;
  ruleList.replaceChildren();
  (data.rules || []).forEach(addRuleCard);
}

function addRuleCard(rule = {}) {
  const card = document.createElement("article");
  card.className = "rule-card";
  card.dataset.ruleId = rule.id || "";
  const isBuiltin = ["courier", "transport", "cable", "computer_accessories"].includes(rule.id);
  const typeOptions = ["", ...reimbursementTypes].map(value => {
    const label = value || "保持用户默认类型";
    return `<option value="${value}"${value === rule.reimbursement_type ? " selected" : ""}>${label}</option>`;
  }).join("");
  card.innerHTML = `
    <div class="rule-title">
      <div><strong>${isBuiltin ? "内置规则" : "自定义规则"}</strong><p>项目名称包含任一关键词时即命中；不支持 * 通配符。</p></div>
      <div class="rule-card-actions"><label class="toggle"><input class="rule-enabled" type="checkbox" ${rule.enabled !== false ? "checked" : ""}> 启用</label><button class="text-button save-rule" type="button">保存</button>${isBuiltin ? "" : '<button class="text-button danger-button delete-rule" type="button">删除</button>'}</div>
    </div>
    <div class="rule-grid">
      <label class="field"><span>规则名称 <b>*</b></span><input class="rule-name" maxlength="80" placeholder="例如：采购耗材"></label>
      <label class="field"><span>命中后的报销类型</span><select class="rule-type">${typeOptions}</select></label>
      <label class="field wide"><span>规则说明（管理员可见）</span><textarea class="rule-description" maxlength="300" placeholder="说明这条规则在什么情况下使用"></textarea></label>
      <label class="field wide"><span>关键词（每行一个；项目名称包含任一项即命中）<b>*</b></span><textarea class="rule-keywords" maxlength="3100" placeholder="例如：物流服务费"></textarea></label>
      <label class="field"><span>备注提示</span><input class="rule-remark" maxlength="300"></label>
      <label class="field wide"><span>说明模板链接（可为空）</span><input class="rule-url" type="url" maxlength="1000" placeholder="http://、https:// 或站内文件链接"></label>
      <label class="field wide"><span>上传说明文件（DOCX / PDF，最大 10 MB；需一键打印时请上传 PDF）</span><div class="rule-file-controls"><input class="rule-file-input" type="file" accept=".docx,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf"><button class="text-button upload-rule-file" type="button">上传并使用</button><a class="text-button rule-file-link" target="_blank" rel="noreferrer" hidden>打开当前附件</a></div></label>
    </div>`;
  card.querySelector(".rule-name").value = rule.title || "";
  card.querySelector(".rule-description").value = rule.description || "";
  card.querySelector(".rule-remark").value = rule.remark || "";
  card.querySelector(".rule-keywords").value = (rule.keywords || []).join("\n");
  card.querySelector(".rule-url").value = rule.template_url || "";
  card.querySelector(".rule-url").addEventListener("input", () => refreshRuleFileLink(card));
  card.querySelector(".save-rule").addEventListener("click", () => {
    saveRuleCard(card).catch(error => showToast(error.message, true));
  });
  card.querySelector(".upload-rule-file").addEventListener("click", () => uploadRuleFile(card));
  card.querySelector(".delete-rule")?.addEventListener("click", () => deleteRule(card));
  refreshRuleFileLink(card);
  ruleList.append(card);
}

function refreshRuleFileLink(card) {
  const url = card.querySelector(".rule-url").value.trim();
  const link = card.querySelector(".rule-file-link");
  link.hidden = !url;
  if (url) link.href = url;
}

function rulePayload(card) {
  return {
    title: card.querySelector(".rule-name").value.trim(),
    description: card.querySelector(".rule-description").value.trim(),
    reimbursement_type: card.querySelector(".rule-type").value,
    keywords: card.querySelector(".rule-keywords").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
    remark: card.querySelector(".rule-remark").value.trim(),
    template_url: card.querySelector(".rule-url").value.trim(),
    enabled: card.querySelector(".rule-enabled").checked,
  };
}

async function saveRuleCard(card) {
  const ruleId = card.dataset.ruleId;
  const response = await adminFetch(ruleId ? `/v1/admin/rules/${encodeURIComponent(ruleId)}` : "/v1/admin/rules", {
    method: ruleId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rulePayload(card)),
  });
  const payload = await response.json();
  if (!ruleId && payload.rule?.id) card.dataset.ruleId = payload.rule.id;
  showToast("这条规则已保存，新的发票识别立即生效。");
}

async function uploadRuleFile(card) {
  const input = card.querySelector(".rule-file-input");
  if (!input.files?.length) return showToast("请先选择 DOCX 或 PDF 说明文件。", true);
  try {
    if (!card.dataset.ruleId) await saveRuleCard(card);
    const data = new FormData();
    data.append("attachment", input.files[0], input.files[0].name);
    const response = await adminFetch(`/v1/admin/rules/${encodeURIComponent(card.dataset.ruleId)}/file`, {
      method: "POST",
      body: data,
    });
    const payload = await response.json();
    card.querySelector(".rule-url").value = payload.rule?.template_url || "";
    refreshRuleFileLink(card);
    input.value = "";
    showToast("说明文件已上传，站内链接已自动保存到这条规则。");
  } catch (error) { showToast(error.message, true); }
}

function renderTexts(site) {
  siteTextFields.replaceChildren();
  Object.entries(site.texts || {}).forEach(([key, value]) => {
    const label = document.createElement("label");
    const longField = ["hero_description", "action_description", "upload_secondary"].includes(key);
    label.className = `field${longField ? " wide" : ""}`;
    const title = document.createElement("span");
    title.textContent = siteLabels[key] || key;
    const input = document.createElement(longField ? "textarea" : "input");
    input.dataset.siteKey = key;
    input.value = value;
    input.maxLength = ["hero_description", "action_description"].includes(key) ? 500 : 100;
    label.append(title, input);
    siteTextFields.append(label);
  });
}

function createImageEditor(slot, title, site) {
  const card = document.querySelector("#image-editor-template").content.firstElementChild.cloneNode(true);
  const frame = card.querySelector(".image-frame");
  const fileInput = card.querySelector(".image-file");
  const fit = card.querySelector("select");
  const ranges = [...card.querySelectorAll("input[type=range]")];
  const status = card.querySelector(".image-status");
  const choose = card.querySelector(".image-choose");
  const remove = card.querySelector(".image-remove");
  const cancel = card.querySelector(".image-cancel");
  const save = card.querySelector(".image-save");
  const defaultSource = slot === "hero" ? "/assets/invoice-desk.webp" : null;
  let savedSource = site.assets[slot];
  let savedLayout = site.image_layouts?.[slot] || SiteImage.defaults();
  let layout = { ...savedLayout };
  let file, localUrl, removing, drag;
  card.querySelector(".image-heading").textContent = title;
  frame.dataset.slot = slot;
  frame.setAttribute("role", "img");
  frame.setAttribute("aria-label", title + "取景预览");
  if (slot === "logo") frame.firstElementChild.textContent = "票";

  function clearFile() {
    if (localUrl) URL.revokeObjectURL(localUrl);
    file = localUrl = null;
    fileInput.value = "";
  }
  card.addEventListener("dispose", clearFile);

  function updatePanControls() {
    const travel = SiteImage.travel(frame);
    for (const range of ranges) {
      if (range.dataset.key !== "zoom") range.disabled = Math.abs(travel[range.dataset.key]) < 1;
    }
    const canPan = Math.abs(travel.x) >= 1 || Math.abs(travel.y) >= 1;
    frame.classList.toggle("can-pan", canPan && !removing);
    card.querySelector(".image-hint").textContent = canPan
      ? "拖动图片可移动位置，也可用滑块微调。"
      : "放大图片后，可在预览框中拖动取景。";
  }
  frame.addEventListener("load", updatePanControls, true);

  function refresh() {
    const source = localUrl || (!removing && savedSource) || defaultSource;
    const dirty = file || removing || Object.keys(layout).some(key => layout[key] !== savedLayout[key]);
    SiteImage.render(frame, source, layout);
    updatePanControls();
    fit.value = layout.fit;
    ranges.forEach(range => {
      range.value = layout[range.dataset.key];
      range.nextElementSibling.value = range.value + "%";
    });
    card.querySelector(".image-adjust").disabled = !source || Boolean(removing);
    choose.textContent = localUrl || (savedSource && !removing) ? "更换图片" : "选择图片";
    remove.disabled = !savedSource || Boolean(removing);
    cancel.disabled = save.disabled = !dirty;
    const message = removing ? "待保存 · 将恢复默认外观" : file ? "待保存 · " + file.name :
      dirty ? "待保存 · 取景已修改" : savedSource ? "正在使用自定义图片" : "正在使用默认外观";
    if (status.textContent !== message) status.textContent = message;
    status.classList.remove("error");
  }

  function showImageError(error) {
    status.textContent = error.message;
    status.classList.add("error");
  }
  choose.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const candidate = fileInput.files[0];
    if (!candidate) return;
    fileInput.value = "";
    if (candidate.size > 5 * 1024 * 1024) return showImageError(new Error("图片超过 5 MB，请选择较小的文件。"));
    if (!/\.(png|jpe?g|webp)$/i.test(candidate.name)) {
      return showImageError(new Error("请选择 PNG、JPEG 或 WebP 图片。"));
    }
    const candidateUrl = URL.createObjectURL(candidate);
    card.disabled = true;
    try {
      const image = new Image();
      image.src = candidateUrl;
      await image.decode();
      if (!card.isConnected) { URL.revokeObjectURL(candidateUrl); return; }
      if (image.naturalWidth < 32 || image.naturalHeight < 32) throw new Error("图片尺寸需要至少 32 × 32 像素。");
      clearFile();
      file = candidate;
      localUrl = candidateUrl;
      removing = false;
      refresh();
    } catch (error) {
      URL.revokeObjectURL(candidateUrl);
      showImageError(error.name === "EncodingError" ? new Error("此图片无法预览，请重新选择文件。") : error);
    } finally { card.disabled = false; }
  });
  fit.addEventListener("change", () => { layout.fit = fit.value; refresh(); });
  ranges.forEach(range => range.addEventListener("input", () => {
    layout[range.dataset.key] = range.valueAsNumber;
    refresh();
  }));
  frame.addEventListener("pointerdown", event => {
    if (card.disabled || !frame.classList.contains("can-pan") || !event.isPrimary || event.button !== 0) return;
    drag = { x: layout.x, y: layout.y, left: event.clientX, top: event.clientY, travel: SiteImage.travel(frame) };
    frame.setPointerCapture(event.pointerId);
  });
  frame.addEventListener("pointermove", event => {
    if (!drag || !frame.hasPointerCapture(event.pointerId)) return;
    for (const [axis, delta] of [["x", event.clientX - drag.left], ["y", event.clientY - drag.top]]) {
      if (Math.abs(drag.travel[axis]) >= 1) {
        layout[axis] = Math.max(0, Math.min(100, Math.round(drag[axis] + 100 * delta / drag.travel[axis])));
      }
    }
    refresh();
  });
  frame.addEventListener("lostpointercapture", () => { drag = null; });
  card.querySelector(".image-reset").addEventListener("click", () => { layout = SiteImage.defaults(); refresh(); });
  cancel.addEventListener("click", () => {
    clearFile();
    removing = false;
    layout = { ...savedLayout };
    refresh();
  });
  remove.addEventListener("click", () => {
    clearFile();
    removing = true;
    layout = SiteImage.defaults();
    refresh();
  });
  save.addEventListener("click", async () => {
    card.disabled = true;
    save.textContent = "正在保存…";
    try {
      let response;
      const url = "/v1/admin/site/image";
      if (removing) {
        response = await adminFetch(url + "/" + slot, { method: "DELETE" });
      } else if (file) {
        const data = new FormData();
        data.append("slot", slot);
        data.append("image", file, file.name);
        data.append("layout", JSON.stringify(layout));
        response = await adminFetch(url, { method: "POST", body: data });
      } else {
        response = await adminFetch(url + "/" + slot + "/layout", {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(layout),
        });
      }
      const updated = await response.json();
      savedSource = updated.assets[slot];
      savedLayout = updated.image_layouts[slot];
      clearFile();
      removing = false;
      layout = { ...savedLayout };
      refresh();
      showToast(title + "已保存，刷新首页即可查看。");
    } catch (error) { showImageError(error); }
    finally { card.disabled = false; save.textContent = "保存图片设置"; }
  });
  refresh();
  return card;
}

async function loadConsole() {
  const [rulesResponse, siteResponse] = await Promise.all([
    adminFetch("/v1/admin/rules"),
    adminFetch("/v1/admin/site"),
  ]);
  renderRules(await rulesResponse.json());
  const site = await siteResponse.json();
  renderTexts(site);
  imageManager.replaceChildren(createImageEditor("logo", "站点 Logo", site), createImageEditor("hero", "首页展示图", site));
  loginPanel.hidden = true;
  adminConsole.hidden = false;
  logoutButton.hidden = false;
  if (location.hash === "#images") document.getElementById("images").scrollIntoView();
}

loginForm.addEventListener("submit", async event => {
  event.preventDefault();
  loginError.textContent = "";
  authorization = basicCredential(
    document.querySelector("#admin-username").value.trim(),
    document.querySelector("#admin-password").value,
  );
  try {
    await loadConsole();
    sessionStorage.setItem(credentialKey, authorization);
    document.querySelector("#admin-password").value = "";
  } catch (error) {
    authorization = "";
    sessionStorage.removeItem(credentialKey);
    loginError.textContent = error.message;
  }
});

logoutButton.addEventListener("click", () => {
  authorization = "";
  sessionStorage.removeItem(credentialKey);
  imageManager.querySelectorAll(".image-card").forEach(card => card.dispatchEvent(new Event("dispose")));
  imageManager.replaceChildren();
  adminConsole.hidden = true;
  loginPanel.hidden = false;
  logoutButton.hidden = true;
});

document.querySelector("#save-rules").addEventListener("click", async () => {
  try {
    const cards = [...document.querySelectorAll(".rule-card")];
    for (const card of cards) {
      await saveRuleCard(card);
    }
    const response = await adminFetch("/v1/admin/rules");
    renderRules(await response.json());
    showToast("规则已保存，新的发票识别立即生效。");
  } catch (error) { showToast(error.message, true); }
});

document.querySelector("#new-rule").addEventListener("click", () => {
  addRuleCard({ enabled: true });
  ruleList.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "center" });
  ruleList.lastElementChild?.querySelector(".rule-name")?.focus();
});

async function deleteRule(card) {
  const ruleId = card.dataset.ruleId;
  if (!ruleId) {
    card.remove();
    return;
  }
  try {
    await adminFetch(`/v1/admin/rules/${encodeURIComponent(ruleId)}`, { method: "DELETE" });
    card.remove();
    showToast("自定义规则已删除。");
  } catch (error) { showToast(error.message, true); }
}

document.querySelector("#save-site-texts").addEventListener("click", async () => {
  const texts = {};
  document.querySelectorAll("[data-site-key]").forEach(input => { texts[input.dataset.siteKey] = input.value.trim(); });
  try {
    const response = await adminFetch("/v1/admin/site", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texts }),
    });
    renderTexts(await response.json());
    showToast("界面文案已保存。");
  } catch (error) { showToast(error.message, true); }
});

if (authorization) {
  loadConsole().catch(() => {
    authorization = "";
    sessionStorage.removeItem(credentialKey);
  });
}
