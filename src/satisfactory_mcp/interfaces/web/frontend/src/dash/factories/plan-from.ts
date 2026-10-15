/* "make a plan": store a new plan that mirrors this factory as built, then open it in the planner. */

import { send } from "../../api/client";
import { go } from "../../app/nav";
import { button } from "../../kit/dashkit";
import { fail, friendlyError, notify } from "../../kit/toast";

import type { PlanFromFactoryResponse } from "../../api/shapes";

export function planFromFactoryButton(name: string): HTMLButtonElement {
  const b = button(
    "make a plan",
    function () {
      b.disabled = true;
      send<PlanFromFactoryResponse>("POST", "/api/factories/plan", { factory: name })
        .then(function (reply) {
          const differs = reply.differences.length;
          notify("“" + reply.name + "” made" + (differs ? "; " + differs + " differences from the build are in its notes" : ""));
          go("planner/" + reply.key);
        })
        .catch(function (failure) {
          b.disabled = false;
          fail("making a plan from “" + name + "”: " + friendlyError(failure));
        });
    },
    { title: "store a new plan that mirrors this factory as built, and open it in the planner" }
  );
  return b;
}
