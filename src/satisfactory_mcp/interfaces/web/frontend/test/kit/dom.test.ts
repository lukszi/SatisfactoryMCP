import { describe, expect, it, vi } from "vitest";

import {
  code,
  COPY_ATTR,
  COPY_CLASS,
  dataButton,
  esc,
  html,
  onAttributeClick,
  popup,
  popupTitleRow,
  TRACE_ATTR,
  traceButtons,
} from "../../src/kit/dom";

describe("markup", () => {
  it("escapes the four characters that end an attribute or start a tag", () => {
    expect(esc('<b class="x">&</b>')).toBe("&lt;b class=&quot;x&quot;&gt;&amp;&lt;/b&gt;");
    expect(esc(42)).toBe("42");
    expect(esc(null)).toBe("null");
  });

  it("escapes popup data by default and passes html() through", () => {
    const table = popup([
      ["name", "<script>"],
      ["count", 3],
      ["link", html("<a>ok</a>")],
      ["dropped", null],
      ["also dropped", undefined],
      ["and this", ""],
    ]);
    expect(table).toBe(
      "<table>" +
        '<tr><td class="popup-key">name</td><td>&lt;script&gt;</td></tr>' +
        '<tr><td class="popup-key">count</td><td>3</td></tr>' +
        '<tr><td class="popup-key">link</td><td><a>ok</a></td></tr>' +
        "</table>"
    );
  });

  it("keeps a zero, which is an answer rather than a gap", () => {
    expect(popup([["count", 0]])).toContain("<td>0</td>");
  });

  it("makes a copyable selector, escaped in every place it appears", () => {
    const plain = code('node:"a"').html;
    expect(plain).toContain('class="' + COPY_CLASS + '"');
    expect(plain).toContain(COPY_ATTR + '="node:&quot;a&quot;"');
    expect(plain).toContain('title="click to copy"');
    expect(plain).toContain(">node:&quot;a&quot;</code>");
    const shown = code("12.5,-3", "x 12, y -3 m").html;
    expect(shown).toContain('title="12.5,-3: click to copy"');
    expect(shown).toContain(">x 12, y -3 m</code>");
  });

  it("builds data buttons with escaped attributes, title and text", () => {
    expect(dataButton({ "data-x": 'a"b' }, "<go>", "t&t")).toBe(
      '<button type="button" class="btn" data-x="a&quot;b" title="t&amp;t">&lt;go&gt;</button>'
    );
    const both = traceButtons("label:Coal").html;
    expect(both.match(new RegExp(TRACE_ATTR + '="label:Coal"', "g"))).toHaveLength(2);
    expect(both).toContain("supply ↑");
    expect(both).toContain("output ↓");
  });

  it("always emits a title row", () => {
    expect(popupTitleRow("building", "Smelter", "Build_Smelter_C")).toEqual(["building", "Smelter"]);
    expect(popupTitleRow("building", null, "Build_Smelter_C")).toEqual(["building", "Build_Smelter_C"]);
    expect(popupTitleRow("building", null, null)[1]).toBe("class not recorded in this projection");
  });
});

describe("onAttributeClick", () => {
  it("hands the nearest carrier of the attribute to the handler and stops the click", () => {
    let listener: ((event: Event) => void) | undefined;
    vi.stubGlobal("document", {
      addEventListener: (_type: string, fn: (event: Event) => void) => {
        listener = fn;
      },
    });
    const handler = vi.fn();
    onAttributeClick("data-find", handler);
    const hit = { id: "hit" };
    const event = {
      target: { closest: (selector: string) => (selector === "[data-find]" ? hit : null) },
      stopPropagation: vi.fn(),
      preventDefault: vi.fn(),
    };
    listener!(event as unknown as Event);
    expect(handler).toHaveBeenCalledWith(hit, event);
    expect(event.stopPropagation).toHaveBeenCalled();

    handler.mockClear();
    listener!({ target: { closest: () => null } } as unknown as Event);
    listener!({ target: null } as unknown as Event);
    expect(handler).not.toHaveBeenCalled();
  });
});
