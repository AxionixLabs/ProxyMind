import { createToast } from "./toast.js";

const $ = id => document.getElementById(id);
let credential = "", reading = null, lastKey = "";
const showFeedback = createToast($("toast-stage"));

const states = {
  idle:["尚未连接","启动客户端监听，设备密钥会自动出现在这里。"],
  connecting:["连接中","正在向服务端建立订阅连接，请稍候。"],
  online:["设备在线","连接已经就绪。复制 API Key，在 Mind Web 中继续工作。"],
  reconnecting:["正在重连","客户端正在恢复连接。已有密钥仍可复制，设备状态以 Web 中显示为准。"],
  stopped:["监听已停止","输入 /listen start 恢复监听，即可继续接收远程任务。"],
  error:["连接未完成","请在客户端执行 /listen status 查看原因，再用 /listen start 重试。"],
  stale:["状态待确认","暂未收到客户端状态更新。请确认客户端仍在运行，并执行 /listen status。"],
};

function render(data) {
  credential = data.credential || "";
  const state = data.updated_at_ms && Date.now() - data.updated_at_ms > 15000 ? "stale" : data.state;
  const [label,detail] = states[state] || states.error;
  $("status-badge").textContent = label;
  $("status-badge").className = `chip status-chip ${state === "online" ? "ready" : ["connecting","reconnecting"].includes(state) ? "busy" : ["error","stale"].includes(state) ? "error" : ""}`;
  $("status-detail").textContent = detail;
  $("key-panel").hidden = !credential;
  $("start-guide").hidden = !!credential;
  $("step-listener").classList.toggle("done",!!data.session_id);
  $("step-key").classList.toggle("done",!!credential);
  $("step-web").classList.toggle("done",!!credential && !!data.web_url);
  if (credential !== lastKey) { $("credential").value = credential; lastKey = credential; conceal(); }
  const link = $("open-web"); link.removeAttribute("href"); link.setAttribute("aria-disabled","true"); link.tabIndex = -1;
  $("service-address").textContent = "等待服务端提供入口";
  if (data.web_url) {
    const url = new URL(data.web_url);
    if (["https:","http:"].includes(url.protocol) && !url.username && !url.password) {
      $("service-address").textContent = url.host;
      if (credential) { link.href = url.href; link.setAttribute("aria-disabled","false"); link.tabIndex = 0; }
    }
  } else if (credential) {
    $("status-detail").textContent = "密钥已获取，但服务端尚未提供 Web 入口。请确认服务端与客户端已同步更新。";
  }
}

async function readAccess() {
  const response = await fetch("/api/agent",{cache:"no-store",credentials:"same-origin"});
  if (!response.ok) throw new Error();
  const body = await response.json(); render(body.data);
}

async function refresh(manual = false) {
  if (reading && !manual) return;
  if (manual) { $("refresh").disabled = true; $("refresh").textContent = "REFRESHING…"; $("refresh").setAttribute("aria-busy","true"); }
  const request = reading || (reading = readAccess());
  try {
    await request;
    if (manual) showFeedback("Status Updated","Client connection status refreshed.");
  } catch {
    $("status-badge").textContent = "本机服务不可用";
    $("status-badge").className = "chip status-chip error";
    $("status-detail").textContent = "无法读取本机连接资料，请确认 Mind 客户端仍在运行。";
    if (manual) showFeedback("Refresh Failed","Unable to read the connection status. Make sure Mind is running.","err");
  } finally {
    if (reading === request) reading = null;
    if (manual) { $("refresh").disabled = false; $("refresh").textContent = "REFRESH"; $("refresh").removeAttribute("aria-busy"); }
  }
}

async function copy(text, message) {
  if (!text) return;
  try { await navigator.clipboard.writeText(text); showFeedback("Copied",message); }
  catch { showFeedback("Copy Failed","Clipboard access is unavailable. Select and copy the text manually.","err"); }
}
function conceal() { $("credential").type = "password"; $("reveal-label").textContent = "显示"; $("reveal").setAttribute("aria-label","显示 API Key"); $("reveal").title = "显示 API Key"; $("reveal").setAttribute("aria-pressed","false"); }
$("reveal").onclick = () => {
  if ($("credential").type === "text") { conceal(); return; }
  $("credential").type = "text"; $("reveal-label").textContent = "隐藏"; $("reveal").setAttribute("aria-label","隐藏 API Key"); $("reveal").title = "隐藏 API Key"; $("reveal").setAttribute("aria-pressed","true");
};
$("copy-key").onclick = () => copy(credential,"API key copied. Paste it in Mind Web to connect.");
for (const button of document.querySelectorAll("[data-copy]")) button.onclick = () => copy(button.dataset.copy,"Command copied. Run it in the Mind client.");
$("refresh").onclick = () => refresh(true);
$("auto-refresh").onchange = () => { if ($("auto-refresh").checked) refresh(); };
document.addEventListener("visibilitychange",() => { conceal(); if (!document.hidden) refresh(); });
refresh();
const poll = () => { if (!document.hidden && $("auto-refresh").checked) refresh(); };
let timer = setInterval(poll,3000);
window.addEventListener("pagehide",() => { conceal(); clearInterval(timer); });
window.addEventListener("pageshow",event => {
  if (!event.persisted) return;
  clearInterval(timer); timer = setInterval(poll,3000); refresh();
});
