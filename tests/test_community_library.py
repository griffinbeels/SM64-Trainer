"""Discovery never changes Practice; metadata leases remain owner-bound."""
import asyncio
from copy import deepcopy

import pytest

from sm64_events.community import CachedPopulation, CommunityError, CommunitySync
from sm64_events.community import codec
from sm64_events.community.library import checked_page
from test_community_sync import Account, IDENTITY, Local, TARGET, native, recording


def summary_row():
    return {"target": dict(TARGET), "clock": "igt", "game_version": "us", "strategy": "Standard",
            "recordings": 4, "players": 2, "best_frames": 3}


def library_page(rows=(), *, summary=False, generation="g1", cursor=None):
    return {"v": 1, "generation": generation, "entries" if summary else "recordings": list(rows),
            "next_cursor": cursor, "complete": cursor is None}


class LibraryAccount(Account):
    def __init__(self):
        super().__init__()
        self.summary = library_page([summary_row()], summary=True)
        self.recordings = library_page([recording()])
        self.batch = {"v": 1, "recordings": [recording("1" * 64), recording("2" * 64)], "unavailable": []}
        self.after_read = lambda: None
        self.failure = None

    async def request_json(self, method, path, **kwargs):
        if path in ("/v1/library", "/v1/library/recordings", "/v1/recordings/metadata"):
            self.calls.append((method, path, kwargs))
            if self.failure:
                raise self.failure
            self.after_read()
            return deepcopy({"/v1/library": self.summary, "/v1/library/recordings": self.recordings,
                             "/v1/recordings/metadata": self.batch}[path])
        return await super().request_json(method, path, **kwargs)


def test_library_pages_use_portable_identity_and_loose_metadata_without_downloads_or_pool_changes(tmp_path):
    async def scenario():
        account = LibraryAccount()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        try:
            summary = await sync.library_summary()
            assert summary == {key: value for key, value in account.summary.items() if key != "v"}
            assert not population._rows and not population._pools
            rows = await sync.library_recordings(TARGET, "igt", "us", "Standard", cursor="page-two")
            assert rows["recordings"][0]["id"] == "1" * 64
            assert population.get("1" * 64)["frames"] == 3
            assert population.metadata_snapshot("star", "1-0", clock="igt", game_version="us", strategy="Standard") == ({"id": "local"},)
            assert not population.is_ready("1" * 64) and not population._pools
            query = account.calls[-1][2]["params"]
            assert query == {"target": '{"entity_key":"1-0","kind":"star"}', "clock": "igt", "rom": "us",
                             "strategy": "Standard", "cursor": "page-two"}
            assert not any(call[1].endswith("/asset") for call in account.calls)
        finally:
            population.close()
    asyncio.run(scenario())


def test_summary_and_recordings_pages_allow_unbounded_depth_through_bounded_cursors(tmp_path):
    async def scenario():
        account = LibraryAccount()
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path, account=account))
        try:
            for index in range(7):
                account.summary = library_page([summary_row()], summary=True, cursor=f"cursor-{index + 1}")
                result = await sync.library_summary(None if index == 0 else f"cursor-{index}")
                assert result["next_cursor"] == f"cursor-{index + 1}" and not result["complete"]
        finally:
            sync.population.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [None, "target", "rom"])
def test_summary_target_and_region_filters_are_sent_and_validated(tmp_path, change):
    async def scenario():
        account = LibraryAccount()
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path, account=account))
        if change == "target":
            account.summary["entries"][0]["target"]["entity_key"] = "2-1"
        elif change == "rom":
            account.summary["entries"][0]["game_version"] = "jp"
        try:
            if change:
                with pytest.raises(CommunityError) as found:
                    await sync.library_summary(target=TARGET, game_version="us")
                assert found.value.code == "identity_mismatch"
            else:
                await sync.library_summary(target=TARGET, game_version="us")
            assert account.calls[-1][2]["params"] == {"target": '{"entity_key":"1-0","kind":"star"}', "rom": "us"}
            assert not sync.population._pools
        finally:
            sync.population.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["owner", "signout", "closing", "generation"])
