"""Graph validation is pure, so it is tested without a database or a model.

These are the guarantees the executor leans on: a graph has no loops, every step is
reachable, and nothing outward-facing can run without an approval gate before it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import automations  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("AUTOMATION GRAPH OK")
