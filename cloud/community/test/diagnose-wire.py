"""Bounded source-local cross-language premise probe, no user clips or network."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
from types import ModuleType

parent = Path(sys.argv[1]).resolve()
service = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(parent / 'src'), str(parent / 'tests')]
# Load only the production codec/sync seams. The stable project environment
# lacks the new OAuth client dependency; unrelated package re-exports are outside
# this byte-admission probe. No validator, fixture or byte content is replaced.
package=ModuleType('sm64_events.community')
package.__path__=[str(parent/'src/sm64_events/community')]
sys.modules['sm64_events.community']=package
spec = importlib.util.spec_from_file_location('wire_probe_source', parent / 'tests' / 'test_community_wire.py')
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)
cases = {}
for version in wire.VERSIONS:
    for clock in (1, 2):
        raw = wire._native(version, clock)
        case = wire._case(raw)
        cases[f'{version}-{clock}'] = {**case, 'level': struct.unpack_from('<i', raw, 104)[0]}
code = r"""
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {validateGHC1}=await import(pathToFileURL(process.argv[1]+'/src/codec.js'));
const input=JSON.parse(readFileSync(0,'utf8'));
for(const [name,c] of Object.entries(input)){
  const bytes=Buffer.from(c.encoded,'base64'),go=grant=>{try{validateGHC1(bytes,grant);return 'accepted';}catch(e){return e.stack.split('\n').slice(1,5).join(' | ');}};
  const corrected={...c.grant,target:{kind:'star',entity_key:name.endsWith('-2')?'2-6':'2-1'}};
  console.log(JSON.stringify({case:name,headerLevel:c.level,declaredTarget:c.grant.target.entity_key,original:go(c.grant),corrected:go(corrected)}));
}
"""
result = subprocess.run(['C:/Program Files/nodejs/node.exe', '--input-type=module', '-e', code, str(service)],
    input=json.dumps(cases), capture_output=True, text=True, encoding='utf-8', timeout=20, check=False,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
print(result.stdout, end='')
if result.returncode:
    print(result.stderr[-2000:])
raise SystemExit(result.returncode)
