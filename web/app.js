const form = document.querySelector("#invoice-form");
const fileInput = document.querySelector("#file-input");
const dropZone = document.querySelector("#drop-zone");
const fileList = document.querySelector("#file-list");
const fileEmpty = document.querySelector("#file-empty");
const previewButton = document.querySelector("#preview-button");
const exportButton = document.querySelector("#export-button");
const resultPanel = document.querySelector("#result-panel");
const resultBadge = document.querySelector("#result-badge");
const resultBody = document.querySelector("#result-body");
const summaryCards = document.querySelector("#summary-cards");
const resultNotices = document.querySelector("#result-notices");
const toast = document.querySelector("#toast");
const profileMessage = document.querySelector("#profile-message");

const fields = {
  claimant: document.querySelector("#claimant"),
  student_id: document.querySelector("#student-id"),
  phone: document.querySelector("#phone"),
  fill_date: document.querySelector("#fill-date"),
  reimbursement_type: document.querySelector("#reimbursement-type"),
  transmit_invoice: document.querySelector("#transmit-invoice"),
  expected_total: document.querySelector("#expected-total"),
};
const profileKey = "invoice-reimbursement-profile-v1";
let selectedFiles = [];
let previewValid = false;
let toastTimer;

function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  toastTimer = setTimeout(() => { toast.className = "toast"; }, 4200);
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function fileKey(file) {
  return `${file.name.toLowerCase()}::${file.size}::${file.lastModified}`;
}

function invalidatePreview() {
  previewValid = false;
  exportButton.disabled = true;
}

function addFiles(files) {
  const allowed = [".pdf", ".zip"];
  const existing = new Set(selectedFiles.map(fileKey));
  let rejected = 0;
  for (const file of files) {
    const suffix = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!allowed.includes(suffix)) {
      rejected += 1;
      continue;
    }
    if (!existing.has(fileKey(file))) {
      selectedFiles.push(file);
      existing.add(fileKey(file));
    }
  }
  if (rejected) showToast(`已忽略 ${rejected} 个非 PDF/ZIP 文件。`, true);
  invalidatePreview();
  renderFiles();
}

