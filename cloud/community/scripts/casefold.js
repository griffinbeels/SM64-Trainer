// Reproducible full case-fold mapping from the Unicode Consortium; no font or install mutation.
import {writeFile} from "node:fs/promises";
const url="https://www.unicode.org/Public/16.0.0/ucd/CaseFolding.txt";
const response=await fetch(url); if(!response.ok) throw new Error("Unicode source unavailable");
const source=await response.text(), map={};
for(const line of source.split("\n")) {
  const match=/^([0-9A-F]+); ([CF]); ([0-9A-F ]+);/.exec(line);
  if(match) map[String.fromCodePoint(parseInt(match[1],16))]=match[3].trim().split(" ").map(x=>String.fromCodePoint(parseInt(x,16))).join("");
}
await writeFile("src/casefold.json",JSON.stringify({version:"Unicode-16.0.0",source:url,map})+"\n");
