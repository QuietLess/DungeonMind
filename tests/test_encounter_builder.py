"""M5 encounter builder (docs/ARCHITECTURE.md › M5 contract — encounter builder).

Rules 1–10 are checked as properties of the builder's output on the real 331-creature snapshot
(skipped when it isn't downloaded), plus small hand-built catalogs where exact results can be
checked by hand.
"""

from dataclasses import asdict
from pathlib import Path

import pytest
from isolated import run_isolated
from pydantic import ValidationError

from dungeonmind.rag.ingest import read_jsonl
from dungeonmind.tools import encounter_builder
from dungeonmind.tools.catalog import Catalog, Monster
from dungeonmind.tools.encounter import XP_BUDGET_PER_CHARACTER, Difficulty, format_cr
from dungeonmind.tools.encounter_builder import (
    EncounterRequest,
    build_options,
    primary_role,
    revise,
    roles,
)

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = ROOT / "data/processed/open5e_srd-2024_creatures.json"
CHUNKS = ROOT / "data/processed/srd_5.2.1_chunks.jsonl"

needs_snapshot = pytest.mark.skipif(
    not (SNAPSHOT.exists() and CHUNKS.exists()),
    reason="SRD snapshot or chunks missing (run sync-open5e / ingest)",
)


@pytest.fixture(scope="module")
def full():
    if not (SNAPSHOT.exists() and CHUNKS.exists()):
        pytest.skip("SRD snapshot or chunks missing")
    return Catalog.load(chunks=read_jsonl(CHUNKS))


def _req(levels, difficulty="moderate", **kw) -> EncounterRequest:
    return EncounterRequest(party_levels=levels, difficulty=difficulty, **kw)


def _budget(levels, difficulty: str) -> int:
    i = ["low", "moderate", "high"].index(difficulty)
    return sum(XP_BUDGET_PER_CHARACTER[lvl][i] for lvl in levels)


def _by_name(catalog: Catalog) -> dict[str, Monster]:
    return {m.name: m for m in catalog.monsters}


# Requests exercised for the property checks (rules 1, 3, 4, 5, 9).
REQUESTS = [
    _req([5, 5, 5, 5], "high"),
    _req([5, 5, 5, 5], "high", creature_types=["undead"], exclude_flying=True),
    _req([1, 1, 1, 1], "low"),
    _req([3, 3, 3], "moderate", creature_types=["humanoid", "beast"]),
    _req([10, 9, 8, 11, 10], "high", max_stat_blocks=2),
    _req([20] * 6, "moderate", creature_types=["dragon"]),
    _req([2, 2], "high", max_stat_blocks=1),
]


def _rid(r: EncounterRequest) -> str:
    levels = "L" + ".".join(map(str, r.party_levels))
    return f"{levels}-{r.difficulty.value}-{'+'.join(r.creature_types) or 'any'}"


# --- hand-built catalog (exact, hand-checkable results) ------------------------------------


def _creature(name, cr, xp, attacks=(), legendary=False, fly=0, ctype="undead"):
    actions = [
        {"name": f"Attack {i}", "desc": d, "action_type": "ACTION"} for i, d in enumerate(attacks)
    ]
    if legendary:
        actions.append({"name": "Swipe", "desc": "It attacks.", "action_type": "LEGENDARY_ACTION"})
    return {
        "key": f"test_{name.lower()}",
        "name": name,
        "challenge_rating": cr,
        "experience_points": xp,
        "type": {"key": ctype, "name": ctype.title()},
        "size": {"key": "medium", "name": "Medium"},
        "armor_class": 12,
        "hit_points": 20,
        "speed": {"walk": 30, "fly": fly},
        "speed_all": {"walk": 30, "fly": fly},
        "traits": [],
        "actions": actions,
    }


MELEE = "Melee Attack Roll: +4, reach 5 ft. 6 (1d8 + 2) Slashing damage."
RANGED = "Ranged Attack Roll: +4, range 80/320 ft. 6 (1d8 + 2) Piercing damage."
CONTROL = "Wisdom Saving Throw: DC 12. Failure: the target has the Frightened condition."


