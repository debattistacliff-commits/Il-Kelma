const buddy = document.querySelector("#buddy");
const say = document.querySelector("#say");
const menu = document.querySelector("#menu");
const buttons = [...document.querySelectorAll(".rb")];
const colors = {rest:"#1E7BEA", listen:"#0FA3C9", think:"#7A4FE0", idea:"#E39A00",
  read:"#1F9E6E", done:"#239B56", laugh:"#F0623C", party:"#0D8BD9",
  shy:"#E0559A", oops:"#5B6B80", sad:"#5A7BA8", sleep:"#3B3F9A"};
let bridge, state, down, dragging;
const put = (element, x, y) => {
  element.style.left = x + "px";
  element.style.top = y + "px";
};
const clamp = (value, limit) => Math.max(0, Math.min(value, limit));

function buttonPositions(state, width, height) {
  const radius = 168;
  const step = Math.PI / 10;
  const halfButton = 25;
  const inset = halfButton + 5;
  const cx = state.x + 52;
  const cy = state.y + 52;
  const direction = state.side * state.vertical;
  const preferred = state.side > 0 ? 0 : Math.PI;
  const arc = start => Array.from({length: 6}, (_, i) => {
    const angle = start + direction * i * step;
    return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
  });
  const withComments = points => {
    const [firstX, firstY] = points[0];
    const [lastX, lastY] = points[5];
    const angle = Math.atan2(firstY + lastY - 2 * cy, firstX + lastX - 2 * cx);
    return [...points, [cx + 106 * Math.cos(angle), cy + 106 * Math.sin(angle)]];
  };
  const fits = points => points.every(([x, y]) =>
    x >= inset - 1e-8 && x <= width - inset + 1e-8 &&
    y >= inset - 1e-8 && y <= height - inset + 1e-8);
  const toCorners = points => points.map(([x, y]) => [x - halfButton, y - halfButton]);

  const preferredArc = withComments(arc(preferred));
  if (fits(preferredArc)) return toCorners(preferredArc);

  // Any feasible rotation begins or ends where a button meets an inset edge.
  const edgeAngles = [];
  for (const edge of [inset, width - inset]) {
    const angle = Math.acos((edge - cx) / radius);
    if (Number.isFinite(angle)) edgeAngles.push(angle, -angle);
  }
  for (const edge of [inset, height - inset]) {
    const angle = Math.asin((edge - cy) / radius);
    if (Number.isFinite(angle)) edgeAngles.push(angle, Math.PI - angle);
  }
  const starts = [];
  for (const angle of edgeAngles) {
    for (let i = 0; i < 6; i++) {
      starts.push(angle - direction * i * step);
    }
  }
  starts.sort((a, b) => Math.cos(b - preferred) - Math.cos(a - preferred));
  for (const start of starts) {
    const points = withComments(arc(start));
    if (fits(points)) return toCorners(points);
  }

  // On cramped work areas, take the closest grid slots clear of Buddy and his name.
  const spacing = 60;
  const slots = [];
  const gridStart = (center, limit) => {
    const span = spacing * Math.floor((limit - 2 * inset) / spacing);
    return inset + clamp(center - span / 2 - inset, limit - span - 2 * inset);
  };
  for (let y = gridStart(cy, height); y <= height - inset; y += spacing) {
    for (let x = gridStart(cx, width); x <= width - inset; x += spacing) {
      if (x + inset <= state.x || x - inset >= state.x + 110 ||
          y + inset <= state.y || y - inset >= state.y + 122) {
        slots.push([x, y]);
      }
    }
  }
  const distance = ([x, y]) => Math.hypot(x - cx, y - cy);
  slots.sort((a, b) => distance(a) - distance(b));
  return toCorners(slots.slice(0, 7));
}

