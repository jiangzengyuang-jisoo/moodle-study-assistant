import { readFileSync, writeFileSync } from 'node:fs';
// Small, version-pinned compatibility fixes; fail instead of silently patching a different version.
const root = new URL('../../node_modules/moodle-mcp/', import.meta.url);
if (JSON.parse(readFileSync(new URL('package.json', root))).version !== '0.2.0') throw new Error('Review patches before upgrading moodle-mcp');
for (const [file, replacements] of [
  ['assignments.js', [['[a.coursemodule, a]', '[a.cmid, a]']]],
  ['forums.js', [
    ['${mod.id}', '${mod.instance}'],
    ['        sortby: "timemodified",\n        sortdirection: "DESC",\n', ''],
    ['${d.firstuserfullname}', '${d.userfullname}'],
  ]],
]) {
  const path = new URL(`dist/tools/${file}`, root);
  let text = readFileSync(path, 'utf8').replaceAll('\r\n', '\n');
  for (const [before, after] of replacements) {
    if (text.includes(before)) text = text.replace(before, after);
    else if (after && !text.includes(after)) throw new Error(`Unexpected upstream source: ${file}`);
  }
  writeFileSync(path, text, 'utf8');
}
