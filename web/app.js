const form = document.querySelector("#invoice-form");
const fileInput = document.querySelector("#file-input");
const dropZone = document.querySelector("#drop-zone");
const fileList = document.querySelector("#file-list");
const fileEmpty = document.querySelector("#file-empty");
const previewButton = document.querySelector("#preview-button");
const exportButton = document.querySelector("#export-button");
const printButton = document.querySelector("#print-button");
const registerButton = document.querySelector("#register-button");
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
};
const profileKey = "invoice-reimbursement-profile-v1";
let selectedFiles = [];
let previewValid = false;
let previewData = null;
let confirmedReviewIds = new Set();
let explanationNotes = new Map();
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
  previewData = null;
  confirmedReviewIds.clear();
  exportButton.disabled = true;
  printButton.disabled = true;
  registerButton.disabled = true;
  resultPanel.hidden = true;
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
  data.append("reimbursement_type", fields.reimbursement_type.value);
  if (previewData) {
    data.append("edits", JSON.stringify(previewData.records.map(record => ({
      record_id: record.record_id, pdf_name: record.pdf_name, source_digest: record.source_digest,
      item_name: record.item_name, specification: record.specification,
      invoice_number: record.invoice_number, invoice_date: record.invoice_date,
      amount: record.amount, excluded: !!record.excluded,
      confirmed: confirmedReviewIds.has(record.record_id),
    }))));
    data.append("explanations", JSON.stringify([...explanationNotes].map(([rule_id, note]) => ({rule_id, note}))));
  }
  data.append("claimant", fields.claimant.value.trim());
  data.append("fill_date", fields.fill_date.value);
  if (includeProfile) {
    Object.entries(fields).forEach(([name, input]) => {
      if (!["reimbursement_type", "claimant", "fill_date"].includes(name)) data.append(name, input.value.trim());
    });
    data.append("confirmed_review_ids", JSON.stringify([...confirmedReviewIds]));
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
  previewData = data;
  confirmedReviewIds.clear();
  explanationNotes.clear();
  resultPanel.hidden = false;
  summaryCards.replaceChildren();
  resultNotices.replaceChildren();
  resultBody.replaceChildren();

  addSummaryCard("上传文件", `${data.input_count} 个`);
  addSummaryCard("识别发票页", `${data.pdf_count} 页`);
  addSummaryCard("票面合计", data.amount_total ? `¥ ${data.amount_total}` : "无法计算");

  if (data.split_pdf_names?.length) {
    addNotice(`已将合并 PDF 按页拆分：${data.split_pdf_names.join("、")}`, "info");
  }
  addNotice("可直接修改票面字段，改完勾选确认。删除只移出本次填报，原始文件不会被修改。", "info");
  if (data.non_pdf_entries?.length) {
    addNotice(`ZIP 中已忽略非 PDF 文件：${data.non_pdf_entries.join("、")}`, "warning");
  }

  for (const record of data.records || []) {
    const row = document.createElement("tr");
    row.dataset.recordId = record.record_id;
    record.original_invoice_number = record.invoice_number;
    const filename = document.createElement("td");
    filename.textContent = record.pdf_name;
    if (record.preview_image) {
      const view = document.createElement("button");
      view.type = "button";
      view.className = "text-button";
      view.textContent = "查看票面";
      view.addEventListener("click", () => {
        document.querySelector("#invoice-image").src = record.preview_image;
        document.querySelector("#invoice-dialog").showModal();
      });
      filename.append(view);
    }
    function editable(cell, field, label, multiline = false) {
      const input = document.createElement(multiline ? "textarea" : "input");
      input.className = "invoice-edit";
      input.value = record[field] ?? "";
      input.setAttribute("aria-label", `${record.pdf_name} ${label}`);
      if (field === "amount") { input.inputMode = "decimal"; input.maxLength = 12; }
      else input.maxLength = field === "item_name" ? 6000 : field === "specification" ? 3000 : 30;
      input.addEventListener("input", () => {
        record[field] = input.value;
        record.edited = true;
        confirmedReviewIds.delete(record.record_id);
        checkbox.checked = false;
        refreshClassification(record, reimbursementType, remarks);
        renderExplanations();
        updateExportState();
      });
      cell.append(input);
    }
    const numberLabel = document.createElement("small");
    numberLabel.textContent = "发票号码";
    filename.append(numberLabel);
    editable(filename, "invoice_number", "发票号码");
    const dateLabel = document.createElement("small");
    dateLabel.textContent = "开票日期";
    filename.append(dateLabel);
    editable(filename, "invoice_date", "开票日期");
    const item = document.createElement("td");
    editable(item, "item_name", "项目名称", true);
    const specification = document.createElement("td");
    editable(specification, "specification", "规格型号（票面空白请留空）", true);
    const reimbursementType = document.createElement("td");
    const amount = document.createElement("td");
    amount.className = "amount";
    editable(amount, "amount", "价税合计（小写）");
    const remarks = document.createElement("td");
    refreshClassification(record, reimbursementType, remarks);
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
      const confirmation = document.createElement("label");
      confirmation.className = "manual-review-confirmation";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.recordId = record.record_id;
      const text = document.createElement("span");
      text.textContent = "我已对照票面，确认项目、规格、号码和金额无误";
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          confirmedReviewIds.add(record.record_id);
          pill.textContent = "已人工确认";
          pill.className = "status-pill success";
        } else {
          confirmedReviewIds.delete(record.record_id);
          pill.textContent = record.status;
          pill.className = "status-pill warning";
        }
        updateExportState();
      });
      confirmation.append(checkbox, text);
      status.append(confirmation);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-button remove-invoice";
      remove.textContent = "删除这张发票";
      remove.addEventListener("click", () => {
        record.excluded = !record.excluded;
        row.classList.toggle("excluded", record.excluded);
        row.querySelectorAll(".invoice-edit").forEach(input => { input.disabled = record.excluded; });
        checkbox.disabled = record.excluded;
        remove.textContent = record.excluded ? "恢复这张发票" : "删除这张发票";
        renderExplanations();
        updateExportState();
      });
      status.append(remove);
    row.append(filename, item, specification, reimbursementType, amount, remarks, status);
    resultBody.append(row);
  }

  renderExplanations();
  updateExportState();
  resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function matchingRules(record) {
  return (previewData?.rules || []).filter(rule => rule.enabled && rule.keywords.some(keyword => keyword && (record.item_name || "").includes(keyword)));
}

