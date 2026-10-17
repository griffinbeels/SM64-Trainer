"""Real Python/Worker wire parity; all fixtures are fictional native bytes.

Run through tools/run_tests.py (or the owned probe runner) for the shared CPU
budget and Windows process job. Node is a direct, quiet, bounded child. A
client-only checkout skips explicitly until cloud/community is integrated.
"""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.community import claims, codec
from sm64_events.community.config import CommunityError
from sm64_events.community.sync import checked_catalog
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.clip import decode_clip, encode_clip, frame_from_bytes
from sm64_events.ghost.sharing_targets import canonical

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "cloud/community"
VERSIONS = (4, 5, 6, 7)
COUNT = 257  # Exercise both a full shuffled block and the final one-record block.
PADS = (None, (0, 0, 0), (-128, 127, 0xff3f))  # one unknown pad in three
KNOWN_PADS = PADS[1:]
# The fixture's counter reads 1 on the origin and climbs one per record, so
# its last record reads COUNT; a star time is that plus Usamune's display tick.
CLAIM = COUNT + 1
REGION = {1: "us", 2: "jp"}  # gclip_header.rom_layout, written independently of claims.REGIONS
SEGMENT = {"kind": "segment", "entity_key": "7", "signature": "0" * 64, "name": "Fixture segment", "strat": None}


def _native(version, clock=1, *, pads=KNOWN_PADS, actions=None, dance_tail=False, layout=1):
    raw = encode_clip(tuple(frame_from_bytes(frame(index,
        pad=pads[(index - 1) % len(pads)] if version == 7 else None,
        action=None if actions is None else actions(index)))
        for index in range(1, COUNT + 1)), producer(), clock_kind=clock,
        clip_id="22222222-2222-4222-8222-222222222222", dance_tail=dance_tail)
    if layout != 1:
        raw = raw[:24] + struct.pack("<I", layout) + raw[28:]
    if version == 7:
        return raw
    _, width, sample_width, sample_version = codec.FORMATS[f"SM64GHO{version}".encode()]
    header = bytearray(raw[:160])
    header[:8] = f"SM64GHO{version}".encode()
    struct.pack_into("<3I", header, 8, version, 160, width)
    struct.pack_into("<I", header, 28, sample_version)
    records = []
    for index in range(COUNT):
        record = bytearray(raw[160 + index * 1284:160 + index * 1284 + width])
        struct.pack_into("<2I", record, 0, width, version)
        struct.pack_into("<2I", record, 16, sample_width, sample_version)
        records.append(record)
    return bytes(header + b"".join(records))


def _unchecked_encode(raw):
    """Transport malformed native records without calling the native validator."""
    width, count = struct.unpack_from("<2I", raw, 16)
    chunks = [codec.ENVELOPE.pack(b"GHC1", len(raw), 160, width, count), raw[:160]]
    for start in range(0, count, 256):
        n = min(256, count - start)
        records = raw[160 + start * width:160 + (start + n) * width]
        payload = zlib.compress(b"".join(records[column::width] for column in range(width)), 6)
        chunks.extend((codec.BLOCK.pack(n, len(payload)), payload))
    return b"".join(chunks)


def _case(raw, encoded=None, **overrides):
    encoded = codec.encode(raw) if encoded is None else encoded
    return {"raw": base64.b64encode(raw).decode("ascii"),
        "encoded": base64.b64encode(encoded).decode("ascii"),
        "grant": {"raw_bytes": len(raw), "encoded_bytes": len(encoded),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "encoded_sha256": hashlib.sha256(encoded).hexdigest(), "frames": CLAIM,
            "rom": REGION.get(struct.unpack_from("<I", raw, 24)[0], "us"), "clock": "igt",
            # Independent native fixture starts in Whomp's (level24, course2).
            "target": {"kind": "star", "entity_key": '2-6' if struct.unpack_from('<I', raw, 112)[0] == 2 else '2-1'},
            **overrides}}


