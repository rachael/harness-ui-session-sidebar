// Explicitly labeled simulation; never reads local harness state.
const pane = document.querySelector('#pane-enabled');
const drawer = document.querySelector('#drawer-enabled');
const overlay = document.querySelector('#prompt-overlay');
const prompt = document.querySelector('#pinned-prompt');
const toggle = document.querySelector('#expand-toggle');
const status = document.querySelector('#demo-status');
let pinnedOpen = false;
function expanded(open) {
  open = open && drawer.checked;
  overlay.hidden = !open;
  toggle.setAttribute('aria-expanded', String(open));
  prompt.setAttribute('aria-expanded', String(open));
  toggle.textContent = open ? 'Hide full prompt' : 'Show full prompt';
}
toggle.addEventListener('click', () => { pinnedOpen = overlay.hidden; expanded(pinnedOpen); });
prompt.addEventListener('mouseenter', () => expanded(true));
prompt.addEventListener('mouseleave', () => { if (!pinnedOpen) expanded(false); });
prompt.addEventListener('focus', () => expanded(true));
prompt.addEventListener('blur', () => { if (!pinnedOpen) expanded(false); });
prompt.addEventListener('click', () => {
  document.querySelector('#original-prompt').classList.add('jump-target');
  status.textContent = 'Demo: jumped to the original prompt. The composer stays in place.';
  pinnedOpen = false; expanded(false);
});
drawer.addEventListener('change', () => { pinnedOpen = false; expanded(false); toggle.disabled = !drawer.checked; });
pane.addEventListener('change', () => {
  document.querySelector('#info-pane').hidden = !pane.checked;
  panels();
});
document.querySelector('#harness').addEventListener('change', event => {
  document.querySelectorAll('.badge').forEach(badge => { badge.textContent = event.target.value === 'claude' ? '✳' : 'C'; });
  status.textContent = 'Simulation switched harness. The shared content stays the same.';
});
document.addEventListener('keydown', event => { if (event.key === 'Escape') { pinnedOpen = false; expanded(false); } });

const customized = document.querySelector('#customized');
const extra = document.querySelector('#extra-content');
const replace = document.querySelector('#replace-layout');
const tabs = document.querySelector('.layout-tabs');
function mode(value) {
  const extending = value === 'extend';
  for (const button of [extra, replace]) {
    const selected = button === (extending ? extra : replace);
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  document.querySelector('#layout-preview').setAttribute('aria-labelledby', extending ? extra.id : replace.id);
  pane.checked = !extending;
  pane.dispatchEvent(new Event('change'));
  document.querySelector('#statusline-view').textContent = extending
    ? 'Your existing statusline · + 2 tasks · + 1 decision'
    : 'model · project · 2 tasks · 1 decision';
  status.textContent = extending ? 'Demo: keep your layout and add extra content.' : 'Demo: use the full layout.';
}
extra.addEventListener('click', () => mode('extend'));
replace.addEventListener('click', () => mode('replace'));
customized.addEventListener('change', () => {
  tabs.insertBefore(customized.checked ? extra : replace, tabs.firstChild);
  mode(customized.checked ? 'extend' : 'replace');
});
tabs.addEventListener('keydown', event => {
  if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const buttons = [...tabs.querySelectorAll('[role=tab]')];
  const current = buttons.indexOf(document.activeElement);
  const target = event.key === 'Home' ? buttons[0] : event.key === 'End' ? buttons.at(-1) : buttons[(current+1)%buttons.length];
  target.focus(); target.click();
});

const sidebar = document.querySelector('#sidebar-enabled');
const statuslineEnabled = document.querySelector('#statusline-enabled');
function panels() {
  document.querySelector('.native-panels').hidden = !pane.checked && !sidebar.checked;
  document.querySelector('.terminal').classList.toggle('no-pane', !pane.checked && !sidebar.checked);
}
sidebar.addEventListener('change', () => {
  document.querySelector('#session-sidebar').hidden = !sidebar.checked;
  panels();
});
statuslineEnabled.addEventListener('change', () => {
  document.querySelector('#statusline-view').hidden = !statuslineEnabled.checked;
});
const sessionExpand = document.querySelector('#session-expand');
sessionExpand.addEventListener('click', () => {
  const open = sessionExpand.getAttribute('aria-expanded') !== 'true';
  sessionExpand.setAttribute('aria-expanded', String(open));
  sessionExpand.textContent = open ? '▾' : '▸';
  document.querySelector('#session-details').hidden = !open;
});
document.querySelectorAll('.session-select').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.session-select').forEach(row => row.classList.remove('selected-session'));
  button.classList.add('selected-session');
  status.textContent = 'Demo: selected '+button.dataset.session+'. Real switching uses supported terminal boundaries.';
}));