function refreshClassification(record, typeCell, remarks) {
  const rules = matchingRules(record);
  typeCell.textContent = rules.find(rule => rule.reimbursement_type)?.reimbursement_type || fields.reimbursement_type.value;
  remarks.replaceChildren();
  remarks.className = rules.some(rule => rule.remark || rule.template_url) ? "remarks-cell" : "";
  rules.forEach(rule => {
    if (!rule.remark && !rule.template_url) return;
    const line = document.createElement("span");
    line.textContent = rule.remark || "请填写说明";
    remarks.append(line);
    if (rule.template_url || ["courier", "transport", "cable"].includes(rule.id)) {
      const edit = document.createElement("a");
      edit.href = `#explanation-${rule.id}`;
      edit.textContent = "在线填写说明";
      remarks.append(edit);
    }
    if (rule.template_url) {
      const original = document.createElement("a");
      original.href = rule.template_url; original.target = "_blank"; original.rel = "noreferrer";
      original.textContent = "原附件";
      remarks.append(original);
    }
  });
}

function renderExplanations() {
  const container = document.querySelector("#explanation-editors");
  container.replaceChildren();
  const defaults = new Map((previewData?.explanations || []).map(item => [item.rule_id, item.note]));
  const active = new Set();
  for (const rule of previewData?.rules || []) {
    if (!rule.template_url && !["courier", "transport", "cable"].includes(rule.id)) continue;
    const records = previewData.records.filter(record => !record.excluded && matchingRules(record).some(item => item.id === rule.id));
    if (!records.length) continue;
    active.add(rule.id);
    const section = document.createElement("section");
    section.className = "explanation-editor";
    section.id = `explanation-${rule.id}`;
    const heading = document.createElement("h3"); heading.textContent = `${rule.title}说明`;
    const details = document.createElement("p"); details.className = "explanation-details";
    details.textContent = records.map(record => `发票号码：${record.invoice_number || "待补充"}；项目名称：${record.item_name}；规格型号：${record.specification || "（票面空白）"}；价税合计：${record.amount || "待补充"}元`).join("\n");
    const label = document.createElement("label"); label.className = "field";
    const title = document.createElement("span"); title.textContent = "补充用途和事由（打印时自动带上上方明细、说明人和日期）";
    const input = document.createElement("textarea"); input.rows = 5; input.maxLength = 10000;
    input.setAttribute("aria-label", `${rule.title}说明内容`);
    input.value = explanationNotes.get(rule.id) ?? defaults.get(rule.id) ?? "上述费用用于【请填写具体用途和事由】，特此说明。";
    explanationNotes.set(rule.id, input.value);
    input.addEventListener("input", () => explanationNotes.set(rule.id, input.value));
    label.append(title, input); section.append(heading, details, label); container.append(section);
  }
  for (const id of explanationNotes.keys()) if (!active.has(id)) explanationNotes.delete(id);
  document.querySelector("#explanation-section").hidden = active.size === 0;
}