def _hand_catalog(*creatures) -> Catalog:
    return Catalog([], list(creatures))


def _names(option) -> set[str]:
    return {m.name for m in option.monsters}


# --- roles -----------------------------------------------------------------------------------


def test_roles_from_hand_built_stat_blocks():
    cat = _hand_catalog(
        _creature("Brute", 2, 450, [MELEE]),
        _creature("Archer", 1, 200, [RANGED]),
        _creature("Skirmisher", 1, 200, [MELEE, RANGED]),
        _creature("Hexer", 1, 200, [MELEE, CONTROL]),
        _creature("Tyrant", 3, 700, [MELEE, CONTROL], legendary=True),
        _creature("Blob", 1, 200, []),
    )
    m = _by_name(cat)
    assert roles(m["Brute"]) == {"melee"}
    assert roles(m["Archer"]) == {"ranged"}
    assert roles(m["Skirmisher"]) == {"melee", "ranged"}
    assert roles(m["Hexer"]) == {"melee", "controller"}
    assert roles(m["Tyrant"]) == {"melee", "controller", "legendary"}
    assert [primary_role(m[n]) for n in ("Brute", "Archer", "Skirmisher", "Hexer", "Tyrant")] == [
        "frontline",
        "ranged",
        "frontline",
        "controller",
        "boss",
    ]
    assert primary_role(m["Blob"]) == "frontline"


def test_saving_throw_without_a_condition_is_not_control():
    plain = "Constitution Saving Throw: DC 13. Failure: 9 (2d8) Necrotic damage."
    cat = _hand_catalog(_creature("Drainer", 1, 200, [MELEE, plain]))
    assert "controller" not in roles(cat.monsters[0])


def test_condition_in_a_trait_with_a_saving_throw_counts():
    record = _creature("Gazer", 1, 200, [MELEE])
    record["traits"] = [
        {
            "name": "Gaze",
            "desc": "Constitution Saving Throw: DC 12. "
            "Failure: the target has the Petrified condition.",
        }
    ]
    assert primary_role(_hand_catalog(record).monsters[0]) == "controller"


@needs_snapshot
@pytest.mark.parametrize(
    ("name", "expected_roles", "primary"),
    [
        # Necrotic Sword (melee) + Necrotic Bow (ranged); Life Drain's save names no condition.
        ("Wight", {"melee", "ranged"}, "frontline"),
        ("Zombie", {"melee"}, "frontline"),
        # Claws: Constitution save or Paralyzed.
        ("Ghoul", {"melee", "controller"}, "controller"),
        ("Mummy", {"melee", "controller"}, "controller"),
        # Has legendary actions.
        ("Adult Red Dragon", {"melee", "legendary"}, "boss"),
        ("Lich", {"melee", "ranged", "legendary"}, "boss"),
    ],
)
def test_roles_of_real_srd_monsters(full, name, expected_roles, primary):
    m = _by_name(full)[name]
    assert roles(m) == expected_roles
    assert primary_role(m) == primary


# --- EncounterRequest validation -----------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"party_levels": []},
        {"party_levels": [5] * 13},
        {"party_levels": [0, 5]},
        {"party_levels": [5, 21]},
        {"party_levels": [5], "max_stat_blocks": 0},
        {"party_levels": [5], "max_stat_blocks": 4},
        {"party_levels": [5], "theme": "x" * 201},
        {"party_levels": [5], "exclude_names": ["Goblin"] * 21},
        {"party_levels": [5], "exclude_names": ["x" * 201]},
        {"party_levels": [5], "difficulty": "deadly"},
        {"party_levels": [5], "habitat": "swamp"},
    ],
)
def test_request_validation_rejects(kwargs):
    with pytest.raises(ValidationError):
        EncounterRequest(**kwargs)


