/* The Settings tab, `dash=settings[/maps]`: every setting as a row, and the maps section. */

import { appendNote, button, fieldError, selectBox, subTabs } from "../kit/dashkit";
import { make } from "../kit/dom";
import { hashFor } from "../map/map";
import { go } from "../app/nav";
import { resetSettings, setSetting, settingChoice, settingNumber, settingOn, SETTINGS } from "../app/settings";
import { requestRender } from "./actions";
import { mapPickerRow, renderMaps } from "./maps/settings-maps";

import type { Amount, Setting } from "../app/settings";

const SETTINGS_SUBS: [string, string][] = [
  ["", "general"],
  ["maps", "maps"],
];

function numberControl(setting: Amount): HTMLElement {
  const least = setting.min;
  const most = setting.max;
  const input = make("input", "dash-number");
  input.type = "number";
  input.min = String(least);
  input.max = String(most);
  input.step = "1";
  input.value = String(settingNumber(setting.key));
  input.onchange = function () {
    const value = Number(input.value);
    if (Number.isInteger(value) && value >= least && value <= most) {
      fieldError(input, "");
      setSetting(setting.key, value);
    } else fieldError(input, "a whole number from " + least + " to " + most);
  };
  const cell = make("span", "dash-setting-ctl");
  cell.appendChild(input);
  return cell;
}

function settingRow(setting: Setting): HTMLElement {
  const row = make("label", "dash-setting");
  const words = make("span", "dash-setting-text");
  words.appendChild(make("span", "dash-setting-k", setting.label));
  words.appendChild(make("span", "dash-setting-hint", setting.hint));
  row.appendChild(words);
  if (setting.kind === "switch") {
    const box = make("input");
    box.type = "checkbox";
    box.checked = settingOn(setting.key);
    box.onchange = function () {
      setSetting(setting.key, box.checked);
    };
    row.appendChild(box);
  } else if (setting.kind === "choice") {
    row.appendChild(
      selectBox(
        setting.options,
        settingChoice(setting.key),
        function (value) {
          setSetting(setting.key, value);
        },
        { label: setting.label }
      )
    );
  } else row.appendChild(numberControl(setting));
  return row;
}

function renderGeneral(body: HTMLElement): void {
  const card = make("section", "dash-card");
  const bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", "settings"));
  bar.appendChild(
    button(
      "reset to defaults",
      function () {
        resetSettings();
        requestRender();
      },
      { title: "put every setting back to its default" }
    )
  );
  card.appendChild(bar);
  appendNote(card, "kept in this browser, except those chat uses too: those every tab and chat share");
  let group = "";
  SETTINGS.forEach(function (setting) {
    if (setting.group !== group) {
      group = setting.group;
      card.appendChild(make("h3", "dash-setting-group", group));
    }
    card.appendChild(settingRow(setting));
  });
  // The last SETTINGS group is "map"; the default picker joins it.
  card.appendChild(mapPickerRow());
  body.appendChild(card);
}

export function renderSettingsTab(body: HTMLElement, subject: string): void {
  const section = subject === "maps" ? "maps" : "";
  body.appendChild(
    subTabs(
      SETTINGS_SUBS.map(function (entry) {
        return { id: entry[0], label: entry[1], href: hashFor(entry[0] ? "settings/" + entry[0] : "settings") };
      }),
      section,
      function (id) {
        go(id ? "settings/" + id : "settings");
      },
      "Settings sections"
    )
  );
  if (section === "maps") renderMaps(body);
  else renderGeneral(body);
}