function updateExportState() {
  if (!previewData) {
    previewValid = false;
    exportButton.disabled = true;
    printButton.disabled = true;
    registerButton.disabled = true;
    return;
  }
  const active = previewData.records.filter(record => !record.excluded);
  const counts = new Map();
  active.forEach(record => counts.set(record.invoice_number, (counts.get(record.invoice_number) || 0) + 1));
  const pending = active.filter(record => ((record.needs_review || record.edited) && !confirmedReviewIds.has(record.record_id)) || !record.item_name.trim() || !/^\d{8,30}$/.test(record.invoice_number) || !/^\d+(\.\d{1,2})?$/.test(String(record.amount ?? "")));
  const duplicates = active.filter(record => (record.duplicate_history && record.invoice_number === record.original_invoice_number) || counts.get(record.invoice_number) > 1);
  const ready = active.length > 0 && pending.length === 0;
  previewValid = ready && !duplicates.length;
  exportButton.disabled = !previewValid;
  registerButton.disabled = !previewValid;
  printButton.disabled = !ready;
  summaryCards.replaceChildren();
  addSummaryCard("本次保留", `${active.length} 张`);
  addSummaryCard("已删除", `${previewData.records.length - active.length} 张`);
  const cents = active.reduce((sum, record) => sum + Math.round(Number(record.amount || 0) * 100), 0);
  addSummaryCard("票面合计", Number.isFinite(cents) ? `¥ ${(cents / 100).toFixed(2)}` : "待补充");
  previewData.records.forEach(record => {
    const pill = resultBody.querySelector(`[data-record-id="${record.record_id}"] .status-pill`);
    const duplicate = duplicates.includes(record);
    pill.textContent = record.excluded ? "已移出本次" : duplicate ? "重复发票，请移除" : confirmedReviewIds.has(record.record_id) ? "已人工确认" : pending.includes(record) ? "请修改并确认" : "已识别";
    pill.className = `status-pill ${!record.excluded && (duplicate || pending.includes(record)) ? "warning" : "success"}`;
  });
  if (previewValid) {
    resultBadge.textContent = "校验通过，可以导出";
    resultBadge.className = "result-badge success";
  } else if (duplicates.length) {
    resultBadge.textContent = `发现 ${duplicates.length} 张重复票，请移除；已报销记录仍可打印`;
    resultBadge.className = "result-badge warning";
  } else if (pending.length) {
    resultBadge.textContent = `请完成 ${pending.length} 张发票的人工核对`;
    resultBadge.className = "result-badge warning";
  } else {
    resultBadge.textContent = "需要补充或人工核对";
    resultBadge.className = "result-badge warning";
  }
}

