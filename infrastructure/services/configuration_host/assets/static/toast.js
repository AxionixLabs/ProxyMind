export function createToast(stage) {
  let timer, exitTimer, frame;

  function clear() {
    clearTimeout(timer);
    clearTimeout(exitTimer);
    cancelAnimationFrame(frame);
    stage.replaceChildren();
  }

  function element(tag, className, text = "") {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  window.addEventListener("pagehide", clear);

  return function showToast(title, detail, kind = "ok") {
    clear();
    const failed = kind === "err";
    const toast = element("div", failed ? "toast err" : "toast ok");
    toast.setAttribute("role", "status");
    const head = element("div", "toast-head");
    const signal = element("div", "toast-sig");
    signal.append(element("span", "toast-led"), element("span", "", "SIGNAL"));
    head.append(signal, element("div", "toast-mark", failed ? "Alert" : "Confirmed"));
    toast.append(head, element("strong", "toast-title", title), element("span", "toast-body", detail));
    stage.append(toast);
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        toast.classList.add("show");
        timer = setTimeout(() => {
          toast.classList.remove("show");
          exitTimer = setTimeout(() => toast.remove(), 260);
        }, failed ? 4200 : 2400);
      });
    });
  };
}