function renderFiles() {
  fileList.replaceChildren();
  fileEmpty.hidden = selectedFiles.length > 0;
  selectedFiles.forEach((file, index) => {
    const item = document.createElement("li");
    item.className = "file-item";

    const type = document.createElement("span");
    type.className = "file-type";
    type.textContent = file.name.toLowerCase().endsWith(".zip") ? "ZIP" : "PDF";

    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.name;
    name.title = file.name;

    const size = document.createElement("span");
    size.className = "file-size";
    size.textContent = formatSize(file.size);

    const remove = document.createElement("button");
    remove.className = "remove-file";
    remove.type = "button";
    remove.setAttribute("aria-label", `移除 ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      selectedFiles.splice(index, 1);
      invalidatePreview();
      renderFiles();
    });
    item.append(type, name, size, remove);
    fileList.append(item);
  });
}

function buildFormData(includeProfile = false) {
  const data = new FormData();
  selectedFiles.forEach(file => data.append("files", file, file.name));
  if (fields.expected_total.value.trim()) data.append("expected_total", fields.expected_total.value.trim());
  data.append("reimbursement_type", fields.reimbursement_type.value);
  if (includeProfile) {
    Object.entries(fields).forEach(([name, input]) => {
      if (name !== "expected_total" && name !== "reimbursement_type") data.append(name, input.value.trim());
    });
  }
  return data;
}

function validateBeforeRequest(includeProfile = false) {
  if (!selectedFiles.length) {
    showToast("请先选择至少一个 PDF 或 ZIP 文件。", true);
    dropZone.focus();
    return false;
  }
  if (includeProfile && !form.reportValidity()) return false;
  return true;
}

async function parseError(response) {
  let payload;
  try { payload = await response.json(); } catch { return `请求失败（HTTP ${response.status}）`; }
  const detail = payload.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  if (Array.isArray(detail)) return detail.map(item => item.msg).join("；");
  return `请求失败（HTTP ${response.status}）`;
}

function addSummaryCard(label, value) {
  const card = document.createElement("div");
  card.className = "summary-card";
  const name = document.createElement("span");
  name.textContent = label;
  const number = document.createElement("strong");
  number.textContent = value ?? "—";
  card.append(name, number);
  summaryCards.append(card);
}

function addNotice(message, kind = "info") {
  const notice = document.createElement("div");
  notice.className = `notice ${kind}`;
  notice.textContent = message;
  resultNotices.append(notice);
}

function renderResult(data) {
  resultPanel.hidden = false;
  summaryCards.replaceChildren();
  resultNotices.replaceChildren();
  resultBody.replaceChildren();

  addSummaryCard("上传文件", `${data.input_count} 个`);
  addSummaryCard("识别发票页", `${data.pdf_count} 页`);
  addSummaryCard("票面合计", data.amount_total ? `¥ ${data.amount_total}` : "无法计算");
  addSummaryCard("预期金额", data.expected_total ? `¥ ${data.expected_total}` : "未填写");

  if (data.split_pdf_names?.length) {
    addNotice(`已将合并 PDF 按页拆分：${data.split_pdf_names.join("、")}`, "info");
  }
  if (!data.expected_total) {
    addNotice("请填写预期总金额并重新预览，金额核对通过后才能导出 Excel。", "warning");
  }
  (data.errors || []).forEach(message => addNotice(message, "error"));
  if (data.non_pdf_entries?.length) {
    addNotice(`ZIP 中已忽略非 PDF 文件：${data.non_pdf_entries.join("、")}`, "warning");
  }

  for (const record of data.records || []) {
    const row = document.createElement("tr");
    const filename = document.createElement("td");
    filename.textContent = record.pdf_name;
    const item = document.createElement("td");
    item.textContent = record.item_name || "（空）";
    const specification = document.createElement("td");
    specification.textContent = record.specification || "（空）";
    const reimbursementType = document.createElement("td");
    reimbursementType.textContent = record.reimbursement_type || "材料费";
    const amount = document.createElement("td");
    amount.className = "amount";
    amount.textContent = record.amount ? `¥ ${record.amount}` : "未识别";
    const remarks = document.createElement("td");
    remarks.className = record.remarks?.length ? "remarks-cell" : "";
    (record.remarks || []).forEach((message, index) => {
      const line = document.createElement(record.template_urls?.[index] ? "a" : "span");
      line.textContent = message;
      if (record.template_urls?.[index]) {
        line.href = record.template_urls[index];
        line.target = "_blank";
        line.rel = "noreferrer";
        line.title = "点击打开说明模板";
      }
      remarks.append(line);
    });
    const status = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = `status-pill ${record.needs_review ? "warning" : "success"}`;
    pill.textContent = record.status;
    status.append(pill);
    if (record.review_reasons?.length) {
      const reasons = document.createElement("div");
      reasons.className = "review-reasons";
      reasons.textContent = record.review_reasons.join("；");
      status.append(reasons);
    }
    row.append(filename, item, specification, reimbursementType, amount, remarks, status);
    resultBody.append(row);
  }

  previewValid = Boolean(data.can_export);
  exportButton.disabled = !previewValid;
  resultBadge.textContent = previewValid ? "校验通过，可以导出" : "需要补充或人工核对";
  resultBadge.className = `result-badge ${previewValid ? "success" : "warning"}`;
  resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function previewInvoices(event) {
  event.preventDefault();
  if (!validateBeforeRequest(false)) return;
  previewButton.disabled = true;
  previewButton.classList.add("loading");
  try {
    const response = await fetch("/v1/invoices/preview", {
      method: "POST",
      body: buildFormData(false),
    });
    if (!response.ok) throw new Error(await parseError(response));
    const data = await response.json();
    renderResult(data);
    showToast(`识别完成：共 ${data.pdf_count} 个发票页。`);
  } catch (error) {
    invalidatePreview();
    showToast(error.message || "识别失败。", true);
  } finally {
    previewButton.disabled = false;
    previewButton.classList.remove("loading");
  }
}

async function exportInvoices() {
  if (!previewValid || !validateBeforeRequest(true)) return;
  exportButton.disabled = true;
  exportButton.classList.add("loading");
  try {
    const response = await fetch("/v1/invoices/export", {
      method: "POST",
      body: buildFormData(true),
    });
    if (!response.ok) throw new Error(await parseError(response));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "发票报销填报表.xlsx";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("Excel 已生成并开始下载。");
  } catch (error) {
    showToast(error.message || "导出失败。", true);
  } finally {
    exportButton.disabled = !previewValid;
    exportButton.classList.remove("loading");
  }
}

function saveProfile() {
  const profile = {
    claimant: fields.claimant.value.trim(),
    student_id: fields.student_id.value.trim(),
    phone: fields.phone.value.trim(),
    reimbursement_type: fields.reimbursement_type.value.trim(),
    transmit_invoice: fields.transmit_invoice.value,
  };
  localStorage.setItem(profileKey, JSON.stringify(profile));
  profileMessage.textContent = "已保存在当前浏览器中；其他电脑和其他浏览器不会共用。";
  showToast("个人信息已保存。下一次打开会自动填写。");
}

function loadProfile() {
  try {
    const saved = JSON.parse(localStorage.getItem(profileKey) || "null");
    if (!saved) return;
    Object.entries(saved).forEach(([name, value]) => {
      if (fields[name] && typeof value === "string") fields[name].value = value;
    });
    profileMessage.textContent = "已载入此浏览器保存的个人信息。";
  } catch {
    localStorage.removeItem(profileKey);
  }
}

async function loadSiteSettings() {
  try {
    const response = await fetch("/v1/site", { cache: "no-store" });
    if (!response.ok) return;
    const site = await response.json();
    const textTargets = {
      site_name: "#site-name",
      site_tagline: "#site-tagline",
      hero_kicker: "#hero-kicker",
      hero_title: "#hero-title",
      hero_description: "#hero-description",
      upload_title: "#upload-title",
      upload_primary: "#upload-primary",
      upload_secondary: "#upload-secondary",
      profile_title: "#profile-title",
      action_title: "#action-title",
      action_description: "#action-description",
      preview_button: "#preview-button-text",
      export_button: "#export-button-text",
    };
    Object.entries(textTargets).forEach(([key, selector]) => {
      const element = document.querySelector(selector);
      if (element && site.texts?.[key]) element.textContent = site.texts[key];
    });
    if (site.texts?.site_name) document.title = site.texts.site_name;

    const logo = document.querySelector("#site-logo");
    const logoFallback = document.querySelector("#logo-fallback");
    if (site.assets?.logo) {
      logo.src = site.assets.logo;
      logo.hidden = false;
      logoFallback.hidden = true;
    }
    const heroImage = document.querySelector("#hero-image");
    if (site.assets?.hero) {
      heroImage.src = site.assets.hero;
      heroImage.hidden = false;
      document.querySelector(".hero").classList.add("has-image");
    }
  } catch {
    // 管理配置加载失败时保留页面内置文案，不影响发票处理。
  }
}

fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});
dropZone.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => {
  event.preventDefault();
  dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
}));
dropZone.addEventListener("drop", event => addFiles(event.dataTransfer.files));
form.addEventListener("submit", previewInvoices);
exportButton.addEventListener("click", exportInvoices);
document.querySelector("#save-profile").addEventListener("click", saveProfile);
document.querySelector("#clear-profile").addEventListener("click", () => {
  localStorage.removeItem(profileKey);
  fields.claimant.value = "";
  fields.student_id.value = "";
  fields.phone.value = "";
  profileMessage.textContent = "已清除当前浏览器保存的个人信息。";
  showToast("已清除保存的信息。")
});
fields.expected_total.addEventListener("input", invalidatePreview);
fields.reimbursement_type.addEventListener("change", invalidatePreview);

const today = new Date();
const localToday = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
fields.fill_date.value = localToday;
loadProfile();
renderFiles();
loadSiteSettings();