def test_library_late_response_never_publishes_under_another_account_or_generation(tmp_path, change):
    async def scenario():
        account = LibraryAccount()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        def changed():
            if change == "owner":
                account.state["uid"] = "22222222-2222-4222-8222-222222222222"
            elif change == "signout":
                account.state.update(authenticated=False, uid=None, status="signed_out")
            elif change == "closing":
                sync._closing = True
            else:
                sync._catalog_generation = "g2"
        account.after_read = changed
        try:
            with pytest.raises(CommunityError) as found:
                await sync.library_recordings(TARGET, "igt", "us", "Standard")
            assert found.value.code == ("generation_changed" if change == "generation" else "canceled")
            assert not population._rows and not population._pools
        finally:
            population.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["target", "clock", "rom", "strategy", "row_generation", "envelope_generation", "extra", "duplicate", "overfull"])
def test_library_invalid_response_rejects_whole_page_before_renewing_any_lease(tmp_path, change):
    async def scenario():
        account = LibraryAccount()
        bad = recording("2" * 64)
        if change == "target":
            bad["target"]["entity_key"] = "2-1"
        elif change == "clock":
            bad["clock"] = "rta"
        elif change == "rom":
            bad["game_version"] = "jp"
        elif change == "strategy":
            bad["strategy"] = bad["target"]["strat"] = "Alternate"
        elif change == "row_generation":
            bad["generation"] = "g2"
        account.recordings = library_page([recording(), bad])
        if change == "envelope_generation":
            account.recordings["generation"] = "g2"
        elif change == "extra":
            account.recordings["unsafe"] = True
        elif change == "duplicate":
            account.recordings["recordings"] = [recording(), recording()]
        elif change == "overfull":
            account.recordings["recordings"] = [recording(f"{index:064x}") for index in range(21)]
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        try:
            with pytest.raises(CommunityError):
                await sync.library_recordings(TARGET, "igt", "us", "Standard")
            assert not population._rows
            if change == "envelope_generation":
                assert sync._catalog_until == 0
        finally:
            population.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["players", "zero", "boolean", "invalid_target", "strategy", "extra", "duplicate", "cursor", "completion", "version"])
def test_summary_envelope_and_counts_are_strict(change):
    row = summary_row()
    value = library_page([row], summary=True)
    if change == "players":
        row["players"] = 5
    elif change == "zero":
        row["recordings"] = 0
    elif change == "boolean":
        row["best_frames"] = True
    elif change == "invalid_target":
        row["target"] = {"kind": "star", "entity_key": "-1-99"}
    elif change == "strategy":
        row["strategy"] = ""
    elif change == "extra":
        row["video"] = "unsafe"
    elif change == "duplicate":
        value["entries"].append(deepcopy(row))
    elif change == "cursor":
        value.update(next_cursor="x" * 2049, complete=False)
    elif change == "completion":
        value["complete"] = False
    else:
        value["v"] = True
    with pytest.raises(CommunityError):
        checked_page(value, summary=True, generation="g1")


def test_library_invalid_queries_and_signed_out_reads_do_no_io(tmp_path):
    async def scenario():
        account = LibraryAccount()
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path, account=account))
        try:
            for args in ((TARGET, "igt", "us", ""), (TARGET, "igt", None, "Standard"), (TARGET, "invalid", "us", "Standard")):
                with pytest.raises(CommunityError):
                    await sync.library_recordings(*args)
            with pytest.raises(CommunityError):
                await sync.library_summary(cursor=True)
            account.state.update(authenticated=False, uid=None, status="signed_out")
            with pytest.raises(CommunityError) as found:
                await sync.library_summary()
            assert found.value.code == "sign_in_required" and not account.calls
        finally:
            sync.population.close()
    asyncio.run(scenario())


