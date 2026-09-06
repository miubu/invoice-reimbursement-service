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
  (data.rules || []).forEach(rule => {
    const card = document.createElement("article");
    card.className = "rule-card";
    card.dataset.ruleId = rule.id;

    const typeOptions = ["", ...reimbursementTypes].map(value => {
      const label = value || "保持用户默认类型";
      return `<option value="${value}"${value === rule.reimbursement_type ? " selected" : ""}>${label}</option>`;
    }).join("");
    card.innerHTML = `
      <div class="rule-title">
        <div><strong></strong><p></p></div>
        <label class="toggle"><input class="rule-enabled" type="checkbox" ${rule.enabled ? "checked" : ""}> 启用</label>
      </div>
      <div class="rule-grid">
        <label class="field"><span>命中后的报销类型</span><select class="rule-type">${typeOptions}</select></label>
        <label class="field"><span>备注提示</span><input class="rule-remark" maxlength="300"></label>
        <label class="field wide"><span>关键词（每行一个，* 可代表任意文字）</span><textarea class="rule-keywords" maxlength="3100"></textarea></label>
        <label class="field wide"><span>说明模板链接（可为空）</span><input class="rule-url" type="url" maxlength="1000" placeholder="http:// 或 https://"></label>
      </div>`;
    card.querySelector(".rule-title strong").textContent = rule.title;
    card.querySelector(".rule-title p").textContent = rule.description;
    card.querySelector(".rule-remark").value = rule.remark || "";
    card.querySelector(".rule-keywords").value = (rule.keywords || []).join("\n");
    card.querySelector(".rule-url").value = rule.template_url || "";
    ruleList.append(card);
  });
}

function renderSite(site) {
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
    input.maxLength = longField ? 500 : 100;
    label.append(title, input);
    siteTextFields.append(label);
  });
  renderImages(site.assets || {});
}

function renderImages(assets) {
  imageManager.replaceChildren();
  const slots = [{ id: "logo", label: "站点 Logo" }, { id: "hero", label: "首页展示图" }];
  slots.forEach(slot => {
    const card = document.createElement("article");
    card.className = "image-card";
    const title = document.createElement("h3");
    title.textContent = slot.label;
    const preview = document.createElement("div");
    preview.className = "image-preview";
    if (assets[slot.id]) {
      const image = document.createElement("img");
      image.src = assets[slot.id];
      image.alt = slot.label;
      preview.append(image);
    } else {
      preview.textContent = "尚未设置图片";
    }
    const controls = document.createElement("div");
    controls.className = "image-controls";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp";
    const upload = document.createElement("button");
    upload.type = "button";
    upload.className = "text-button";
    upload.textContent = "上传替换";
    upload.addEventListener("click", () => uploadImage(slot.id, input));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button danger-button";
    remove.textContent = "删除图片";
    remove.disabled = !assets[slot.id];
    remove.addEventListener("click", () => removeImage(slot.id));
    controls.append(input, upload, remove);
    card.append(title, preview, controls);
    imageManager.append(card);
  });
}

async function loadConsole() {
  const [rulesResponse, siteResponse] = await Promise.all([
    adminFetch("/v1/admin/rules"),
    adminFetch("/v1/admin/site"),
  ]);
  renderRules(await rulesResponse.json());
  renderSite(await siteResponse.json());
  loginPanel.hidden = true;
  adminConsole.hidden = false;
  logoutButton.hidden = false;
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
  adminConsole.hidden = true;
  loginPanel.hidden = false;
  logoutButton.hidden = true;
});

document.querySelector("#save-rules").addEventListener("click", async () => {
  const rules = [...document.querySelectorAll(".rule-card")].map(card => ({
    id: card.dataset.ruleId,
    reimbursement_type: card.querySelector(".rule-type").value,
    keywords: card.querySelector(".rule-keywords").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
    remark: card.querySelector(".rule-remark").value.trim(),
    template_url: card.querySelector(".rule-url").value.trim(),
    enabled: card.querySelector(".rule-enabled").checked,
  }));
  try {
    const response = await adminFetch("/v1/admin/rules", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    });
    renderRules({ ...(await response.json()), reimbursement_types: reimbursementTypes });
    showToast("规则已保存，新的发票识别立即生效。");
  } catch (error) { showToast(error.message, true); }
});

document.querySelector("#save-site-texts").addEventListener("click", async () => {
  const texts = {};
  document.querySelectorAll("[data-site-key]").forEach(input => { texts[input.dataset.siteKey] = input.value.trim(); });
  try {
    const response = await adminFetch("/v1/admin/site", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texts }),
    });
    renderSite(await response.json());
    showToast("界面文案已保存。");
  } catch (error) { showToast(error.message, true); }
});

async function uploadImage(slot, input) {
  if (!input.files?.length) return showToast("请先选择图片。", true);
  const data = new FormData();
  data.append("slot", slot);
  data.append("image", input.files[0], input.files[0].name);
  try {
    const response = await adminFetch("/v1/admin/site/image", { method: "POST", body: data });
    renderImages((await response.json()).assets || {});
    showToast("图片已上传并替换。");
  } catch (error) { showToast(error.message, true); }
}

async function removeImage(slot) {
  try {
    const response = await adminFetch(`/v1/admin/site/image/${slot}`, { method: "DELETE" });
    renderImages((await response.json()).assets || {});
    showToast("图片已删除。");
  } catch (error) { showToast(error.message, true); }
}

if (authorization) {
  loadConsole().catch(() => {
    authorization = "";
    sessionStorage.removeItem(credentialKey);
  });
}
