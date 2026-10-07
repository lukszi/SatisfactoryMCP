import { beforeEach, describe, expect, it, vi } from "vitest";

import { copyText, listenForCopies } from "../../src/kit/copy";
import { COPY_ATTR, COPY_CLASS } from "../../src/kit/dom";
import { fail, notify } from "../../src/kit/toast";

vi.mock("../../src/kit/toast", () => ({ fail: vi.fn(), notify: vi.fn() }));

type Listener = (event: unknown) => void;

/* A document with just enough of the API for the fallback copy and for listening. */
function fakeDocument(execCommand: () => boolean) {
  const listeners: Record<string, Listener> = {};
  const pad = { value: "", style: {} as Record<string, string>, setAttribute: vi.fn(), select: vi.fn() };
  const doc = {
    listeners,
    pad,
    createElement: vi.fn(() => pad),
    body: { appendChild: vi.fn(), removeChild: vi.fn() },
    execCommand: vi.fn(execCommand),
    addEventListener: (type: string, fn: Listener) => {
      listeners[type] = fn;
    },
  };
  vi.stubGlobal("document", doc);
  return doc;
}

function copyable(text: string | null, content = "shown"): Element {
  const span = {
    getAttribute: (name: string) => (name === COPY_ATTR ? text : null),
    textContent: content,
  };
  return { closest: (selector: string) => (selector === "." + COPY_CLASS ? span : null) } as unknown as Element;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("copyText", () => {
  it("uses the async clipboard when there is one", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await copyText("node:A");
    expect(writeText).toHaveBeenCalledWith("node:A");
  });

  it("falls back to a hidden textarea, and removes it either way", async () => {
    vi.stubGlobal("navigator", {});
    const doc = fakeDocument(() => true);
    await copyText("label:Coal");
    expect(doc.pad.value).toBe("label:Coal");
    expect(doc.pad.select).toHaveBeenCalled();
    expect(doc.body.removeChild).toHaveBeenCalledWith(doc.pad);
  });

  it("rejects when the browser refuses, or throws", async () => {
    vi.stubGlobal("navigator", {});
    fakeDocument(() => false);
    await expect(copyText("x")).rejects.toThrow("the browser refused to copy");
    const doc = fakeDocument(() => {
      throw new Error("blocked");
    });
    await expect(copyText("x")).rejects.toThrow("the browser refused to copy");
    expect(doc.body.removeChild).toHaveBeenCalled();
  });
});

describe("listenForCopies", () => {
  let doc: ReturnType<typeof fakeDocument>;

  beforeEach(() => {
    doc = fakeDocument(() => true);
    vi.mocked(fail).mockClear();
    vi.mocked(notify).mockClear();
    listenForCopies();
  });

  it("copies what a clicked selector carries and says so", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.resolve() } });
    doc.listeners.click!({ target: copyable("node:BP_ResourceNode26_99") });
    await settle();
    expect(notify).toHaveBeenCalledWith("copied node:BP_ResourceNode26_99");
  });

  it("falls back to the shown text, and reports a refusal", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
    doc.listeners.click!({ target: copyable(null, "label:Coal Power") });
    await settle();
    expect(fail).toHaveBeenCalledWith("could not copy label:Coal Power: denied");
  });

  it("ignores a click outside any selector", async () => {
    doc.listeners.click!({ target: { closest: () => null } });
    doc.listeners.click!({ target: null });
    await settle();
    expect(notify).not.toHaveBeenCalled();
  });

  it("copies on Enter from a focused selector but not from a button", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.resolve() } });
    const preventDefault = vi.fn();
    const focused = (tagName: string) => ({
      ...copyable("node:B"),
      closest: copyable("node:B").closest,
      tagName,
      classList: { contains: (name: string) => name === COPY_CLASS },
    });
    doc.listeners.keydown!({ key: "Enter", target: focused("BUTTON"), preventDefault });
    doc.listeners.keydown!({ key: "a", target: focused("CODE"), preventDefault });
    expect(preventDefault).not.toHaveBeenCalled();
    doc.listeners.keydown!({ key: "Enter", target: focused("CODE"), preventDefault });
    await settle();
    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith("copied node:B");
  });
});