def _dancing(index):
    """Rows COUNT-40..COUNT-38 fall after a midair grab, then Mario dances."""
    return 0x0c400201 if index < COUNT - 40 else 0x1904 if index < COUNT - 37 else 0x1302


# Row COUNT-37 is the dance entry; record n reads counter n, so its star time is n + 1.
DANCE_CLAIM = COUNT - 37 + 1


def _claims():
    """Every claim an upload makes, and the verdict both sides must reach."""
    raw, segment, jp = _native(7), _native(7, 2), _native(7, layout=2)
    # Raw transport for clips the Python encoder itself refuses to send.
    unchecked = lambda clip, **grant: _case(clip, _unchecked_encode(clip), **grant)
    tail = _native(7, actions=_dancing, dance_tail=True)
    cases = {
        "us_clip_named_us": (_case(raw), None),
        "jp_clip_named_jp": (unchecked(jp), None),
        "us_clip_named_jp": (_case(raw, rom="jp"), "rom_mismatch"),
        "jp_clip_named_us": (unchecked(jp, rom="us"), "rom_mismatch"),
        "unknown_rom_layout": (unchecked(_native(7, layout=3), rom="us"), "invalid_clip"),
        "one_pad_in_three_unknown": (_case(_native(7, pads=PADS)), "incomplete_inputs"),
        **{f"format_{version}_has_no_pads": (_case(_native(version)), "incomplete_inputs") for version in (4, 5, 6)},
        "one_frame_faster": (_case(raw, frames=CLAIM - 1), "frames_mismatch"),
        "one_frame_slower": (_case(raw, frames=CLAIM + 1), "frames_mismatch"),
        "hundred_coin_star_on_segment_clock": (_case(segment), None),
        "hundred_coin_star_one_frame_faster": (_case(segment, frames=CLAIM - 1), "frames_mismatch"),
        # The close is the dance entry, or a stale read at most two frames before it.
        **{f"dance_entry_less_{early}": (_case(tail, frames=DANCE_CLAIM - early), None) for early in (0, 1, 2)},
        "grab_frame_before_the_fall": (_case(tail, frames=DANCE_CLAIM - 3), "frames_mismatch"),
        "inside_the_dance": (_case(tail, frames=DANCE_CLAIM + 1), "frames_mismatch"),
        "end_of_the_dance_tail": (_case(tail, frames=CLAIM), "frames_mismatch"),
        "tail_flag_without_a_dance": (_case(_native(7, dance_tail=True)), "invalid_clip"),
        # A segment: never below the counter's span (COUNT-1), never above the
        # frame span (COUNT-1) plus the origin counter, 30 loading frames and the tick.
        **{f"segment_{name}": (_case(segment, clock="rta", target=SEGMENT, frames=frames), code)
           for name, frames, code in (("below_its_counter", COUNT - 2, "frames_mismatch"),
                                      ("at_its_counter", COUNT - 1, None),
                                      ("after_a_closing_load", COUNT - 1 + 1 + 30 + 1, None),
                                      ("past_a_closing_load", COUNT - 1 + 1 + 30 + 2, "frames_mismatch"))},
    }
    return {name: case for name, (case, _) in cases.items()}, {name: code for name, (_, code) in cases.items()}


def _python_verdict(item):
    """The trainer's verdict on one upload: its download door, then its claims."""
    raw, grant = base64.b64decode(item["raw"]), item["grant"]
    try:
        codec.decode(base64.b64decode(item["encoded"]), raw_sha256=grant["raw_sha256"])
        claims.check_claims(raw, game_version=grant["rom"], clock=grant["clock"], frames=grant["frames"])
    except CommunityError as error:
        return error.code
    except ValueError:
        return "invalid_clip"
    return None


