"""The UE struct grammar, including every shape that silently breaks naive parsers."""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.gamedata.uestruct import (
    UeStructError,
    amount,
    as_list,
    obj_class,
    parse_struct,
)


def test_keyed_list_becomes_dict():
    assert parse_struct("(A=1,B=2)") == {"A": "1", "B": "2"}


def test_positional_list_becomes_list():
    assert parse_struct('("x","y")') == ["x", "y"]


def test_nested_single_entry_list():
    # ((A=1)) is a LIST of one struct, but (A=1) is a bare struct. as_list unifies.
    assert parse_struct("((A=1))") == [{"A": "1"}]
    assert as_list(parse_struct("(A=1)")) == [{"A": "1"}]


def test_commas_and_parens_inside_quotes_are_not_delimiters():
    raw = '(ItemClass="/Game/A.B_C(1),x",Amount=6)'
    parsed = parse_struct(raw)
    assert parsed["Amount"] == "6"
    assert parsed["ItemClass"] == "/Game/A.B_C(1),x"


def test_whitespace_after_delimiter():
    # 16 fields in the real dump use ", " between entries.
    assert parse_struct("( A=1, B=2 )") == {"A": "1", "B": "2"}


def test_empty_struct():
    assert parse_struct("()") == {}
    assert parse_struct("") is None
    assert parse_struct(None) is None


def test_real_json_fields_pass_through():
    # mUnlocks / mSchematicDependencies / mFuel arrive as real JSON, not strings.
    payload = [{"Class": "BP_UnlockRecipe_C"}]
    assert parse_struct(payload) is payload


def test_omitted_amount_defaults_to_zero():
    # UE omits struct members equal to their default: Schematic_Goat_C.mCost has no
    # Amount at all, so indexing would raise.
    entry = parse_struct('((ItemClass="/Game/X.Desc_Y_C"))')[0]
    assert "Amount" not in entry
    assert amount(entry) == 0.0


def test_obj_class_handles_both_reference_shapes():
    wrapped = (
        "\"/Script/Engine.BlueprintGeneratedClass'/Game/FactoryGame/Resource/"
        "Parts/Rubber/Desc_Rubber.Desc_Rubber_C'\""
    )
    assert obj_class(wrapped) == "Desc_Rubber_C"
    assert obj_class("/Game/A/Build_OilRefinery.Build_OilRefinery_C") == "Build_OilRefinery_C"
    # raw native names appear 51 times in mProducedIn
    assert obj_class("/Script/FactoryGame.FGBuildGun") == "FGBuildGun"
    assert obj_class(None) is None
    assert obj_class("") is None


def test_unterminated_input_raises():
    with pytest.raises(UeStructError):
        parse_struct("(A=1")
