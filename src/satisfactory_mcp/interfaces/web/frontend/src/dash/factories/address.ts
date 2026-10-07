/* A factory page's address, `factories/<name>[/<aspect>]`, and the controls that move it. */

import { button, subTabs } from "../../kit/dashkit";
import { hashFor } from "../../map/map";
import { createPin } from "../../chat/pins";
import { pushDash } from "../../app/nav";
import { WORDS } from "../../kit/words";
import { requestRender } from "../actions";

const ASPECTS: [string, string][] = [
  ["", "overview"],
  ["flows", "flows"],
  ["machines", "machines"],
  ["power", "power"],
  ["nodes", "nodes"],
  ["links", "links"],
  ["floors", "floors"],
  ["sites", "sites"],
];

export interface FactoryAddress {
  name: string;
  aspect: string;
}

function isAspect(id: string): boolean {
  return (
    !!id &&
    ASPECTS.some(function (aspect) {
      return aspect[0] === id;
    })
  );
}

/* A name that itself ends in "/flows" stays whole when `known` says it is a factory. */
export function factoryAddress(subject: string, known?: (name: string) => boolean): FactoryAddress {
  const cut = subject.lastIndexOf("/");
  if (cut > 0) {
    const tail = subject.slice(cut + 1);
    if (isAspect(tail) && !known?.(subject)) return { name: subject.slice(0, cut), aspect: tail };
  }
  return { name: subject, aspect: "" };
}

export function factoryDash(name: string, aspect: string): string {
  return "factories/" + name + (aspect ? "/" + aspect : "");
}

export function factoryPinButton(name: string): HTMLButtonElement {
  return button(
    WORDS.pin,
    function () {
      createPin("factory", { factory: name });
    },
    { title: "pin this factory and copy its pin:N for chat", label: "pin " + name }
  );
}

export function aspectTabs(name: string, aspect: string): HTMLElement {
  return subTabs(
    ASPECTS.map(function (entry) {
      return { id: entry[0], label: entry[1], href: hashFor(factoryDash(name, entry[0])) };
    }),
    aspect,
    function (id) {
      pushDash(factoryDash(name, id));
      requestRender();
    },
    "factory sections"
  );
}
