from dungeonmind.rag.hybrid import rrf


def test_rrf_rewards_agreement_between_rankers():
    fused = rrf({"bm25": ["a", "b", "c"], "dense": ["b", "a", "d"]})
    assert [d for d, _ in fused][:2] in (["a", "b"], ["b", "a"])
    assert {d for d, _ in fused} == {"a", "b", "c", "d"}


def test_rrf_weights_shift_the_ranking():
    rankings = {"bm25": ["x", "y"], "dense": ["y", "x"]}
    assert rrf(rankings, {"bm25": 0.3})[0][0] == "y"
    assert rrf(rankings, {"dense": 0.3})[0][0] == "x"
