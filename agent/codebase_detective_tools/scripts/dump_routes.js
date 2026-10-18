// Dumps every route registered on your Express app to routes.json.
//
// Setup (inside your TARGET project, not the detective):
//   npm install --save-dev express-list-endpoints
// Run from the target project root:
//   node /path/to/scripts/dump_routes.js ./src/app.js ./routes.json
//
// Your app file must EXPORT the express app without calling app.listen()
// at import time (keep listen() in server.js).
const fs = require('fs');
const path = require('path');
const listEndpoints = require('express-list-endpoints');

const appPath = path.resolve(process.argv[2] || './src/app.js');
const outPath = path.resolve(process.argv[3] || './routes.json');

const mod = require(appPath);
const app = mod.app || mod.default || mod;

const routes = listEndpoints(app);
fs.writeFileSync(outPath, JSON.stringify(routes, null, 2));
console.log(`Wrote ${routes.length} routes to ${outPath}`);
process.exit(0); // app import may leave DB/Redis connections open
