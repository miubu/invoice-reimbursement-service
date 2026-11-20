(() => {
  function defaults() {
    return { fit: "contain", zoom: 100, x: 50, y: 50 };
  }

  function render(frame, src, layout = defaults()) {
    frame.classList.add("site-image-frame");
    frame.style.setProperty("--image-fit", layout.fit);
    frame.style.setProperty("--image-size", `${layout.zoom}%`);
    frame.style.setProperty("--image-x", `${layout.x}%`);
    frame.style.setProperty("--image-y", `${layout.y}%`);

    let picture = frame.querySelector(".site-image-content");
    if (!src) {
      picture?.remove();
      delete frame.dataset.hasImage;
      return;
    }
    if (picture?.getAttribute("src") === src) return;
    picture?.remove();
    delete frame.dataset.hasImage;
    picture = document.createElement("img");
    picture.alt = "";
    picture.draggable = false;
    picture.className = "site-image-content";
    picture.onload = () => {
      if (picture.parentElement === frame) frame.dataset.hasImage = "true";
    };
    frame.append(picture);
    picture.src = src;
  }

  // 与 CSS 使用相同的缩放，拖动距离按图像实际可移动的宽、高换算。
  function travel(frame) {
    const picture = frame.querySelector(".site-image-content");
    if (!picture?.naturalWidth) return { x: 0, y: 0 };
    const width = frame.clientWidth;
    const height = frame.clientHeight;
    const fit = frame.style.getPropertyValue("--image-fit");
    const ratio = fit === "cover" ? Math.max : Math.min;
    const scale = ratio(width / picture.naturalWidth, height / picture.naturalHeight)
      * parseFloat(frame.style.getPropertyValue("--image-size")) / 100;
    return { x: width - picture.naturalWidth * scale, y: height - picture.naturalHeight * scale };
  }

  window.SiteImage = { defaults, render, travel };
})();
