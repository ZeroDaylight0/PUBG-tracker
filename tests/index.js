// `node --test tests/` on Node 22+ treats "tests/" as a module path and loads this file.
// Older Node versions scan the directory themselves and skip this file (no .test.js suffix).
const fs = require('node:fs');
const path = require('node:path');

for (const f of fs.readdirSync(__dirname).filter((n) => n.endsWith('.test.js')).sort()) {
    require(path.join(__dirname, f));
}