def test_request_accepts_the_limits_and_has_contract_defaults():
    r = EncounterRequest(
        party_levels=[1] * 6 + [20] * 6,
        max_stat_blocks=1,
        theme="x" * 200,
        exclude_names=["x" * 200] * 20,
    )
    assert len(r.party_levels) == 12
    d = EncounterRequest(party_levels=[5])
    assert d.difficulty == Difficulty.MODERATE
    assert d.creature_types == [] and d.exclude_names == []
    assert d.exclude_flying is False and d.max_stat_blocks == 3 and d.theme is None


# --- rule 10: revise -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "direction", "end"),
    [
        ("low", "harder", "moderate"),
        ("moderate", "harder", "high"),
        ("high", "easier", "moderate"),
        ("moderate", "easier", "low"),
    ],
)
def test_10_revise_moves_one_step_and_keeps_other_fields(start, direction, end):
    before = _req(
        [3, 4, 5],
        start,
        creature_types=["undead"],
        exclude_flying=True,
        exclude_names=["Ghoul"],
        max_stat_blocks=2,
        theme="ruined temple",
    )
    snapshot = before.model_dump()
    after = revise(before, direction)
    assert after.difficulty == Difficulty(end)
    assert {**after.model_dump(), "difficulty": None} == {**snapshot, "difficulty": None}
    assert before.model_dump() == snapshot  # the input isn't mutated


@pytest.mark.parametrize(
    ("start", "direction", "limit"), [("low", "easier", "low"), ("high", "harder", "high")]
)
def test_10_revise_past_the_ends_raises_naming_the_limit(start, direction, limit):
    with pytest.raises(ValueError) as exc:
        revise(_req([5], start), direction)
    assert limit in str(exc.value).lower()


# --- rule 2: band ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("levels", "difficulty", "band"),
    [
        ([5, 5, 5, 5], "high", (3000, 4400)),
        ([5, 5, 5, 5], "moderate", (2000, 3000)),
        ([5, 5, 5, 5], "low", (0, 2000)),
        ([1, 1, 1, 1], "low", (0, 200)),
        ([1, 5, 20], "high", (75 + 750 + 13_200, 100 + 1_100 + 22_000)),
    ],
)
def test_2_band_and_budget(levels, difficulty, band):
    result = build_options(_req(levels, difficulty), Catalog.load(FIXTURES))
    assert tuple(result.band) == band
    assert result.budget == band[1] == _budget(levels, difficulty)


@needs_snapshot
@pytest.mark.parametrize("request_", REQUESTS, ids=_rid)
def test_2_every_option_total_is_inside_the_band(full, request_):
    result = build_options(request_, full)
    lo, hi = result.band
    assert result.options, result.note
    for opt in result.options:
        assert lo < opt.total_xp <= hi
        assert opt.total_xp == sum(m.xp * m.count for m in opt.monsters)


# --- rule 1: candidates ----------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("request_", REQUESTS, ids=_rid)
def test_1_every_monster_passes_the_candidate_filters(full, request_):
    index = _by_name(full)
    top = max(request_.party_levels)
    for opt in build_options(request_, full).options:
        for om in opt.monsters:
            m = index[om.name]
            assert 0 < m.cr <= top, om
            if request_.creature_types:
                assert m.record["type"]["key"] in request_.creature_types
            if request_.exclude_flying:
                assert m.fly_speed == 0
            assert om.cr == format_cr(m.cr)
            assert om.xp == m.record["experience_points"]


@needs_snapshot
def test_1_exclude_names_is_case_insensitive(full):
    base = _req([5, 5, 5, 5], "high", creature_types=["undead"])
    first = build_options(base, full)
    used = sorted({m.name for opt in first.options for m in opt.monsters})
    assert used
    odd_case = [n.swapcase() for n in used]
    again = build_options(base.model_copy(update={"exclude_names": odd_case}), full)
    assert not ({m.name for opt in again.options for m in opt.monsters} & set(used))


