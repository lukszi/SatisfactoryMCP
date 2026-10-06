"""What the player has unlocked: recipes, schematics and the buildings they grant."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from ...core.gamedata.model import GameData, Recipe, Schematic
from ...core.saveio.schema import Projection

__all__ = ["UnlockSet"]


@dataclass
class UnlockSet:
    """The unlock gate, read from the save and joined to the shipped recipe dump."""

    projection: Projection
    game: GameData

    @cached_property
    def available_recipe_ids(self) -> set[str]:
        """FGRecipeManager.mAvailableRecipes -- the authoritative unlock gate.

        Deliberately NOT reconstructed from purchased schematics: that over-reports
        by 8 on the reference save, because recipes can arrive via more than one
        schematic (Charcoal/Biocoal come from Compacted Coal, whose own alternate
        schematics are unpurchased).
        """
        return set(self.projection.get("progression", {}).get("available_recipes", ()))

    @cached_property
    def purchased_schematic_ids(self) -> set[str]:
        return set(self.projection.get("progression", {}).get("purchased_schematics", ()))

    @property
    def last_active_schematic(self) -> Schematic | None:
        """The schematic the player last set as their active goal, where the save names one.

        mLastActiveSchematic is what the HUB tracks, so it is "what you were working on"
        and not "what you last bought". ``None`` when the save carries no such pick or the
        dump has no schematic under that class.
        """
        cls = self.projection.get("progression", {}).get("last_active_schematic")
        return self.game.schematics.get(cls or "")

    @cached_property
    def unresolved_recipe_ids(self) -> set[str]:
        """Available recipes with no FGRecipe in Docs.json.

        31 on the reference save: 24 Recipe_Swatch_*, 5 Recipe_Material_*, 2 skins.
        Filtered out rather than surfaced as broken IDs.
        """
        return {
            recipe_id
            for recipe_id in self.available_recipe_ids
            if recipe_id not in self.game.recipes
        }

    def unlocked_recipes(self, kind: str = "part") -> list[Recipe]:
        return [
            self.game.recipes[recipe_id]
            for recipe_id in sorted(self.available_recipe_ids)
            if recipe_id in self.game.recipes and self.game.recipes[recipe_id].kind == kind
        ]

    def has_recipe(self, recipe_id: str) -> bool:
        return recipe_id in self.available_recipe_ids

    @cached_property
    def unlocked_alternates(self) -> list[Recipe]:
        return [r for r in self.unlocked_recipes("part") if r.is_alternate]

    @cached_property
    def locked_alternates(self) -> list[Recipe]:
        return [r for r in self.game.alternates() if r.cls not in self.available_recipe_ids]

    @cached_property
    def unlocked_building_ids(self) -> set[str]:
        """Buildings the player can construct.

        Derived from unlocked BUILDING recipes via their product descriptor, because
        recipe naming is unreliable: Recipe_SmelterMk1_C builds the FOUNDRY.
        """
        building_of_descriptor = {
            b.descriptor: cls for cls, b in self.game.buildings.items() if b.descriptor
        }
        out: set[str] = set()
        for recipe_id in self.available_recipe_ids:
            recipe = self.game.recipes.get(recipe_id)
            if recipe is None or recipe.kind != "building":
                continue
            for product in recipe.products:
                building_id = building_of_descriptor.get(product.item)
                if building_id:
                    out.add(building_id)
        return out

    def can_build(self, building_id: str) -> bool:
        return building_id in self.unlocked_building_ids

    def schematic_recipes(self, schematic: Schematic) -> list[Recipe]:
        """Recipes a schematic grants that the player does not already have.

        Follows BP_UnlockSchematic_C exactly one level, which is needed for
        Quartz Purification -> Silica Distilled. Deeper recursion is wrong: it drags
        in 23 customization schematics.
        """
        recipe_ids = list(schematic.unlocks_recipes)
        for chained in schematic.unlocks_schematics:
            child = self.game.schematics.get(chained)
            if child is not None:
                recipe_ids.extend(child.unlocks_recipes)
        out: list[Recipe] = []
        for recipe_id in dict.fromkeys(recipe_ids):
            recipe = self.game.recipes.get(recipe_id)
            if recipe is not None and recipe_id not in self.available_recipe_ids:
                out.append(recipe)
        return out

    def dependencies_met(self, schematic_id: str) -> tuple[bool, list[str]]:
        """Gate on mSchematicDependencies, never on mTechTier.

        24 of the 109 alternates are blocked behind milestone schematics on the
        reference save. mTechTier is 0 for 71 of them, so it cannot be the gate.
        """
        schematic = self.game.schematics.get(schematic_id)
        if schematic is None:
            return False, [f"unknown schematic {schematic_id}"]
        missing = [
            dependency
            for dependency in schematic.dependencies
            if dependency not in self.purchased_schematic_ids
        ]
        names = [
            self.game.schematics[missing_id].name
            if missing_id in self.game.schematics
            else missing_id
            for missing_id in missing
        ]
        return (not missing), names
