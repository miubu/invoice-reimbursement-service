async function loadPortalImages() {
  try {
    const response = await fetch("/v1/site", { cache: "no-store" });
    if (!response.ok) return;
    const site = await response.json();
    SiteImage.render(document.querySelector("#portal-logo"), site.assets?.logo, site.image_layouts?.logo);
    SiteImage.render(document.querySelector("#portal-cover-image"), site.assets?.hero || "/assets/invoice-desk.webp", site.image_layouts?.hero);
  } catch {
    // 网络异常时仍可使用首页原有的两个业务入口。
  }
}

loadPortalImages();
