import { describe, expect, it } from "vitest";

import { cls, html, join, marked, trusted } from "../../src/kit/html";

describe("the html builder", () => {
  it("escapes every hole", () => {
    const name = '<img src=x onerror="alert(1)">&';
    expect(html`<b title="${name}">${name}</b>`.safeHtml).toBe('<b title="&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;">&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;</b>');
  });

  it("passes nested markup through once, without escaping it again", () => {
    const inner = html`<i>${"a<b"}</i>`;
    expect(html`<p>${inner}</p>`.safeHtml).toBe("<p><i>a&lt;b</i></p>");
  });

  it("drops null, undefined and false, and joins arrays", () => {
    expect(html`${null}${undefined}${false}${[html`<i></i>`, "x&", 3]}`.safeHtml).toBe("<i></i>x&amp;3");
  });

  it("joins non-empty parts with a separator", () => {
    expect(join(["a", "", null, "b"], trusted(" · ")).safeHtml).toBe("a · b");
  });

  it("marks matches and escapes the rest", () => {
    expect(marked("Iron <Plate> iron", "iron").safeHtml).toBe("<mark>Iron</mark> &lt;Plate&gt; <mark>iron</mark>");
    expect(marked("a<b", "").safeHtml).toBe("a&lt;b");
  });

  it("joins class names, skipping falsy ones", () => {
    expect(cls("entry", false, "bad", null, undefined, "")).toBe("entry bad");
  });
});