def _malformed():
    raw = _native(7, pads=PADS)  # record 1 has an unknown pad for the pad mutations
    result = {}
    # Reserved/header identity and record identities must survive validation.
    mutations = {
        "header_reserved": [(152, "<I", 1)],
        "unknown_rom_layout": [(24, "<I", 0)],
        "unknown_clock": [(112, "<I", 0)],
        "sample_version_is_not_sample_bytes": [(160 + 20, "<I", 1268)],
        "record_configuration": [(160 + 8, "<I", 99)],
        "unknown_world_state": [(160 + 12, "<I", 12)],
        "nonmonotonic_sequence": [(160 + 1284 + 32, "<Q", 1)],
        "clock_raw_disagrees_with_source": [(160 + 272, "<I", 2)],
        "infinite_pose": [(160 + 76, "<f", float("inf"))],
        "pose_bound": [(160 + 76, "<f", 10000001)],
        "camera_bound": [(160 + 220, "<f", -10000001)],
        "fov_exclusive_bound": [(160 + 248, "<f", 180)],
        "unknown_pad_has_bytes": [(160 + 1276, "<h", 1)],
        "stick_exclusive_bound": [(160 + 1284 + 1276, "<h", 128)],
        "button_reserved_bit": [(160 + 1284 + 1280, "<H", 0x40)],
        "pad_reserved_word": [(160 + 1284 + 1282, "<H", 1)],
        "relative_counter_overflow": [(136, "<Q", 0), (160 + 308, "<Q", 0),
            (160 + (COUNT - 1) * 1284 + 308, "<Q", 2**64 - 1)],
    }
    for name, edits in mutations.items():
        bad = bytearray(raw)
        for at, layout, value in edits:
            struct.pack_into(layout, bad, at, value)
        bad = bytes(bad)
        result[name] = _case(bad, _unchecked_encode(bad))
    encoded = codec.encode(raw)
    n, size = codec.BLOCK.unpack_from(encoded, 180)
    extra = zlib.compress(b"extra stream")
    result["trailing_bytes"] = _case(raw, encoded + b"trailing")
    result["extra_zlib_stream"] = _case(raw, encoded[:180] + codec.BLOCK.pack(n, size + len(extra))
        + encoded[188:188 + size] + extra + encoded[188 + size:])
    result["wrong_block_count"] = _case(raw, encoded[:180] + codec.BLOCK.pack(n - 1, size) + encoded[188:])
    result["truncated_block"] = _case(raw, encoded[:-1])
    bomb = zlib.compress(bytes((n + 1) * 1284))
    result["inflates_over_declared_block"] = _case(raw, encoded[:180] + codec.BLOCK.pack(n, len(bomb))
        + bomb + encoded[188 + size:])
    over = bytearray(encoded)
    struct.pack_into("<I", over, 4, codec.MAX_RAW + 1)
    result["decoded_size_bound"] = _case(raw, bytes(over), raw_bytes=codec.MAX_RAW + 1)
    result["raw_hash_mismatch"] = _case(raw, encoded, raw_sha256="0" * 64)
    for case in result.values():
        case.pop("raw")  # Rejected bodies need only their compressed bytes and grant.
    return result


