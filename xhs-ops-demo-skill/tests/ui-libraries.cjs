/* Load the real, dependency-free UI libraries into isolated VM fixtures. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function loadUiLibraries(context, assets) {
  for (const file of ['workspace-data.js', 'display-text.js', 'task-insights.js']) {
    for (const entry of ['index.html', 'harness.html']) {
      const html = fs.readFileSync(path.join(assets, entry), 'utf8');
      assert.ok(html.includes(`<script src="${file}"></script>`), `${entry} must load the real ${file} dependency`);
    }
    vm.runInContext(fs.readFileSync(path.join(assets, file), 'utf8'), context, { filename: file });
  }
  for (const name of ['WorkspaceData', 'DisplayText', 'TaskInsights']) {
    if (context.window?.[name]) context[name] = context.window[name];
  }
}
module.exports = { loadUiLibraries };
