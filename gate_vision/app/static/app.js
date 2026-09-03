const elements = {
  availability: document.querySelector("#availability"),
  cameraStatus: document.querySelector("#camera-status"),
  frame: document.querySelector("#camera-frame"),
  coverState: document.querySelector("#cover-state"),
  visionState: document.querySelector("#vision-state"),
  controllerTarget: document.querySelector("#controller-target"),
  detected: document.querySelector("#detected-state"),
  motion: document.querySelector("#motion"),
  travel: document.querySelector("#travel"),
  fault: document.querySelector("#fault"),
  message: document.querySelector("#message"),
  buttons: [...document.querySelectorAll("button[data-target]")],
};

let lastFrameTime = null;

function endpoint(path) {
  return new URL(path, window.location.href).toString();
}

function setControlsDisabled(disabled) {
  for (const button of elements.buttons) button.disabled = disabled;
}

function render(status) {
  const available = Boolean(status.available);
  const busy = Boolean(status.is_moving || status.controller_active);
  elements.availability.textContent = available ? "Available" : "Unavailable";
  elements.availability.classList.toggle("offline", !available);
  elements.cameraStatus.textContent = status.camera_online ? "Online" : "Offline";
  // The cover state is what Home Assistant shows. The rest is diagnostics.
  elements.coverState.textContent = available ? status.cover_state || "unknown" : "unavailable";
  elements.visionState.textContent = status.vision_state || "UNKNOWN";
  elements.controllerTarget.textContent = status.target_state || "None";
  elements.detected.textContent = status.raw_state || "UNKNOWN";
  elements.motion.textContent = status.is_moving ? "Moving" : "Stationary";
  elements.travel.textContent =
    status.travel === null || status.travel === undefined
      ? "Unknown"
      : `${Math.round(status.travel * 100)}% open`;
  elements.fault.textContent = status.fault ? status.fault_reason || "Fault" : "None";
  setControlsDisabled(!available || busy);

  if (status.last_camera_frame_time && status.last_camera_frame_time !== lastFrameTime) {
    lastFrameTime = status.last_camera_frame_time;
    elements.frame.src = `${endpoint("api/frame")}?at=${encodeURIComponent(lastFrameTime)}`;
  }
}

async function refresh() {
  try {
    const response = await fetch(endpoint("api/status"), { cache: "no-store" });
    if (!response.ok) throw new Error(`Status request failed (${response.status})`);
    render(await response.json());
    if (elements.message.dataset.error === "connection") elements.message.textContent = "";
  } catch (error) {
    elements.availability.textContent = "Unavailable";
    elements.availability.classList.add("offline");
    setControlsDisabled(true);
    elements.message.dataset.error = "connection";
    elements.message.textContent = error.message;
  }
}

async function requestGate(target) {
  setControlsDisabled(true);
  elements.message.textContent = `Requesting ${target.toLowerCase()}…`;
  try {
    const response = await fetch(endpoint(`api/request/${target}`), { method: "POST" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    elements.message.textContent = `${target === "OPEN" ? "Open" : "Close"} request accepted`;
  } catch (error) {
    elements.message.textContent = error.message;
  } finally {
    await refresh();
  }
}

for (const button of elements.buttons) {
  button.addEventListener("click", () => requestGate(button.dataset.target));
}

refresh();
window.setInterval(refresh, 250);
