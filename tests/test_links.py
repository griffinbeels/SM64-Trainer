from sm64_events.links import star_links, xcams_url


_HIST = "https://sm64-xcams.netlify.app/home/history"


def test_xcams_url_main_course_star():
    # star:8:2 = SSL "Inside the Ancient Pyramid"; star:7:3 = LLL "Red-Hot Log Rolling"
    assert xcams_url("star:8:2") == f"{_HIST}?star=ssl_3"
    assert xcams_url("star:7:3") == f"{_HIST}?star=lll_4"


def test_xcams_url_bowser_segment():
    assert xcams_url("segment:5") == f"{_HIST}?star=bow_1n"   # BitDW No Reds


def test_xcams_url_movement_segment_is_none():
    assert xcams_url("segment:1") is None                     # LBLJ has no xcams page


def test_xcams_url_secret_star():
    assert xcams_url("star:19:0") == f"{_HIST}?star=pss"      # Princess's Secret Slide


def test_xcams_url_unknown_is_none():
    assert xcams_url("star:99:0") is None
    assert xcams_url("garbage") is None


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
