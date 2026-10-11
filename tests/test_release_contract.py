import pytest

from sm64_events.core.release_contract import (
    REQUIRED_ASSETS, WITHDRAWN_MARKER, is_forward, select_release, version_key,
)


def release(tag, **kwargs):
    return {"tag_name": tag, "prerelease": "-" in tag,
            "assets": [{"name": name, "browser_download_url": "https://example.com/" + name}
                       for name in REQUIRED_ASSETS], **kwargs}


def test_semver_precedence_including_numeric_beta_and_final_promotion():
    versions = ["1.9.1", "1.10.0-beta.1", "1.10.0-beta.2", "1.10.0-beta.10",
                "1.10.0-rc.1", "1.10.0", "1.10.1", "1.11.0-beta.1"]
    assert sorted(reversed(versions), key=version_key) == versions
    assert version_key("v1.10.0+candidate.123") == version_key("1.10.0")


@pytest.mark.parametrize("invalid", ["1.2", "01.2.3", "1.2.3-beta.01", "1.2.3-",
                                    "1.2.3.4", "1.2.3 garbage", " vv1.2.3", None, 4])
def test_malformed_versions_fail_closed(invalid):
    with pytest.raises(ValueError):
        version_key(invalid)


def test_beta_off_selects_stable_beta_on_selects_furthest_forward():
    stable, beta = release("1.9.2"), release("1.10.0-beta.2")
    assert select_release([beta, stable], "1.9.1") == stable
    assert select_release([beta, stable], "1.9.1", include_beta=True) == beta


def test_opt_out_keeps_forward_floor_then_production_promotes_beta():
    assert select_release([release("1.9.3")], "1.10.0-beta.2") is None
    final = release("1.10.0")
    assert select_release([release("1.9.3"), final], "1.10.0-beta.2") == final


def test_opted_out_beta_repairs_only_its_exact_installed_version():
    installed = release("1.10.0-beta.2")
    rows = [release("1.10.0-beta.3"), installed, release("1.10.0-beta.1")]
    assert select_release(rows, "1.10.0-beta.2") is None
    assert select_release(rows, "1.10.0-beta.2", allow_same=True) == installed


def test_dates_cannot_make_old_feature_line_win():
    older = release("1.9.9", published_at="2030-01-01")
    newer = release("1.10.0", published_at="2020-01-01")
    assert select_release([older, newer], "1.9.1") == newer


def test_unavailable_candidates_do_not_hide_valid_stable_fallback():
    valid = release("1.9.2")
    bad = [release("1.10.0-beta.1", draft=True), release("1.10.0-beta.2", assets=[]),
           release("1.10.0-beta.3", body=WITHDRAWN_MARKER),
           release("1.10.0-beta.4", prerelease=False), release("1.10.0", prerelease=True),
           release("1.10.1", draft="false"), {"tag_name": "not a version"}]
    assert select_release(bad + [valid], "1.9.1", include_beta=True) == valid


@pytest.mark.parametrize("url", ["http://example.com/file", "file:///C:/file",
                                "https://example.com/file\n", "https://[broken/file"])
def test_invalid_asset_urls_are_never_offered(url):
    candidate = release("1.9.2")
    candidate["assets"][0]["browser_download_url"] = url
    assert select_release([candidate], "1.9.1") is None


def test_ambiguous_asset_and_same_version_repair():
    candidate = release("1.9.1")
    assert select_release([candidate], "1.9.1") is None
    assert select_release([candidate], "1.9.1", allow_same=True) == candidate
    candidate["assets"].append(candidate["assets"][0])
    assert select_release([candidate], "1.9.1", allow_same=True) is None
    assert is_forward("1.10.0", "1.10.0-beta.2")
    assert not is_forward("1.9.2", "1.10.0-beta.2")
