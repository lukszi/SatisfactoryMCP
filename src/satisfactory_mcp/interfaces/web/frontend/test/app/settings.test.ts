import { describe, expect, it, vi } from "vitest";

function fakeStorage(initial: Record<string, string>) {
  const data = new Map(Object.entries(initial));
  return {
    data: data,
    getItem: (key: string) => (data.has(key) ? data.get(key)! : null),
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
}

/* settings.ts reads storage while it is imported, so each test loads it afresh. */
async function load(saved?: unknown) {
  vi.resetModules();
  const storage = fakeStorage(saved === undefined ? {} : { settings: typeof saved === "string" ? saved : JSON.stringify(saved) });
  vi.stubGlobal("localStorage", storage);
  return { settings: await import("../../src/app/settings"), storage: storage };
}

describe("reading a setting", () => {
  it("falls back to each setting's default", async () => {
    const { settings } = await load();
    expect(settings.settingOn("spoilers")).toBe(false);
    expect(settings.settingOn("fedOnly")).toBe(true);
    expect(settings.settingChoice("naming")).toBe("short");
    expect(settings.settingNumber("minMachines")).toBe(2);
    expect(settings.spoilerQuery()).toBe("spoilers=0");
  });

  it("answers an unknown key, or one asked as the wrong kind, with nothing", async () => {
    const { settings } = await load();
    expect(settings.settingOn("nonesuch")).toBe(false);
    expect(settings.settingChoice("spoilers")).toBe("");
    expect(settings.settingNumber("naming")).toBe(0);
  });

  it("recalls the stored values that are still valid and drops the rest", async () => {
    const { settings } = await load({ spoilers: true, minMachines: 0, naming: "bogus", follow: "off", paybackHours: 2.5 });
    expect(settings.spoilerFlag()).toBe("1");
    expect(settings.settingNumber("minMachines")).toBe(2);
    expect(settings.settingChoice("naming")).toBe("short");
    expect(settings.settingChoice("follow")).toBe("off");
    expect(settings.settingNumber("paybackHours")).toBe(0);
  });

  it("works without storage, or with storage it cannot read", async () => {
    expect((await load("{not json")).settings.settingOn("fedOnly")).toBe(true);
    vi.resetModules();
    vi.stubGlobal("localStorage", undefined);
    const settings = await import("../../src/app/settings");
    settings.setSetting("spoilers", true);
    expect(settings.settingOn("spoilers")).toBe(true);
    expect(settings.claimSpoilerNotice()).toBe(false);
  });
});

describe("changing a setting", () => {
  it("accepts only a valid value, stores it and tells the listeners", async () => {
    const { settings, storage } = await load();
    const heard = vi.fn();
    settings.onSetting(heard);
    settings.setSetting("minMachines", 3.5);
    settings.setSetting("minMachines", 501);
    settings.setSetting("naming", "long");
    settings.setSetting("nonesuch", true);
    expect(heard).not.toHaveBeenCalled();
    settings.setSetting("minMachines", 5);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(settings.settingNumber("minMachines")).toBe(5);
    expect(JSON.parse(storage.data.get("settings")!)).toEqual({ minMachines: 5 });
  });

  it("writes a shared setting through to the server and keeps it local too", async () => {
    const { settings } = await load();
    const writer = vi.fn();
    settings.writeSharedWith(writer);
    settings.setSetting("spoilers", true);
    expect(writer).not.toHaveBeenCalled();
    settings.setSetting("biomass", true);
    expect(writer).toHaveBeenCalledWith({ biomass: true });
    expect(settings.sharedLocal()).toEqual({ biomass: true });
  });

  it("adopts the server's valid shared values and tells only when one moved", async () => {
    const { settings } = await load();
    const heard = vi.fn();
    settings.onSetting(heard);
    settings.adoptShared({ biomass: true, stage_headroom: "nameplate", payback_hours: 500, spoilers: true });
    expect(heard).toHaveBeenCalledTimes(1);
    expect(settings.settingOn("biomass")).toBe(true);
    expect(settings.settingChoice("stageHeadroom")).toBe("nameplate");
    expect(settings.settingNumber("paybackHours")).toBe(0);
    expect(settings.settingOn("spoilers")).toBe(false);
    settings.adoptShared({ biomass: true });
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("resets to the defaults, clearing every shared one on the server", async () => {
    const { settings, storage } = await load({ spoilers: true, biomass: true });
    const writer = vi.fn();
    settings.writeSharedWith(writer);
    settings.resetSettings();
    expect(settings.settingOn("spoilers")).toBe(false);
    expect(storage.data.get("settings")).toBe("{}");
    const cleared = writer.mock.calls[0]![0] as Record<string, null>;
    expect(cleared.biomass).toBeNull();
    expect(cleared.stage_headroom).toBeNull();
    expect(Object.values(cleared).every((value) => value === null)).toBe(true);
  });
});

describe("the spoiler notice", () => {
  it("is owed once per browser, and never once spoilers were chosen", async () => {
    const first = await load();
    expect(first.settings.claimSpoilerNotice()).toBe(true);
    expect(first.settings.claimSpoilerNotice()).toBe(false);
    const chosen = await load({ spoilers: false });
    expect(chosen.settings.claimSpoilerNotice()).toBe(false);
  });
});
