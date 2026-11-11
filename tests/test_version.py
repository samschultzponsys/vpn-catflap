import pytest

from catflap import version


@pytest.mark.parametrize("cur,nxt", [("0.1", "0.2"), ("1.1", "1.2"), ("1.8", "1.9"),
                                     ("1.9", "2.0"), ("0.9", "1.0"), ("9.9", "10.0")])
def test_next_version_rolls_over_at_9(cur, nxt):
    assert version.next_version(cur) == nxt


@pytest.mark.parametrize("bad", ["1.10", "1", "1.2.3", "v1.a", "", "1.-1"])
def test_rejects_non_major_minor(bad):
    with pytest.raises(version.VersionError):
        version.parse(bad)


def test_current_version_is_valid():
    version.parse(version.__version__)


def test_is_newer_and_tags():
    assert version.is_newer("v2.0", "1.9")
    assert not version.is_newer("1.2", "1.2")
    assert version.parse("v1.4") == (1, 4)
