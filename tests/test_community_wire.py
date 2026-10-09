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
from sm64_events.community import codec
from sm64_events.community.config import CommunityError
from sm64_events.community.sync import checked_catalog
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.clip import decode_clip, encode_clip, frame_from_bytes
from sm64_events.ghost.sharing_targets import canonical

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "cloud/community"
VERSIONS = (4, 5, 6, 7)
COUNT = 257  # Exercise both a full shuffled block and the final one-record block.
PADS = (None, (0, 0, 0), (-128, 127, 0xff3f))


def _native(version, clock=1):
    raw = encode_clip(tuple(frame_from_bytes(frame(index,
        pad=PADS[(index - 1) % 3] if version == 7 else None))
        for index in range(1, COUNT + 1)), producer(), clock_kind=clock,
        clip_id="22222222-2222-4222-8222-222222222222")
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
            "encoded_sha256": hashlib.sha256(encoded).hexdigest(), "frames": 100,
            **overrides}}


def _malformed():
    raw = _native(7)
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
  if(version === 7) for(let index=0;index<257;index++) {
    if(index%3 === 0) continue;
    const at=160+index*1284;raw.writeUInt32LE(31|512,at+28);
    if(index%3 === 2) {
      raw.writeInt16LE(-128,at+1276);raw.writeInt16LE(127,at+1278);raw.writeUInt16LE(0xff3f,at+1280);
    }
  }
  const encoded=ghc1(raw), grant={raw_bytes:raw.length,encoded_bytes:encoded.length,
    raw_sha256:digest(raw),encoded_sha256:digest(encoded),frames:100};
  return {version,raw:raw.toString('base64'),encoded:encoded.toString('base64'),
    ...accepted({encoded:encoded.toString('base64'),grant})};
});
const rejected=Object.fromEntries(Object.entries(input.bad).map(([name,item])=>[name,accepted(item)]));
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
console.log(JSON.stringify({python,service,rejected,catalog:{generation:catalog.generation,
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
    request = json.dumps({"python": python, "bad": bad}, separators=(",", ":"))
    assert len(request) < 20 << 20
    # The outer test runner owns the job, priority, and complete process tree.
    result = subprocess.run([node, "--input-type=module", "-e", NODE_PROBE, str(SERVICE)],
        input=request, capture_output=True, text=True, encoding="utf-8", timeout=20,
        cwd=ROOT, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr[-4000:]
    assert len(result.stdout) < 8 << 20
    return python, bad, json.loads(result.stdout)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("clock", (1, 2), ids=("igt", "rta"))
def test_python_native_bytes_survive_real_worker_codec(version, clock, wire):
    python, _, result = wire
    case, actual = python[f"{version}-{clock}"], result["python"][f"{version}-{clock}"]
    assert actual["ok"], actual
    assert actual["metadata"] == {"version": version, "record_count": COUNT,
        "clock_kind": clock, "native_sha256": case["grant"]["raw_sha256"]}
    raw = base64.b64decode(case["raw"])
    assert codec.decode(base64.b64decode(actual["encoded"]),
        raw_sha256=case["grant"]["raw_sha256"]) == raw
    pads = tuple(record.pad for record in decode_clip(raw).frames)
    assert pads == tuple(PADS[index % 3] if version == 7 else None for index in range(COUNT))


@pytest.mark.parametrize("version", VERSIONS)
def test_independent_service_native_fixtures_decode_exactly_in_python(version, wire):
    _, _, result = wire
    item = next(item for item in result["service"] if item["version"] == version)
    assert item["ok"], item
    raw = base64.b64decode(item["raw"])
    assert codec.decode(base64.b64decode(item["encoded"]),
        raw_sha256=item["metadata"]["native_sha256"]) == raw
    assert codec.decode(codec.encode(raw)) == raw
    pads = tuple(record.pad for record in decode_clip(raw).frames)
    assert pads == tuple(PADS[index % 3] if version == 7 else None for index in range(COUNT))


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
