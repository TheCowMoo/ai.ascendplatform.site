"""Graph validation is pure, so it is tested without a database or a model.

These are the guarantees the executor leans on: a graph has no loops, every step is
reachable, and nothing outward-facing can run without an approval gate before it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import automations  # noqa: E402

OTHER_ORG = 999999      # not a real client: these rows are cleaned up as they are made


def _chain(*kinds):
    steps = []
    for index, kind in enumerate(kinds):
        steps.append({
            "id": "s%d" % (index + 1),
            "type": kind,
            "title": kind.title(),
            "next": ["s%d" % (index + 2)] if index + 1 < len(kinds) else [],
        })
    return steps


def test_a_simple_chain_is_valid():
    assert automations.validate_graph(_chain("agent", "notify", "stop")) == []


def test_loops_are_rejected():
    steps = _chain("agent", "notify")
    steps[1]["next"] = ["s1"]
    assert any("loop" in p for p in automations.validate_graph(steps))


def test_unreachable_steps_are_rejected():
    steps = _chain("agent", "stop")
    steps.append({"id": "orphan", "type": "notify", "title": "Orphan", "next": ["s2"]})
    assert any("Unreachable" in p for p in automations.validate_graph(steps))


def test_missing_targets_are_rejected():
    steps = _chain("agent")
    steps[0]["next"] = ["ghost"]
    assert any("missing step" in p for p in automations.validate_graph(steps))


def test_unknown_types_are_rejected():
    steps = _chain("agent", "stop")
    steps[0]["type"] = "teleport"
    assert any("unknown type" in p for p in automations.validate_graph(steps))


def test_outward_action_needs_a_gate():
    steps = _chain("tool", "stop")
    steps[0]["config"] = {"tool": "send_email"}
    assert any("approval gate" in p for p in automations.validate_graph(steps))

    gated = _chain("gate", "tool", "stop")
    gated[0]["next"] = ["s2"]
    gated[1]["config"] = {"tool": "send_email"}
    gated[1]["next"] = ["s3"]
    assert automations.validate_graph(gated) == []


def test_empty_and_oversized_graphs_are_rejected():
    assert automations.validate_graph([]) != []
    assert automations.validate_graph(None) != []
    many = [{"id": "n%d" % i, "type": "stop", "title": "Stop", "next": []}
            for i in range(automations.MAX_NODES + 1)]
    assert any("Too many steps" in p for p in automations.validate_graph(many))


def test_unknown_trigger_is_rejected():
    assert any("trigger" in p for p in
               automations.validate_graph(_chain("agent", "stop"), trigger_type="whenever"))


def test_clean_graph_rejects_before_saving():
    try:
        automations._clean_graph([{"id": "a", "type": "agent", "title": "", "next": []}],
                                 {}, "manual")
    except automations.AutomationError as exc:
        assert "title" in str(exc)
    else:
        raise AssertionError("invalid graph was accepted")


def test_the_walk_follows_edges_not_list_order():
    """List order and graph order must agree only by coincidence."""
    steps = [
        {"id": "d", "type": "notify", "title": "D", "next": ["e"]},
        {"id": "e", "type": "stop", "title": "E", "next": []},
        {"id": "a", "type": "agent", "title": "A", "next": ["b"]},
        {"id": "b", "type": "gate", "title": "B", "next": ["d"]},
    ]
    assert automations.walk(steps) == ["d", "e"], automations.walk(steps)


def test_the_walk_stops_at_a_gate_and_a_stop():
    gated = [{"id": "a", "type": "agent", "title": "A", "next": ["g"]},
             {"id": "g", "type": "gate", "title": "G", "next": ["z"]},
             {"id": "z", "type": "notify", "title": "Z", "next": ["s"]},
             {"id": "s", "type": "stop", "title": "S", "next": []}]
    assert automations.walk(gated) == ["a", "g"]
    stopped = [{"id": "a", "type": "agent", "title": "A", "next": ["s"]},
               {"id": "s", "type": "stop", "title": "S", "next": []}]
    assert automations.walk(stopped) == ["a", "s"]


def test_the_walk_cannot_hang_on_a_cycle():
    steps = [{"id": "a", "type": "agent", "title": "A", "next": ["b"]},
             {"id": "b", "type": "agent", "title": "B", "next": ["a"]}]
    assert automations.walk(steps) == ["a", "b"]      # visited set stops it, no timeout
    assert automations.walk([]) == []


def test_resume_picks_up_after_the_gate_and_never_reruns_earlier_work():
    steps = [{"id": "a", "type": "agent", "title": "A", "next": ["g"]},
             {"id": "g", "type": "gate", "title": "G", "next": ["z"]},
             {"id": "z", "type": "notify", "title": "Z", "next": ["s"]},
             {"id": "s", "type": "stop", "title": "S", "next": []}]
    assert automations.walk(steps) == ["a", "g"]
    assert automations.walk(steps, resume_after="g") == ["z", "s"]
    assert automations.walk(steps, resume_after="z") == ["s"]
    # An unknown or stale gate id must not replay the whole graph.
    assert automations.walk(steps, resume_after="gone") == ["a", "g"]


def _branch(config):
    """A condition with a then branch and an else branch, each ending in its own stop."""
    return [
        {"id": "c", "type": "condition", "title": "Enough drafts?", "config": config,
         "next": ["then", "else"]},
        {"id": "then", "type": "notify", "title": "Then", "next": ["t_end"]},
        {"id": "else", "type": "notify", "title": "Else", "next": ["e_end"]},
        {"id": "t_end", "type": "stop", "title": "T", "next": []},
        {"id": "e_end", "type": "stop", "title": "E", "next": []},
    ]


def test_a_condition_takes_the_first_link_when_it_is_true():
    # OTHER_ORG has no drafts, so "drafts == 0" is true.
    steps = _branch({"fact": "drafts", "op": "==", "value": 0})
    assert automations.validate_graph(steps) == [], automations.validate_graph(steps)
    assert automations.walk(steps, org_id=OTHER_ORG) == ["c", "then", "t_end"]
    answer, note = automations.evaluate_condition(steps[0], OTHER_ORG)
    assert answer is True
    assert "first" in note, note
    print("true ->", note)


def test_a_condition_takes_the_second_link_when_it_is_false():
    steps = _branch({"fact": "drafts", "op": "==", "value": 5})
    assert automations.walk(steps, org_id=OTHER_ORG) == ["c", "else", "e_end"]
    answer, note = automations.evaluate_condition(steps[0], OTHER_ORG)
    assert answer is False
    assert "second" in note, note
    print("false ->", note)


def test_a_condition_without_an_else_link_stops_a_false_answer():
    """One link is a one-sided branch: false means the run ends there, not that it loops."""
    steps = _branch({"fact": "drafts", "op": ">", "value": 999})
    steps[0]["next"] = ["then"]
    assert automations.validate_graph(steps) != []      # 'else' is now unreachable
    assert automations.walk(steps, org_id=OTHER_ORG) == ["c"]


def test_a_condition_is_validated_before_it_can_be_saved():
    three = _branch({"fact": "drafts", "op": ">", "value": 0})
    three[0]["next"] = ["then", "else", "then"]
    assert any("at most two links" in p for p in automations.validate_graph(three))

    unknown = _branch({"fact": "vibes", "op": ">", "value": 0})
    assert any("unknown condition" in p for p in automations.validate_graph(unknown))

    bad_op = _branch({"fact": "drafts", "op": "≈", "value": 0})
    assert any("unknown operator" in p for p in automations.validate_graph(bad_op))
    print("three links, an unknown fact and a bad operator are all refused")


def test_an_unanswered_condition_reads_zero_rather_than_guessing():
    """No client, no rows: every fact is 0, and the run says so instead of inventing one."""
    assert automations.fact_value(None, "drafts") == 0
    assert automations.fact_value(OTHER_ORG, "sites") == 0
    answer, note = automations.evaluate_condition(
        {"type": "condition", "config": {"fact": "failed_jobs", "op": ">", "value": 0}}, None)
    assert answer is False and "0" in note, note


def test_resuming_with_nothing_parked_is_refused():
    mine = automations.create(OTHER_ORG, "No runs", [
        {"id": "s", "type": "stop", "title": "S", "next": []}])
    try:
        automations.resume(mine, OTHER_ORG)
    except automations.AutomationError as exc:
        assert "waiting" in str(exc).lower(), exc
    else:
        raise AssertionError("resume accepted an automation with no parked run")
    finally:
        automations.delete(mine, OTHER_ORG)
    print("resume refused when nothing is parked")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("AUTOMATION GRAPH OK")
