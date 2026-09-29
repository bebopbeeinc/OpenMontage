"""Choosing the locations before writing the prompts.

Drafting was serialized for one reason: a draft had to see what the earlier
ones took, or two images in a batch chose the same city. Deciding the
locations first removes that reason, and the ninety seconds each prompt takes
can then be spent at the same time rather than one after another.
"""

import json

import pytest

from scripts.chonky import prompts


def _reply(payload):
    return lambda system, user, *, model=None: json.dumps(payload)


THREE = {"locations": [{"city": "Prague", "country": "Czech Republic"},
                       {"city": "Porto", "country": "Portugal"},
                       {"city": "Osaka", "country": "Japan"}]}


def test_one_location_per_slot():
    got = prompts.pick_locations(slots=[1, 2, 3], used=[], caller=_reply(THREE))
    assert [g["city"] for g in got] == ["Prague", "Porto", "Osaka"]


def test_the_levels_are_named_so_the_choice_can_suit_them():
    """Level 1 wants somewhere iconic and level 5 does not."""
    seen = {}

    def caller(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(THREE)

    prompts.pick_locations(slots=[1, 3, 5], used=[], caller=caller)
    assert "1" in seen["user"] and "3" in seen["user"] and "5" in seen["user"]


def test_already_used_places_are_named():
    seen = {}

    def caller(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(THREE)

    prompts.pick_locations(slots=[1], used=["Lisbon, Portugal"],
                           caller=lambda s, u, model=None: (seen.update(user=u),
                                                            json.dumps({"locations": [
                                                                {"city": "Prague",
                                                                 "country": "CZ"}]}))[1])
    assert "Lisbon, Portugal" in seen["user"]


def test_a_reply_that_repeats_a_city_is_rejected():
    """Two images of one place is the failure this whole step exists to avoid."""
    same = {"locations": [{"city": "Prague", "country": "Czech Republic"},
                          {"city": "Prague", "country": "Czech Republic"}]}
    with pytest.raises(prompts.DraftError):
        prompts.pick_locations(slots=[1, 2], used=[], caller=_reply(same))


def test_a_reply_that_reuses_an_old_location_is_rejected():
    with pytest.raises(prompts.DraftError):
        prompts.pick_locations(slots=[1], used=["Prague, Czech Republic"],
                               caller=_reply({"locations": [
                                   {"city": "Prague",
                                    "country": "Czech Republic"}]}))


def test_the_wrong_number_of_locations_is_rejected():
    with pytest.raises(prompts.DraftError):
        prompts.pick_locations(slots=[1, 2, 3, 4], used=[], caller=_reply(THREE))


def test_a_rejected_pick_is_retried_with_the_reason():
    same = {"locations": [{"city": "Prague", "country": "CZ"},
                          {"city": "Prague", "country": "CZ"}]}
    good = {"locations": [{"city": "Prague", "country": "CZ"},
                          {"city": "Porto", "country": "PT"}]}
    replies = [json.dumps(same), json.dumps(good)]
    asks = []

    def caller(system, user, *, model=None):
        asks.append(user)
        return replies.pop(0)

    got = prompts.pick_locations(slots=[1, 2], used=[], caller=caller)
    assert [g["city"] for g in got] == ["Prague", "Porto"]
    assert len(asks) == 2
    assert "Prague" in asks[1]
