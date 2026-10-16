import source from '../catalog.json' with {type:'json'};
import {validateCatalog} from '../src/catalog.js';
import {sha256,canonical} from '../src/errors.js';
validateCatalog(source);
if(await sha256(canonical(source.entries))!==source.generation)throw Error('Canonical catalog digest mismatch');
console.log(JSON.stringify({entries:source.entries.length,generation:source.generation}));
