import { describe, expect, it } from "vitest";

import { html } from "../../src/kit/html";
import { actSlots } from "../../src/kit/ui/entry";

describe("action slots", () => {
  it("keeps the empty ⋯ slot last", () => {
    expect(actSlots(html`<button>go</button>`).safeHtml).toBe('<div class="acts aslots"><span class="aslot-b"><button>go</button></span><span class="aslot-g"></span></div>');
  });

  it("halves the button slot for a pair and leads with ◎ when asked", () => {
    const out = actSlots({ split: [html`<i>a</i>`, html`<i>b</i>`] }, { pin: "" }).safeHtml;
    expect(out).toBe('<div class="acts aslots"><span class="aslot-g"></span><span class="aslot-s"><i>a</i><i>b</i></span><span class="aslot-g"></span></div>');
  });

  it("drops the ⋯ slot for a list without menus", () => {
    expect(actSlots("", { more: false }).safeHtml).toBe('<div class="acts aslots"><span class="aslot-b"></span></div>');
  });

  it("drops the button slot for a list of glyphs only", () => {
    expect(actSlots(null, { pin: "", more: "" }).safeHtml).toBe('<div class="acts aslots"><span class="aslot-g"></span><span class="aslot-g"></span></div>');
  });
});