def test_batch_renews_four_leases_with_one_call_and_keeps_bytes_integrity(tmp_path):
    async def scenario():
        account = LibraryAccount()
        rows = [recording(f"{index:064x}") for index in range(1, 5)]
        account.batch["recordings"] = rows
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        sync._catalog_generation = "g1"
        try:
            await sync._assets([row["id"] for row in rows], renew=True, identity_key=population.identity_key(IDENTITY))
            assert len([call for call in account.calls if call[1] == "/v1/recordings/metadata"]) == 1
            assert len([call for call in account.calls if call[1].endswith("/asset")]) == 4
            assert all(population.is_ready(row["id"]) for row in rows)
            assert not any(call[0] == "GET" and call[1].endswith(row["id"]) for row in rows for call in account.calls)
            account.calls.clear()
            await sync._assets([row["id"] for row in rows], renew=True)
            assert len(account.calls) == 1 and account.calls[0][1] == "/v1/recordings/metadata"
        finally:
            population.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [404, 405, 500, 401, 403])
def test_batch_falls_back_only_for_unsupported_routes(tmp_path, status):
    async def scenario():
        account = LibraryAccount()
        account.failure = CommunityError("request_failed", status=status)
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        pins = ["1" * 64, "2" * 64]
        try:
            if status in (404, 405):
                await sync._assets(pins, renew=True)
                assert not sync._batch_metadata_supported
                assert all(population.is_ready(identifier) for identifier in pins)
                await sync._assets(pins, renew=True)
                assert sum(call[1] == "/v1/recordings/metadata" for call in account.calls) == 1
            else:
                with pytest.raises(CommunityError):
                    await sync._assets(pins, renew=True)
                assert sync._batch_metadata_supported and not population._rows
                assert len(account.calls) == 1
        finally:
            population.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "bad_row", "identity", "generation", "owner"])
def test_batch_partial_or_wrong_response_cannot_renew_good_first_row(tmp_path, change):
    async def scenario():
        account = LibraryAccount()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        sync._catalog_generation = "g1"
        if change == "missing":
            account.batch["recordings"].pop()
        elif change == "extra":
            account.batch["unavailable"] = ["f" * 64]
        elif change == "duplicate":
            account.batch["recordings"][1] = recording()
        elif change == "bad_row":
            account.batch["recordings"][1]["full_start"] = False
        elif change == "identity":
            account.batch["recordings"][1]["clock"] = "rta"
        elif change == "generation":
            account.batch["recordings"][1]["generation"] = "g2"
        else:
            account.after_read = lambda: account.state.update(uid="22222222-2222-4222-8222-222222222222")
        try:
            if change == "owner":
                await sync._assets(["1" * 64, "2" * 64], renew=True, identity_key=population.identity_key(IDENTITY))
            else:
                with pytest.raises(CommunityError):
                    await sync._assets(["1" * 64, "2" * 64], renew=True, identity_key=population.identity_key(IDENTITY))
            assert not population._rows and not any(call[1].endswith("/asset") for call in account.calls)
        finally:
            population.close()
    asyncio.run(scenario())


def test_unavailable_batch_member_keeps_its_old_lease_while_valid_member_renews(tmp_path):
    async def scenario():
        account = LibraryAccount()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        rows = account.batch["recordings"]
        for row in rows:
            population.store_asset(row, codec.encode(native()), native())
        previous = population.lease_until(recording=rows[1]["id"])
        account.batch = {"v": 1, "recordings": [rows[0]], "unavailable": [rows[1]["id"]]}
        try:
            with pytest.raises(CommunityError) as found:
                await sync._assets([row["id"] for row in rows], renew=True)
            assert found.value.code == "asset_unavailable"
            assert population.lease_until(recording=rows[1]["id"]) == previous
            assert rows[1]["id"] in sync.refused_assets()
            assert not any(call[1].endswith("/asset") for call in account.calls)
        finally:
            population.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [None, "withdrawn", "owner", "generation", "identifier"])
