from sm64_events.links import star_links, ukikipedia_url, xcams_url


_HIST = "https://sm64-xcams.netlify.app/home/history"


def test_xcams_url_main_course_star():
    # star:8:2 = SSL "Inside the Ancient Pyramid"; star:7:3 = LLL "Red-Hot Log Rolling"
    assert xcams_url("star:8:2") == f"{_HIST}?star=ssl_3"
    assert xcams_url("star:7:3") == f"{_HIST}?star=lll_4"


def test_xcams_url_bowser_reds_star():
    # the Bowser courses' 8-red-coin star — the banner's "Reds" target
    assert xcams_url("star:16:0") == f"{_HIST}?star=bow_1r"   # BitDW Reds
    assert xcams_url("star:17:0") == f"{_HIST}?star=bow_2r"   # BitFS Reds
    assert xcams_url("star:18:0") == f"{_HIST}?star=bow_3r"   # BitS Reds


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
    assert links["ukikipedia"] == (
        "https://ukikipedia.net/wiki/RTA_Guide/Shoot_into_the_Wild_Blue")
    assert links["example"] is None


def test_punctuation_kept_spaces_underscored():
    links = star_links(4, 0)  # "Slip Slidin' Away"
    assert links["ukikipedia"].endswith("/RTA_Guide/Slip_Slidin'_Away")


def test_100_coin_star_uses_course_abbreviation():
    assert star_links(2, 6)["ukikipedia"] == (
        "https://ukikipedia.net/wiki/RTA_Guide/WF_100_Coins")


def test_override_wins():
    import sm64_events.links as L
    L.OVERRIDES[(2, 2)] = {"example": "https://example.com/wf-wild-blue"}
    try:
        assert star_links(2, 2)["example"] == "https://example.com/wf-wild-blue"
    finally:
        L.OVERRIDES.pop((2, 2))


# --- ukikipedia_url: a link is a page that EXISTS, never a hope ------------
#
# Every URL below is resolved against `ukikipedia_titles.py`, the wiki's own
# page list. A red test here after `tools/scrape_ukikipedia.py` means the
# wiki renamed or deleted a page -- which is exactly the alarm this exists
# to raise, since the old star-name-to-URL guess could only 404 silently.

_WIKI = "https://ukikipedia.net/wiki/RTA_Guide/"


def test_unknown_star_has_no_page():
    # Used to answer a made-up RTA_Guide URL; a link the click cannot keep is
    # worse than no link (acceptance.md: a dead control fails the same way).
    assert star_links(99, 0)["ukikipedia"] is None
    assert ukikipedia_url("garbage") is None
    assert ukikipedia_url(None) is None


def test_every_star_the_trainer_knows_has_a_page():
    # 15 courses x 7 stars (100 coins included), the three Bowser reds stars
    # and the six castle secret stars. The secret stars are the ones the old
    # generator got wrong: their star_name is "8 Red Coins" / "Slide Star",
    # so the wiki page is the COURSE's.
    for course in range(1, 16):
        for star in range(7):
            assert ukikipedia_url(f"star:{course}:{star}"), (course, star)
    for course in range(16, 25):
        assert ukikipedia_url(f"star:{course}:0"), course


def test_secret_course_star_links_to_the_course_page():
    assert ukikipedia_url("star:19:0") == _WIKI + "The_Princess's_Secret_Slide"
    assert ukikipedia_url("star:20:0") == _WIKI + "Cavern_of_the_Metal_Cap"


def test_bowser_reds_star_and_course_segment_share_the_course_page():
    assert ukikipedia_url("star:16:0") == _WIKI + "Bowser_in_the_Dark_World"
    assert ukikipedia_url("segment:5") == _WIKI + "Bowser_in_the_Dark_World"
    assert ukikipedia_url("segment:8") == _WIKI + "Bowser_Battles"


def test_a_title_the_wiki_spells_differently_still_resolves():
    # Our name: "Can the Eel Come Out to Play?"; the wiki's: "...Come out to Play?"
    assert ukikipedia_url("star:3:1") == _WIKI + "Can_the_Eel_Come_out_to_Play%3F"
    # and a redirect lands on the page it redirects to
    assert ukikipedia_url("star:23:0") == _WIKI + "Wing_Mario_Over_the_Rainbow"


def test_the_two_jet_streams_get_their_own_pages():
    assert ukikipedia_url("star:3:5") == _WIKI + "Through_the_Jet_Stream_(JRB)"
    assert ukikipedia_url("star:9:3") == _WIKI + "Through_the_Jet_Stream_(DDD)"


def test_a_movement_is_recognised_by_its_label():
    assert ukikipedia_url(None, "CCM wooden door - Enter WF (LBLJ)") == (
        _WIKI + "Lobby_Backwards_Long_Jump")
    assert ukikipedia_url(None, "Lakitu skip") == _WIKI + "Lakitu_Skip"
    assert ukikipedia_url("segment:1", "LBLJ") == _WIKI + "Lobby_Backwards_Long_Jump"
    # the more specific alias wins over the rabbit himself
    assert ukikipedia_url(None, "HMC door - Enter DDD (☆15 MIPS Clip)") == (
        _WIKI + "MIPS_Clip")
    assert ukikipedia_url(None, "Tunnel door - HMC door (MIPS)") == _WIKI + "MIPS"


def test_a_toad_detour_names_the_toad_by_where_he_stands():
    assert ukikipedia_url(None, "HMC door - Enter HMC (Toad)") == _WIKI + "HMC_Toad"
    assert ukikipedia_url(None, "THI door - Enter TTM (Toad)") == _WIKI + "Upstairs_Toad"
    assert ukikipedia_url(None, "TTC result - Enter RR (Toad)") == _WIKI + "Tippy_Toad"


def test_a_course_rta_row_is_the_course_page_whatever_its_parenthetical_says():
    assert ukikipedia_url(None, "HMC RTA (RTA strat, Fadeout, w/ CotMC)") == (
        _WIKI + "Hazy_Maze_Cave")
    assert ukikipedia_url(None, "BoB RTA (RTA strat, Fadeout, w/ cannon cutscene)") == (
        _WIKI + "Bob-omb_Battlefield")


def test_a_door_to_door_movement_has_no_page():
    # "Castle Movement" on the wiki redirects to Lakitu Skip -- there is no
    # general page to fall back on, so these honestly get no mark.
    assert ukikipedia_url(None, "Lobby door (L) - CCM wooden door") is None
    assert ukikipedia_url("segment:1", "Lobby door (L) - CCM wooden door") is None


def test_identity_outranks_the_label():
    # A star target's row label mentions a detour; the page is still the star's.
    assert ukikipedia_url("star:6:6", "HMC RTA + 100c w/ CotMC") == _WIKI + "HMC_100_Coins"