# --- rule 3: shape ---------------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("request_", REQUESTS, ids=_rid)
def test_3_shape(full, request_):
    for opt in build_options(request_, full).options:
        names = [m.name for m in opt.monsters]
        assert 1 <= len(names) <= request_.max_stat_blocks
        assert len(set(names)) == len(names)
        assert all(m.count >= 1 for m in opt.monsters)
        assert sum(m.count for m in opt.monsters) <= 2 * len(request_.party_levels)
        assert opt.rating == request_.difficulty.value


@needs_snapshot
def test_3_options_carry_no_srd_warnings(full):
    # Within the shape rules none of encounter.evaluate's warnings can apply.
    for request_ in REQUESTS:
        for opt in build_options(request_, full).options:
            assert opt.warnings == [], opt


# --- rule 4: variety -------------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("request_", REQUESTS, ids=_rid)
def test_4_variety(full, request_):
    options = build_options(request_, full).options
    sets = [frozenset(_names(o)) for o in options]
    assert len(set(sets)) == len(sets)
    tops = []
    for o in options:
        best = max(m.xp for m in o.monsters)
        tops.append(frozenset(m.name for m in o.monsters if m.xp == best))
    assert len(set(tops)) == len(tops), tops


# --- rule 5: ranking -------------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("request_", REQUESTS, ids=_rid)
def test_5_options_are_in_ranking_order_and_roles_are_consistent(full, request_):
    index = _by_name(full)
    options = build_options(request_, full).options
    assert len(options) <= 3

    def key(o):
        return (-len(o.roles), -o.total_xp, sum(m.count for m in o.monsters))

    assert [key(o) for o in options] == sorted(key(o) for o in options)
    for o in options:
        assert all(m.role == primary_role(index[m.name]) for m in o.monsters)
        assert sorted(o.roles) == sorted({m.role for m in o.monsters})


@needs_snapshot
def test_5_max_options_caps_the_count(full):
    request_ = _req([5, 5, 5, 5], "high")
    assert len(build_options(request_, full, max_options=1).options) == 1
    assert len(build_options(request_, full, max_options=5).options) == 5


def test_5_more_roles_beat_higher_xp():
    # One level-5 character, Moderate: band (500, 750], at most 2 creatures.
    # Fits: {Brute, Archer} 650 XP (frontline + ranged), {Brute, Hexer} 550 XP (frontline +
    # controller), {Ogre} 700 XP (frontline). The two Brute options share their highest-XP
    # stat block, so only the better-ranked one ({Brute, Archer}) may appear.
    cat = _hand_catalog(
        _creature("Brute", 2, 450, [MELEE]),
        _creature("Archer", 1, 200, [RANGED]),
        _creature("Hexer", 0.5, 100, [MELEE, CONTROL]),
        _creature("Ogre", 3, 700, [MELEE]),
    )
    result = build_options(_req([5], "moderate"), cat)
    assert [_names(o) for o in result.options] == [{"Brute", "Archer"}, {"Ogre"}]
    assert [o.total_xp for o in result.options] == [650, 700]
    assert sorted(result.options[0].roles) == ["frontline", "ranged"]


def test_5_with_equal_roles_higher_xp_comes_first():
    # Band (500, 750] for one level-5 character: {Zeta} 700 XP vs {Alpha, Beta} 650 XP.
    cat = _hand_catalog(
        _creature("Alpha", 2, 450, [MELEE]),
        _creature("Beta", 1, 200, [MELEE]),
        _creature("Zeta", 3, 700, [MELEE]),
    )
    result = build_options(_req([5], "moderate"), cat)
    assert [_names(o) for o in result.options] == [{"Zeta"}, {"Alpha", "Beta"}]


# --- rule 6: deterministic -------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("request_", REQUESTS[:3], ids=_rid)
def test_6_deterministic(full, request_):
    first = asdict(build_options(request_, full))
    assert asdict(build_options(request_, full)) == first
    fresh = Catalog.load(chunks=read_jsonl(CHUNKS))
    assert asdict(build_options(request_.model_copy(deep=True), fresh)) == first


