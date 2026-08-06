// Preact->React shim for the Claude Design bundle.
//
// The SM64 Trainer UI uses exactly three preact entry points (h, Fragment,
// render) and binds htm to `h`. htm is renderer-agnostic, so binding it to
// React.createElement makes every component in ui/ a genuine React component
// with no source change.
//
// What is NOT free is the DOM dialect. Preact accepts the HTML attribute
// spelling; React only accepts its own prop spelling, and throws outright on
// a string `style`. Every component in ui/ is written in the Preact dialect
// (class=, style="..."), so h() normalizes props on the way through. That
// keeps the translation in ONE place instead of across 45 files.
import * as React from "react";
import { createRoot } from "react-dom/client";

const CUSTOM_PROP = /^--/;

// A CSS declaration list cannot be split on a bare ";" -- a data URI carries
// one of its own ("data:image/png;base64,..."), and the cap art is exactly
// that. Splitting naively shatters every --art/--mask value, which shows up
// as a component rendering in the right COLOUR and the wrong SHAPE.
function splitAtTopLevel(css, separator) {
  const parts = [];
  let depth = 0;
  let quote = null;
  let start = 0;
  for (let index = 0; index < css.length; index++) {
    const ch = css[index];
    if (quote) {
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    else if (ch === separator && depth === 0) {
      parts.push(css.slice(start, index));
      start = index + 1;
    }
  }
  parts.push(css.slice(start));
  return parts;
}

// "color: red; --art: url(data:image/png;base64,AA)" -> { color, "--art" }
function styleObject(css) {
  const out = {};
  for (const decl of splitAtTopLevel(String(css), ";")) {
    if (!decl.trim()) continue;
    const segments = splitAtTopLevel(decl, ":");
    if (segments.length < 2) continue;
    const name = segments[0].trim();
    const value = segments.slice(1).join(":").trim();
    if (!name || !value) continue;
    // React passes custom properties through verbatim; everything else has
    // to arrive camelCased or it is silently dropped.
    out[CUSTOM_PROP.test(name) ? name : name.replace(/-([a-z])/g, (_, ch) => ch.toUpperCase())] = value;
  }
  return out;
}

function reactProps(props) {
  if (!props) return props;
  let translated = null;
  for (const key of Object.keys(props)) {
    const value = props[key];
    let outKey = key;
    let outValue = value;
    if (key === "class") outKey = "className";
    else if (key === "for") outKey = "htmlFor";
    else if (key === "style" && typeof value === "string") outValue = styleObject(value);
    if (outKey !== key || outValue !== value) {
      translated = translated || { ...props };
      delete translated[key];
      translated[outKey] = outValue;
    }
  }
  return translated || props;
}

export const h = (type, props, ...children) => React.createElement(type, reactProps(props), ...children);
export const createElement = h;
export const Fragment = React.Fragment;
export const Component = React.Component;
export const createContext = React.createContext;
export const cloneElement = React.cloneElement;
export const createRef = React.createRef;
export const isValidElement = React.isValidElement;
export const toChildArray = (children) => React.Children.toArray(children);
export const render = (vnode, container) => createRoot(container).render(vnode);
export default { h, createElement, Fragment, Component, createContext, cloneElement, createRef, isValidElement, toChildArray, render };
