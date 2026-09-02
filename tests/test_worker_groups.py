"""The worker group every test carries for pytest-xdist's `loadgroup` scheduler.

`tests/conftest.py::pytest_collection_modifyitems` is the one place that
decides which tests may share a worker. These read the mark off the live item
so the rule is proved on the test that asks, not restated: drop the hook and
both go red.
"""
import pytest


def test_a_plain_test_stays_with_its_file(request):
    mark = request.node.get_closest_marker("xdist_group")
    assert mark is not None, "no worker group -- the conftest hook is gone"
    assert mark.args[0] == "tests/test_worker_groups.py"


@pytest.mark.spread
@pytest.mark.parametrize("case", ["one", "two"])
def test_a_spread_test_is_its_own_group(request, case):
    mark = request.node.get_closest_marker("xdist_group")
    assert mark is not None, "no worker group -- the conftest hook is gone"
    assert mark.args[0] == (
        f"tests/test_worker_groups.py::test_a_spread_test_is_its_own_group_{case}_")
    assert "@" not in mark.args[0] and "]" not in mark.args[0], (
        "xdist splits `nodeid@group` on those characters")


def test_under_loadgroup_the_group_reached_the_scheduler(request):
    """xdist rewrites the nodeid to `<nodeid>@<group>` on the worker, and only
    if the mark was there when ITS hook ran. Proves the hook order, which a
    look at the mark alone cannot: the mark is present either way."""
    if not getattr(request.config.option, "loadgroup", False):
        pytest.skip("only meaningful under `-n <k> --dist loadgroup`")
    assert request.node.nodeid.endswith("@tests/test_worker_groups.py"), request.node.nodeid