# --- rule 7: bounded -------------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize(
    "levels_and_kw",
    [
        ([20] * 12, {"difficulty": "high"}),
        ([20] * 12, {"difficulty": "low"}),
        ([1] * 4, {"difficulty": "low"}),
        ([1] * 12, {"difficulty": "high"}),
        ([1, 3, 5, 8, 11, 14, 17, 20], {"difficulty": "moderate"}),
        ([10] * 12, {"difficulty": "high", "creature_types": ["beast", "dragon"]}),
    ],
)
def test_7_bounded_under_one_second_on_the_full_catalog(levels_and_kw):
    levels, kw = levels_and_kw
    out = run_isolated(
        f"""
        from pathlib import Path
        from dungeonmind.rag.ingest import read_jsonl
        from dungeonmind.tools.catalog import Catalog
        from dungeonmind.tools.encounter_builder import EncounterRequest, build_options
        cat = Catalog.load(chunks=read_jsonl(Path({str(CHUNKS)!r})))
        req = EncounterRequest(party_levels={levels!r}, **{kw!r})
        _started = time.perf_counter()
        result = build_options(req, cat)
        _done(len(result.options))
        """,
        hard_timeout=20,
    )
    assert out["elapsed"] < 1.0, out


# --- rule 8: honest when empty ---------------------------------------------------------------


@needs_snapshot
def test_8_no_candidates_after_filters(full):
    # The only ooze at CR <= 1 is the Gray Ooze, and it's excluded.
    result = build_options(
        _req([1], "low", creature_types=["ooze"], exclude_names=["gray ooze"]), full
    )
    assert result.options == []
    assert result.note


@needs_snapshot
def test_8_no_combination_fits_the_band(full):
    # One level-1 character, Moderate: band (50, 75], at most 2 creatures. The only plant at
    # CR <= 1 is the Violet Fungus (50 XP): 1 → 50, 2 → 100; neither fits.
    result = build_options(_req([1], "moderate", creature_types=["plant"]), full)
    assert result.options == []
    assert result.note


@needs_snapshot
def test_8_the_two_empty_reasons_are_told_apart(full):
    none_left = build_options(
        _req([1], "low", creature_types=["ooze"], exclude_names=["Gray Ooze"]), full
    )
    no_fit = build_options(_req([1], "moderate", creature_types=["plant"]), full)
    assert none_left.note != no_fit.note


@needs_snapshot
def test_8_options_found_means_no_empty_note(full):
    result = build_options(_req([5, 5, 5, 5], "high"), full)
    assert result.options
    assert result.note is None


# --- rule 9: citations -----------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("request_", REQUESTS, ids=_rid)
def test_9_citations_are_srd_page_citations(full, request_):
    index = _by_name(full)
    for opt in build_options(request_, full).options:
        for m in opt.monsters:
            assert m.citation.startswith("SRD 5.2.1"), m
            assert m.citation == index[m.name].citation


# --- theme is never a filter -----------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("theme", ["haunted crypt", "undead only, no flying, no dragons"])
def test_theme_never_filters(full, theme):
    base = _req([5, 5, 5, 5], "high")
    plain = asdict(build_options(base, full))
    themed = asdict(build_options(base.model_copy(update={"theme": theme}), full))
    assert themed == plain


def test_build_result_serialises_with_asdict():
    import json

    result = build_options(
        _req([5, 5, 5, 5], "high", creature_types=["undead"]), Catalog.load(FIXTURES)
    )
    data = json.loads(json.dumps(asdict(result)))
    assert set(data) >= {"options", "budget", "band", "note"}


# --- M5 clarifications -----------------------------------------------------------------------


@needs_snapshot
@pytest.mark.parametrize("name", ["Guard", "Kobold Warrior", "Mage", "Warrior Infantry"])
def test_c_melee_or_ranged_attack_grants_ranged_only(full, name):
    # Each has only "Melee or Ranged Attack Roll" weapons (checked against the snapshot).
    m = _by_name(full)[name]
    assert roles(m) == {"ranged"}
    assert primary_role(m) == "ranged"