def test_explicit_metadata_rechecks_publication_without_fetching_asset(tmp_path, change):
    async def scenario():
        account = LibraryAccount()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        identifier = "1" * 64
        population.renew_metadata(recording())
        previous = population.lease_until(recording=identifier)
        original = account.request_json
        async def respond(method, path, **kwargs):
            if path == "/v1/recordings/" + identifier:
                account.calls.append((method, path, kwargs))
                if change == "withdrawn":
                    raise CommunityError("not_found", status=404)
                if change == "owner":
                    account.state["uid"] = "22222222-2222-4222-8222-222222222222"
                row = recording("2" * 64 if change == "identifier" else identifier)
                if change == "generation":
                    row["generation"] = "g2"
                return {"v": 1, **row}
            return await original(method, path, **kwargs)
        account.request_json = respond
        try:
            if change:
                with pytest.raises(CommunityError):
                    await sync.recording_metadata(identifier)
                # account_owner is checked before touching the population;
                # the old account's row is never renewed by the response.
                assert population._rows[identifier][1] == previous
            else:
                row = await sync.recording_metadata(identifier)
                assert row == recording() and population.get(identifier)["id"] == identifier
            assert not any(call[1].endswith("/asset") for call in account.calls)
        finally:
            population.close()
    asyncio.run(scenario())


def test_shutdown_cancels_inflight_library_reads(tmp_path):
    async def scenario():
        account = LibraryAccount()
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path, account=account))
        entered = asyncio.Event()
        original = account.request_json
        async def blocked(method, path, **kwargs):
            if path == "/v1/library/recordings":
                entered.set()
                await asyncio.Future()
            return await original(method, path, **kwargs)
        account.request_json = blocked
        task = asyncio.create_task(sync.library_recordings(TARGET, "igt", "us", "Standard"))
        try:
            await entered.wait()
            await sync.stop()
            assert task.cancelled() and not sync._library_tasks and not sync.population._rows
        finally:
            sync.population.close()
    asyncio.run(scenario())


def test_browsing_more_than_metadata_capacity_keeps_active_manual_rival_without_renewing_it(tmp_path):
    from sm64_events.community.cache import MAX_METADATA

    async def scenario():
        account = LibraryAccount()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        active = recording("f" * 64)
        population.store_asset(active, codec.encode(native()), native())
        population.keep((active["id"],))
        lease = population.lease_until(recording=active["id"])
        other = {**TARGET, "entity_key": "2-1"}
        try:
            for offset in range(0, MAX_METADATA + 40, 20):
                rows = [recording(f"{index:064x}") for index in range(offset + 1, offset + 21)]
                for row in rows:
                    row["target"] = other
                account.recordings = library_page(rows)
                await sync.library_recordings(other, "igt", "us", "Standard")
            assert len(population._rows) == MAX_METADATA
            assert population.get(active["id"])["id"] == active["id"] and population.is_ready(active["id"])
            assert population.lease_until(recording=active["id"]) == lease
            path, _ = population.resolve(population.get(active["id"])["ghost"], TARGET)
            assert path.is_file() and not population._pools
            assert population.metadata_snapshot("star", "1-0", clock="igt", game_version="us", strategy="Standard") == ({"id": "local"},)
            population.keep(())
            population.renew_metadata(recording("e" * 64))
            assert active["id"] not in population._rows and len(population._rows) == MAX_METADATA
        finally:
            population.close()
    asyncio.run(scenario())


def test_metadata_protection_has_only_the_bounded_active_seats(tmp_path):
    from sm64_events.ghost.adaptive import MAX_RIVALS
    population = CachedPopulation(Local(), root=tmp_path, account=Account())
    try:
        population.keep(f"{index:064x}" for index in range(MAX_RIVALS * 100))
        assert len(population._kept) == MAX_RIVALS
    finally:
        population.close()