# Import the production admission validator and the service's independently
# written native/Deflate fixtures. No HTTP, database instance, or secret is used.
NODE_PROBE = r"""
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const root = process.argv[1];
const moduleAt = path => import(pathToFileURL(root + '/' + path).href);
const {validateGHC1} = await moduleAt('src/codec.js');
const {nativeClip, ghc1, digest} = await moduleAt('test/fixtures.js');
const {validateCatalog, catalogFor, lookup} = await moduleAt('src/catalog.js');
const input = JSON.parse(readFileSync(0, 'utf8'));
const accepted = item => {
  try { return {ok:true, metadata:validateGHC1(Buffer.from(item.encoded,'base64'),item.grant)}; }
  catch (error) { return {ok:false, code:error.code, status:error.status}; }
};
const python = Object.fromEntries(Object.entries(input.python).map(([name,item]) => {
  const raw = Buffer.from(item.raw,'base64');
  return [name, {...accepted(item), encoded:ghc1(raw).toString('base64')}];
}));
const service = [4,5,6,7].map(version => {
  const raw = nativeClip({version,count:257});
  if(version === 7) for(let index=2;index<257;index+=3) {
    const at=160+index*1284;
    raw.writeInt16LE(-128,at+1276);raw.writeInt16LE(127,at+1278);raw.writeUInt16LE(0xff3f,at+1280);
  }
  const encoded=ghc1(raw), grant={raw_bytes:raw.length,encoded_bytes:encoded.length,
    raw_sha256:digest(raw),encoded_sha256:digest(encoded),frames:257,rom:'us',clock:'igt',
    target:{kind:'star',entity_key:'1-0'}};
  return {version,raw:raw.toString('base64'),encoded:encoded.toString('base64'),
    ...accepted({encoded:encoded.toString('base64'),grant})};
});
const rejected=Object.fromEntries(Object.entries(input.bad).map(([name,item])=>[name,accepted(item)]));
const claims=Object.fromEntries(Object.entries(input.claims).map(([name,item])=>[name,accepted(item)]));
const catalog=JSON.parse(readFileSync(root+'/catalog.json','utf8'));
validateCatalog(catalog);
await catalogFor({CATALOG_GENERATION:catalog.generation});
const first=catalog.entries[0], strategy=first.strategies[0].name;
const unsupported=[['clock',first.target,'unknown',first.game_version,strategy],
  ['rom',first.target,first.clock,'unknown',strategy],
  ['star_range',{kind:'star',entity_key:'99-9'},first.clock,first.game_version,strategy],
  ['strategy',first.target,first.clock,first.game_version,'\u0000unsupported']];
const identity=Object.fromEntries(unsupported.map(([name,...args])=>{
  try { lookup(catalog,...args);return [name,true]; } catch { return [name,false]; }
}));
let boolVersionAccepted=true;
try { validateCatalog({...catalog,v:true}); } catch { boolVersionAccepted=false; }
console.log(JSON.stringify({python,service,rejected,claims,catalog:{generation:catalog.generation,
  entries:catalog.entries.length,identity,boolVersionAccepted}}));
"""


