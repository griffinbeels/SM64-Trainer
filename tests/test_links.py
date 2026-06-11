from sm64_events.links import star_links


def test_normal_star_generates_rta_guide_url():
    links = star_links(2, 2)  # WF "Shoot into the Wild Blue"
    assert links["ukikipedia"] == \
        "https://ukikipedia.net/wiki/RTA_Guide/Shoot_into_the_Wild_Blue"
    assert links["example"] is None


def test_punctuation_kept_spaces_underscored():
    links = star_links(4, 0)  # "Slip Slidin' Away"
    assert links["ukikipedia"].endswith("/RTA_Guide/Slip_Slidin'_Away")


def test_100_coin_star_uses_course_abbreviation():
    assert star_links(2, 6)["ukikipedia"] == \
        "https://ukikipedia.net/wiki/RTA_Guide/WF_100_Coins"


def test_override_wins():
    import sm64_events.links as L
    L.OVERRIDES[(2, 2)] = {"example": "https://example.com/wf-wild-blue"}
    try:
        assert star_links(2, 2)["example"] == "https://example.com/wf-wild-blue"
    finally:
        L.OVERRIDES.pop((2, 2))


def test_unknown_star_still_returns_a_wiki_search_link():
    links = star_links(99, 0)
    assert links["ukikipedia"].startswith("https://ukikipedia.net/wiki/RTA_Guide/")
