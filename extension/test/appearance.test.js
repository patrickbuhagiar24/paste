// The side panel is read at a glance, so its palette is checked, not just
// eyeballed: every text pair must reach WCAG AA, the action buttons must stay
// distinguishable from each other and from plain rows, and the stylesheet must
// style the states the panel actually sets (selection, Drive state, status
// tone, themes).
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const css = fs.readFileSync(path.join(__dirname, "../sidepanel.css"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../sidepanel.html"), "utf8");
const script = fs.readFileSync(path.join(__dirname, "../sidepanel.js"), "utf8");

// --- a small WCAG 2.x contrast calculator ---------------------------------

function luminance(hex) {
  const raw = hex.replace("#", "");
  const full = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw;
  assert.equal(full.length, 6, "not a colour: " + hex);
  const [r, g, b] = [0, 2, 4].map((i) => {
    const channel = parseInt(full.slice(i, i + 2), 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

function block(selector) {
  const start = css.indexOf(selector + " {");
  assert.notEqual(start, -1, "missing rule: " + selector);
  return css.slice(start, css.indexOf("}", start));
}

function variable(name, scope) {
  const match = new RegExp("--" + name + ":\\s*(#[0-9a-fA-F]{3,6})").exec(scope);
  assert.ok(match, "missing variable --" + name);
  return match[1];
}

// The custom properties of one theme: the :root block for light, and the
// block inside the dark media query (where the light :root is overridden).
function theme(name) {
  if (name === "dark") {
    const media = css.indexOf("@media (prefers-color-scheme: dark)");
    assert.notEqual(media, -1, "the dark theme is missing");
    const open = css.indexOf("{", media);
    const close = css.indexOf("\n}", open);
    assert.ok(open !== -1 && close !== -1, "the dark theme block is not closed");
    return css.slice(open, close);
  }
  const start = css.indexOf(":root {");
  assert.notEqual(start, -1, "the light theme is missing");
  return css.slice(start, css.indexOf("\n}", start));
}

// --- the palette ----------------------------------------------------------

function coloursFor(name) {
  const scope = theme(name);
  const root = theme("light");
  // A theme overrides only what it needs; everything else is inherited from
  // the :root block, exactly like the cascade.
  const pick = (variableName) => {
    const pattern = "--" + variableName + ":\\s*#[0-9a-fA-F]{3,6}";
    return new RegExp(pattern).test(scope) ? variable(variableName, scope) : variable(variableName, root);
  };
  return {
    ink: pick("ink"),
    muted: pick("muted"),
    bg: pick("bg"),
    surface: pick("surface"),
    borderStrong: pick("border-strong"),
    accent: pick("accent"),
    rowIdle: pick("row-idle"),
    rowHover: pick("row-hover"),
    rowSelected: pick("row-selected"),
    primaryTop: pick("primary-top"),
    primaryBottom: pick("primary-bottom"),
    primaryText: pick("primary-text"),
    danger: pick("danger"),
    dangerTop: pick("danger-top"),
    dangerText: pick("danger-text"),
    focus: pick("focus"),
    focusStrong: pick("focus-strong"),
    guide: pick("guide"),
    rowToggle: pick("row-toggle"),
  };
}

for (const name of ["light", "dark"]) {
  const c = coloursFor(name);

  // Text pairs: body text and row labels are 13px, so AA needs 4.5:1.
  assert.ok(contrast(c.ink, c.bg) >= 7, name + ": ink on the panel background must read at AAA, got " + contrast(c.ink, c.bg).toFixed(2));
  for (const [label, background] of [["idle row", c.rowIdle], ["hovered row", c.rowHover], ["selected row", c.rowSelected]]) {
    const ratio = contrast(c.ink, background);
    assert.ok(ratio >= 7, name + ": the label on a " + label + " must reach AAA, got " + ratio.toFixed(2));
  }
  const mutedRatio = contrast(c.muted, c.bg);
  assert.ok(mutedRatio >= 4.5, name + ": muted text must reach AA, got " + mutedRatio.toFixed(2));
  // The header carries the Drive state and the status line on --surface.
  assert.ok(contrast(c.muted, c.surface) >= 4.5, name + ": the Drive line must reach AA on the header");
  assert.ok(contrast(c.ink, c.surface) >= 4.5, name + ": header text must reach AA on the header");

  // The button borders are the only thing that says "this is a control" on a
  // white panel, so they must reach the 3:1 non-text minimum against both
  // the background and the filled surface.
  for (const [label, background] of [["background", c.bg], ["surface", c.surface]]) {
    const ratio = contrast(c.borderStrong, background);
    assert.ok(ratio >= 3, name + ": secondary borders must reach 3:1 on the " + label + ", got " + ratio.toFixed(2));
  }

  // Filled actions: white text on the colour, at both ends of the gradient.
  for (const [label, background] of [["primary top", c.primaryTop], ["primary bottom", c.primaryBottom]]) {
    const ratio = contrast(c.primaryText, background);
    assert.ok(ratio >= 4.5, name + ": white text on the " + label + " must reach AA, got " + ratio.toFixed(2));
  }
  for (const [label, background] of [["danger top", c.dangerTop], ["danger", c.danger]]) {
    const ratio = contrast(c.dangerText, background);
    assert.ok(ratio >= 4.5, name + ": white text on the " + label + " must reach AA, got " + ratio.toFixed(2));
  }

  // Focus rings are a non-text indicator: 3:1 against what is behind them.
  assert.ok(contrast(c.focus, c.bg) >= 3, name + ": the focus ring needs 3:1 on the background");
  assert.ok(contrast(c.focusStrong, c.surface) >= 3, name + ": the filled-button ring needs 3:1 on the header");

  // The pointer state must be separable from the resting row, and the
  // selected row from the pointed one: all three depend on these two gaps.
  const hoverDelta = contrast(c.rowHover, c.rowIdle);
  assert.ok(hoverDelta >= 1.15, name + ": hovered rows must be clearly apart from idle (got " + hoverDelta.toFixed(2) + ")");
  const selectedFromHover = contrast(c.rowSelected, c.rowHover);
  assert.ok(selectedFromHover >= 1.15, name + ": selected rows must stay apart from hover (got " + selectedFromHover.toFixed(2) + ")");
  // The cursor border and the column guide are non-text indicators.
  assert.ok(contrast(c.accent, c.rowHover) >= 3, name + ": the pointer border needs 3:1 on a hovered row");
  assert.ok(contrast(c.guide, c.bg) >= 2, name + ": the tree guide line must be visible");
  assert.ok(contrast(c.rowToggle, c.rowSelected) >= 3, name + ": the expand arrow must read on a selected row");

  // A selected row must be visibly different from an idle one; a 1.2:1 tint
  // (the old palette) is too easy to miss.
  const selectedDelta = contrast(c.rowSelected, c.rowIdle);
  assert.ok(selectedDelta >= 1.35, name + ": selected rows must stand out (got " + selectedDelta.toFixed(2) + ")");
}

// --- the states the script sets are styled --------------------------------

test("the stylesheet styles every state the side panel sets", () => {
  // Rows carry their kind and selection; every rule below must exist.
  for (const rule of [".row.selected", '.row[data-kind="folder"]', ".row:focus-within .mini"]) {
    assert.ok(css.includes(rule), "missing rule " + rule);
  }
  // Selected rows beat :hover, whatever order the rules are written in.
  const hoverAt = css.indexOf(".row:hover {");
  const selectedAt = css.indexOf(".row.selected,");
  assert.ok(hoverAt !== -1 && selectedAt !== -1 && selectedAt > hoverAt, "the selected rules must follow the hover rule");

  // Drive states and status tones come from sidepanel.js. A state without a
  // rule is a state the user cannot see.
  for (const state of ["ready", "busy", "error"]) {
    assert.ok(css.includes('#drive-status[data-state="' + state + '"]'), "missing Drive state " + state);
    assert.ok(script.includes('"' + state + '"'), "sidepanel.js never sets the Drive state " + state);
  }
  for (const tone of ["ok", "error"]) {
    assert.ok(css.includes('#status[data-state="' + tone + '"]'), "missing status tone " + tone);
    assert.ok(script.includes('", "' + tone + '")'), "sidepanel.js never passes the " + tone + " tone");
  }
  assert.ok(script.includes('setAttribute("data-state"'), "setStatus must mark the status line");
  assert.ok(script.includes('$("selection-count").className'), "the selection count must expose its filled state");
  assert.ok(css.includes("#selection-count.has-items"), "missing the selection chip rule");

  // The intent of an action is expressed in the markup, not guessed by colour.
  assert.ok(html.includes('<button id="editor-save" class="primary">'), "Save must be the primary action");
  assert.ok(html.includes('<button id="btn-delete-selected" class="danger"'), "Delete must be destructive");
  assert.ok(html.includes('id="btn-text" class="primary"'), "New snippet must be a primary action");
});

test("buttons keep a visible boundary and disabled actions step back", () => {
  const button = block("button");
  assert.ok(button.includes("border: 1px solid var(--border-strong)"), "every button needs a visible outline");
  assert.ok(button.includes("font-weight: 600"), "button labels must carry weight");
  assert.ok(css.includes("button.primary"), "missing the primary button style");
  assert.ok(css.includes("button.danger"), "missing the destructive button style");
  assert.ok(css.includes("button.primary:disabled"), "a disabled primary must lose the loud fill");
  assert.ok(css.includes("button:focus-visible"), "buttons need a focus indicator");
  assert.ok(css.includes("prefers-reduced-motion"), "the panel must honour reduced motion");
  // The focus halo must be a ring with a gap, and stronger over a filled
  // primary button where a thin blue outline would disappear.
  const focusStart = css.indexOf("button:focus-visible");
  assert.notEqual(focusStart, -1, "missing the focus rule");
  const focusRule = css.slice(focusStart, css.indexOf("}", focusStart));
  assert.match(focusRule, /outline: 3px solid var\(--focus\)/, "the cursor ring must be 3px");
  assert.match(focusRule, /outline-offset: 2px/, "the ring must leave a gap from the control");
  assert.ok(css.includes("outline-color: var(--focus-strong)"), "filled buttons need the stronger ring");
  assert.ok(css.includes(".children {") && css.includes("border-left: 1px solid var(--guide)"),
    "nested rows need the column guide");
  assert.ok(css.includes("prefers-contrast"), "the panel must honour the contrast preference");
  assert.ok(css.includes("prefers-color-scheme: dark"), "the panel must follow the system theme");
  assert.ok(css.includes("color-scheme: light dark"), "native controls must follow the theme");
});

test("the snippet rows show their actions without hunting for hover", () => {
  assert.ok(css.includes(".row:hover .mini"), "row actions must appear on hover");
  assert.ok(css.includes(".row.selected .mini"), "row actions must appear on the selected row");
  assert.ok(css.includes(".row:focus-within .mini"), "row actions must appear for keyboard focus");
  assert.ok(css.includes(".row .mini:hover:not(:disabled)"), "row actions need a hover state of their own");
});