function render(next) {
  state = next;
  document.body.classList.toggle("open", state.open);
  document.body.classList.toggle("clipped", state.clipped);
  document.querySelectorAll(".mood").forEach(el => el.classList.toggle("active", el.id === "m-" + state.mood));
  document.querySelectorAll(".flag").forEach(el => el.style.display = el.dataset.code === state.language ? "block" : "none");
  const clipTip = document.querySelector("#b-clip .tip");
  clipTip.innerHTML = state.clipped ? "Unclip<br><b>Aqla'</b>" : "Clip here<br><b>Waħħal hawn</b>";
  const comments = document.querySelector("#b-comments");
  comments.setAttribute("aria-pressed", state.comments);
  comments.querySelector(".tip").innerHTML = state.comments
    ? "Stop reading comments<br><b>Waqqaf il-qari</b>"
    : "Read comments<br><b>Aqra l-kummenti</b>";
  document.querySelector("#comments-icon").textContent = state.comments ? "■" : "💬▶";
  document.querySelector("#comments-badge").hidden = !state.commentsReading;
  document.querySelector("#listen-badge").hidden = !state.listenActive;
  document.querySelector("#listen-learn").setAttribute("aria-pressed", state.listenActive);
  buttons.forEach(button => button.setAttribute("aria-label", button.querySelector(".tip").innerText));
  const name = document.querySelector("#name");
  name.textContent = state.name;
  put(buddy, state.x, state.y);
  put(name, state.x, state.y + 100);
  put(document.querySelector("#clipbadge"), state.x + 78, state.y + 17);
  if (state.open) {
    const positions = buttonPositions(state, innerWidth, innerHeight);
    buttons.forEach((button, i) => {
      button.style.display = positions[i] ? "" : "none";
      if (positions[i]) put(button, ...positions[i]);
    });
  }
  say.textContent = state.message;
  say.setAttribute("aria-label", state.commentsReading ? "Stop reading comments" : state.message);
  document.querySelector("#comments").textContent = state.comments
    ? "Stop reading comments / Waqqaf il-qari"
    : "Read new comments here / Aqra l-kummenti l-ġodda";
  say.style.borderColor = colors[state.mood] || colors.rest;
  say.hidden = !state.message;
  put(say, clamp(state.x - 72, innerWidth - say.offsetWidth),
      clamp(state.vertical < 0
        ? state.y - say.offsetHeight - (state.open ? 166 : 8)
        : state.y + (state.open ? 268 : 120), innerHeight - say.offsetHeight));
  menu.hidden = !state.context;
  put(menu, clamp(state.x + 105, innerWidth - menu.offsetWidth),
      clamp(state.y, innerHeight - menu.offsetHeight));
  reportRegions();
}

function reportRegions() {
  if (!state || !bridge) return;
  const elements = [buddy, document.querySelector("#name")];
  if (state.clipped) elements.push(document.querySelector("#clipbadge"));
  if (!say.hidden) elements.push(say);
  if (!menu.hidden) elements.push(menu);
  if (state.open) elements.push(...buttons.filter(button => button.style.display !== "none"),
                                ...document.querySelectorAll(".rb:hover .tip"));
  bridge.regions(elements.map(el => {
    // offset geometry includes the full size even during the open animation.
    if (el.classList.contains("tip")) {
      const r = el.getBoundingClientRect();
      return [Math.floor(r.x), Math.floor(r.y), Math.ceil(r.width), Math.ceil(r.height)];
    }
    return [el.offsetLeft - 5, el.offsetTop - 5, el.offsetWidth + 10, el.offsetHeight + 10];
  }));
}

function act(key) {
  if (bridge) bridge.command(key);
}
buttons.forEach(el => {
  el.addEventListener("mouseenter", () => {
    const tip = el.querySelector(".tip");
    const r = el.getBoundingClientRect();
    tip.style.transform = "none";
    tip.style.left = clamp(r.left + (r.width - tip.offsetWidth) / 2, innerWidth - tip.offsetWidth) - r.left + "px";
    tip.style.bottom = r.top >= tip.offsetHeight + 6 ? "58px" : "-40px";
    reportRegions();
  });
  el.addEventListener("mouseleave", reportRegions);
});
say.addEventListener("click", () => act(state.commentsReading ? "stop-comments" : "dismiss"));
document.addEventListener("contextmenu", event => {
  event.preventDefault();
  act("context");
});
buddy.addEventListener("pointerdown", event => {
  if (event.button !== 0 || !state) return;
  buddy.setPointerCapture(event.pointerId);
  down = [event.screenX, event.screenY];
  dragging = false;
});
buddy.addEventListener("pointermove", event => {
  if (!down || dragging || state.clipped) return;
  if (Math.abs(event.screenX - down[0]) + Math.abs(event.screenY - down[1]) > 6) {
    dragging = true;
    buddy.classList.add("dragging");
    act("drag");
  }
});
buddy.addEventListener("pointerup", () => {
  if (down) act(dragging ? "drop" : "menu");
  down = null;
  buddy.classList.remove("dragging");
});
buddy.addEventListener("dblclick", event => {
  event.preventDefault();
  act("pad");
});
buddy.addEventListener("lostpointercapture", () => {
  down = null;
  buddy.classList.remove("dragging");
});
window.addEventListener("resize", () => {
  if (state) render(state);
});
new QWebChannel(qt.webChannelTransport, channel => {
  bridge = channel.objects.buddy;
  bridge.ready();
});