@pytest.fixture(scope="module")
def wire():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the real Worker wire parity check")
    if not (SERVICE / "src/codec.js").is_file():
        pytest.skip("Integrate cloud/community before running Worker wire parity")
    python = {f"{version}-{clock}": _case(_native(version, clock))
        for version in VERSIONS for clock in (1, 2)}
    bad = _malformed()
    claimed, _ = _claims()
    request = json.dumps({"python": python, "bad": bad, "claims": claimed}, separators=(",", ":"))
    assert len(request) < 20 << 20
    # The outer test runner owns the job, priority, and complete process tree.
    result = subprocess.run([node, "--input-type=module", "-e", NODE_PROBE, str(SERVICE)],
        input=request, capture_output=True, text=True, encoding="utf-8", timeout=20,
        cwd=ROOT, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr[-4000:]
    assert len(result.stdout) < 8 << 20
    return python, bad, json.loads(result.stdout)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("clock", (1, 2), ids=("star_clock", "segment_clock"))
def test_python_native_bytes_survive_real_worker_codec(version, clock, wire):
    python, _, result = wire
    case, actual = python[f"{version}-{clock}"], result["python"][f"{version}-{clock}"]
    if version == 7:
        assert actual["ok"], {key: actual.get(key) for key in ('code', 'status')}
        assert actual["metadata"] == {"version": version, "record_count": COUNT,
            "clock_kind": clock, "native_sha256": case["grant"]["raw_sha256"]}
    else:  # Formats before exact pad capture transport losslessly but are never ghosts.
        assert {key: actual.get(key) for key in ("ok", "code", "status")} == {
            "ok": False, "code": "incomplete_inputs", "status": 422}
    raw = base64.b64decode(case["raw"])
    assert codec.decode(base64.b64decode(actual["encoded"]),
        raw_sha256=case["grant"]["raw_sha256"]) == raw
    pads = tuple(record.pad for record in decode_clip(raw).frames)
    assert pads == tuple(KNOWN_PADS[index % 2] if version == 7 else None for index in range(COUNT))


@pytest.mark.parametrize("version", VERSIONS)
def test_independent_service_native_fixtures_decode_exactly_in_python(version, wire):
    _, _, result = wire
    item = next(item for item in result["service"] if item["version"] == version)
    raw = base64.b64decode(item["raw"])
    verdict = _python_verdict({**item, "grant": {"raw_sha256": hashlib.sha256(raw).hexdigest(),
        "rom": "us", "clock": "igt", "frames": 257}})
    if version == 7:  # the Worker fixture's star time is its record count
        assert item["ok"], {key: item.get(key) for key in ('code', 'status')}
        assert verdict is None
    else:
        assert (item["code"], verdict) == ("incomplete_inputs", "incomplete_inputs")
    assert codec.decode(base64.b64decode(item["encoded"]), raw_sha256=hashlib.sha256(raw).hexdigest()) == raw
    assert codec.decode(codec.encode(raw)) == raw
    pads = tuple(record.pad for record in decode_clip(raw).frames)
    assert pads == tuple(((-128, 127, 0xff3f) if index % 3 == 2 else (0, 0, 0)) if version == 7 else None
                         for index in range(COUNT))


# Until ghost/clip.py decodes rom_layout 2 (feature/jp-ghost-native), the
# trainer refuses every JP clip as malformed; integrating that branch turns
# these two red, and the marks come off.
JP_DECODE = pytest.mark.xfail(strict=True, reason="decode_clip refuses rom_layout 2 before feature/jp-ghost-native")


@pytest.mark.parametrize("name", tuple(pytest.param(name, marks=JP_DECODE) if name.startswith("jp_") else name
                                       for name in _claims()[1]))
def test_both_sides_reach_one_verdict_on_every_upload_claim(name, wire):
    """The Worker (codec.js) and the trainer (community/claims.py) refuse the
    same claims with the same code: a ROM the header contradicts, any unknown
    pad, a time the clip's clock does not bear out."""
    _, _, result = wire
    cases, expected = _claims()
    actual = result["claims"][name]
    worker = None if actual["ok"] else actual["code"]
    assert actual.get("status", 422) == 422
    assert (worker, _python_verdict(cases[name])) == (expected[name], expected[name])


@pytest.mark.parametrize("mutation", tuple(_malformed()))
def test_both_codecs_refuse_bounded_tampering(mutation, wire):
    _, bad, result = wire
    actual = result["rejected"][mutation]
    assert actual == {"ok": False, "code": "invalid_clip", "status": 422}, mutation
    with pytest.raises(ValueError):
        codec.decode(base64.b64decode(bad[mutation]["encoded"]),
            raw_sha256=bad[mutation]["grant"]["raw_sha256"])


def test_actual_bundled_catalog_has_the_same_canonical_digest_and_identity_ranges(wire):
    _, _, result = wire
    data = (SERVICE / "catalog.json").read_bytes()
    assert len(data) <= 4 << 20
    catalog = json.loads(data)
    generation, curves = checked_catalog(catalog)
    assert generation == hashlib.sha256(canonical(catalog["entries"])).hexdigest()
    assert result["catalog"] == {"generation": generation, "entries": len(catalog["entries"]),
        "identity": {"clock": False, "rom": False, "star_range": False, "strategy": False},
        "boolVersionAccepted": False}
    assert curves and all(curves.values())
    invalid = deepcopy(catalog)
    invalid["v"] = True
    with pytest.raises(CommunityError):
        checked_catalog(invalid)
    for field, value in (("clock", "unknown"), ("game_version", "unknown")):
        invalid = deepcopy(catalog)
        invalid["entries"][0][field] = value
        with pytest.raises(CommunityError):
            checked_catalog(invalid)
    invalid = deepcopy(catalog)
    invalid["entries"][0]["target"] = {"kind": "star", "entity_key": "99-9"}
    with pytest.raises(ValueError):
        checked_catalog(invalid)