def test_c_melee_or_ranged_text_in_a_hand_built_block():
    either = "Melee or Ranged Attack Roll: +3, reach 5 ft. or range 20/60 ft. 4 (1d6 + 1) damage."
    m = _hand_catalog(_creature("Spearman", 0.125, 25, [either])).monsters[0]
    assert roles(m) == {"ranged"}


@pytest.mark.parametrize("bad", [["dinosaur"], ["undead", "robot"]])
def test_c_unknown_creature_type_is_rejected(bad):
    with pytest.raises(ValidationError):
        EncounterRequest(party_levels=[5], creature_types=bad)


def test_c_every_catalog_creature_type_is_accepted():
    from dungeonmind.tools.catalog import CREATURE_TYPES

    assert EncounterRequest(party_levels=[5], creature_types=list(CREATURE_TYPES))


def test_c_final_tiebreak_is_sorted_lowercased_names():
    # Band (500, 750] for one level-5 character. Both options: 1 creature, 700 XP, frontline.
    # Case-sensitive sorting would put "Zed" before "ash"; lowercased puts "ash" first.
    cat = _hand_catalog(
        _creature("Zed", 3, 700, [MELEE]),
        _creature("ash", 3, 700, [MELEE]),
    )
    result = build_options(_req([5], "moderate"), cat)
    assert [_names(o) for o in result.options] == [{"ash"}, {"Zed"}]


# --- R1 name cap respects variety (docs/ARCHITECTURE.md › M5 review fixes) -------------------

R1_REQUEST = EncounterRequest(
    party_levels=[1] * 6, difficulty="high", creature_types=["beast"], max_stat_blocks=3
)


def _shape(result) -> list[list[tuple[str, int]]]:
    return [sorted((m.name, m.count) for m in opt.monsters) for opt in result.options]


@needs_snapshot
def test_r1_third_option_is_not_hidden_by_the_name_cap(full):
    # Verified against the snapshot: an uncapped search ranks this 5-creature option third;
    # the capped search used to return Ape + 4 Black Bears + 2 Constrictor Snakes (7).
    third = _shape(build_options(R1_REQUEST, full))[2]
    assert third == [("Ape", 1), ("Constrictor Snake", 2), ("Giant Toad", 2)]


@needs_snapshot
def test_r1_capped_search_equals_an_uncapped_search(full, monkeypatch):
    capped = build_options(R1_REQUEST, full)
    monkeypatch.setattr(encounter_builder, "NAMES_PER_CLASS", 10_000)
    monkeypatch.setattr(encounter_builder, "MAX_CLASSES", 10_000)
    monkeypatch.setattr(encounter_builder, "MAX_EXPANSIONS", 10_000_000)
    uncapped = build_options(R1_REQUEST, full)
    assert _shape(capped) == _shape(uncapped)
    assert asdict(capped) == asdict(uncapped)


# --- Re-review: the class cap must not drop cheap top-up creatures -----------------------------


@needs_snapshot
@pytest.mark.parametrize(
    ("levels", "best_xp"),
    [
        ([13, 9, 12, 20, 13, 12, 18, 14, 3, 13, 17, 8], 57_925),
        ([13, 10, 4, 8, 4, 18, 8, 20, 11, 3, 13], 39_875),
    ],
)
def test_class_cap_keeps_cheap_creatures_that_fill_the_budget(full, levels, best_xp, monkeypatch):
    # With 48 classes the 25 XP Goblin Minion class was dropped, so the best options stopped
    # 25 XP short (57,900 / 39,850). The result must match a search with every cap lifted.
    request = _req(levels, "moderate")
    capped = build_options(request, full)
    monkeypatch.setattr(encounter_builder, "MAX_CLASSES", 10_000)
    monkeypatch.setattr(encounter_builder, "NAMES_PER_CLASS", 10_000)
    monkeypatch.setattr(encounter_builder, "MAX_EXPANSIONS", 10_000_000)
    uncapped = build_options(request, full)
    assert uncapped.options[0].total_xp == best_xp
    assert asdict(capped) == asdict(uncapped)