async function previewInvoices(event) {
  event.preventDefault();
  if (!validateBeforeRequest(false)) return;
  invalidatePreview();
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

async function printInvoices() {
  if (!previewData || !validateBeforeRequest(false)) return;

  // 在点击事件中先创建窗口，避免异步生成 PDF 后被浏览器当作弹窗拦截。
  const printWindow = window.open("", "_blank");
  if (!printWindow) {
    showToast("浏览器拦截了打印窗口，请允许本站弹出窗口后重试。", true);
    return;
  }
  printWindow.document.title = "正在准备打印文件";
  printWindow.document.body.textContent = "正在合并发票与对应说明文件，请稍候……";

  printButton.disabled = true;
  printButton.classList.add("loading");
  try {
    const response = await fetch("/v1/invoices/print", {
      method: "POST",
      body: buildFormData(false),
    });
    if (!response.ok) throw new Error(await parseError(response));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    let printTriggered = false;
    const triggerPrint = () => {
      if (printTriggered || printWindow.closed) return;
      printTriggered = true;
      try {
        printWindow.focus();
        printWindow.print();
      } catch {
        showToast("打印文件已打开，请使用浏览器中的打印按钮。", false);
      }
    };
    printWindow.onload = () => {
      window.setTimeout(triggerPrint, 900);
    };
    printWindow.location.replace(url);
    // 部分浏览器的内置 PDF 查看器不会触发普通页面的 load 事件。
    window.setTimeout(triggerPrint, 2500);
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    showToast("已合并全部发票和对应说明，正在打开打印窗口。", false);
  } catch (error) {
    printWindow.close();
    showToast(error.message || "打印文件生成失败。", true);
  } finally {
    printButton.disabled = !previewData;
    printButton.classList.remove("loading");
  }
}

async function registerHistory() {
  if (!previewValid || !validateBeforeRequest(true)) return;
  if (!window.confirm("确认这些发票已经报销？登记后再次上传将提醒重复。误登记可在历史中撤销。")) return;
  registerButton.disabled = true;
  try {
    const response = await fetch("/v1/invoices/history/register", {method: "POST", body: buildFormData(true)});
    if (!response.ok) throw new Error(await parseError(response));
    const payload = await response.json();
    previewData.records.filter(record => !record.excluded).forEach(record => {
      record.duplicate_history = true;
      record.original_invoice_number = record.invoice_number;
    });
    updateExportState();
    showToast(`已登记 ${payload.registered_count} 张发票。`);
    await loadHistory();
  } catch (error) { showToast(error.message || "登记失败。", true); updateExportState(); }
}

async function loadHistory() {
  const identity = {student_id: fields.student_id.value.trim(), phone: fields.phone.value.trim()};
  if (!identity.student_id || !identity.phone) return showToast("请先填写学号和电话，用于查询你的历史。", true);
  try {
    const response = await fetch("/v1/invoices/history/query", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(identity),
    });
    if (!response.ok) throw new Error(await parseError(response));
    const data = await response.json();
    const body = document.querySelector("#history-body"); body.replaceChildren();
    for (const record of data.records) {
      const row = document.createElement("tr");
      [record.invoice_number, record.item_name, `¥ ${Number(record.amount).toFixed(2)}`, new Date(record.exported_at).toLocaleString()].forEach(value => {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      });
      const actions = document.createElement("td");
      const undo = document.createElement("button"); undo.type = "button"; undo.className = "text-button"; undo.textContent = "撤销误登记";
      undo.addEventListener("click", async () => {
        if (!window.confirm(`撤销发票 ${record.invoice_number} 的报销登记？`)) return;
        try {
          const response = await fetch("/v1/invoices/history/undo", {
            method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...identity, invoice_number: record.invoice_number}),
          });
          if (!response.ok) throw new Error(await parseError(response));
          if (previewData) {
            previewData.records.filter(item => item.invoice_number === record.invoice_number).forEach(item => { item.duplicate_history = false; });
            updateExportState();
          }
          await loadHistory();
          showToast("已撤销误登记。");
        } catch (error) { showToast(error.message, true); }
      });
      actions.append(undo); row.append(actions); body.append(row);
    }
    if (!data.records.length) {
      const row = document.createElement("tr"); const cell = document.createElement("td"); cell.colSpan = 5; cell.textContent = "暂无已登记的报销记录。"; row.append(cell); body.append(row);
    }
    const panel = document.querySelector("#history-panel"); panel.hidden = false;
    panel.scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) { showToast(error.message || "历史查询失败。", true); }
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
      print_button: "#print-button-text",
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
printButton.addEventListener("click", printInvoices);
registerButton.addEventListener("click", registerHistory);
document.querySelector("#load-history").addEventListener("click", loadHistory);
document.querySelector("#refresh-history").addEventListener("click", loadHistory);
document.querySelector("#save-profile").addEventListener("click", saveProfile);
document.querySelector("#clear-profile").addEventListener("click", () => {
  localStorage.removeItem(profileKey);
  fields.claimant.value = "";
  fields.student_id.value = "";
  fields.phone.value = "";
  profileMessage.textContent = "已清除当前浏览器保存的个人信息。";
  showToast("已清除保存的信息。")
});
fields.reimbursement_type.addEventListener("change", invalidatePreview);

const today = new Date();
const localToday = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
fields.fill_date.value = localToday;
loadProfile();
renderFiles();
loadSiteSettings();
