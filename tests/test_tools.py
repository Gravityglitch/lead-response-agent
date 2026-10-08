from agent.tools import ToolBox


def test_lookup_property_matches_name_fragment(toolbox: ToolBox) -> None:
    out = toolbox.lookup_property("maple court")
    assert out["found"] and out["matches"][0]["id"] == "P-101"


def test_lookup_property_no_match(toolbox: ToolBox) -> None:
    assert toolbox.lookup_property("zzz nowhere")["found"] is False


def test_propose_slot_skips_booked(toolbox: ToolBox) -> None:
    out = toolbox.propose_showing_slot("P-101")
    assert out["slot"]["id"] == "S-1" and "S-2" not in out["alternatives"]


def test_tools_are_read_only(toolbox: ToolBox) -> None:
    before = (len(toolbox.crm["leads"]), sum(s["booked"] for s in toolbox.calendar["slots"]))
    toolbox.call("lookup_property", {"query": "oak"})
    toolbox.call("propose_showing_slot", {"property_id": "P-103"})
    toolbox.call("escalate", {"reason": "test"})
    after = (len(toolbox.crm["leads"]), sum(s["booked"] for s in toolbox.calendar["slots"]))
    assert before == after
