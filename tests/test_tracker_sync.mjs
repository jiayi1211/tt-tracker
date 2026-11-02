// Run: node tests/test_tracker_sync.mjs   (from the tt-tracker folder; Node 18+)
// Tests the tracker's GitHub sync (merging save, load repair) and the Update button against a fake GitHub API.
import fs from 'fs';
import vm from 'vm';
const html = fs.readFileSync(process.argv[2] || new URL("../tt_results_tracker.html", import.meta.url), "utf8");
function grab(name) {
  const m = html.match(new RegExp('(async )?function ' + name + '\\('));
  if (!m) throw new Error('missing ' + name);
  let i = html.indexOf('{', m.index), depth = 0, j = i;
  for (; j < html.length; j++) { if (html[j] === '{') depth++; else if (html[j] === '}' && --depth === 0) break; }
  return html.slice(m.index, j + 1);
}
const names = ['cleanScore', 'n', 'getGithubPAT', 'githubRead', 'githubLoad', 'setSyncBase', 'mergeWithRemote',
               'githubSave', 'loadFromGitHub', 'runResultsUpdate', 'loadShared', 'save'];
let src = names.map(grab).join('\n\n');
src = `const GITHUB_USER='u', GITHUB_REPO='r', GITHUB_FILE='data.json', PAT_KEY='pat', STORAGE_KEY='k';
const GITHUB_API='https://api.github.com/repos/u/r'; let _syncBase=null; let matches=[];
const _SEED=[{ id: 42, player: 'Starter', score: '3–0' }];
const UPDATE_WORKFLOW = GITHUB_API + '/actions/workflows/update-results.yml';
${src}
globalThis.api = { mergeWithRemote, setSyncBase, githubSave, loadFromGitHub, runResultsUpdate, loadShared, save,
  get matches(){return matches}, set matches(v){matches=v}, get base(){return _syncBase} };`;

// ---- fake environment ----
const store = { pat: 'tok' };
const gh = { reads: [], readFails: false, data: [], sha: 1, puts: 0, conflictOnce: false, dispatched: 0, runStatus: [], run: null, dispatchStatus: 204 };
const b64 = s => Buffer.from(s, 'utf8').toString('base64');
const alerts = [], status = [];
const ctx = {
  console, TextDecoder, TextEncoder, Uint8Array, JSON, Map, Set, Date, Math, Promise, Buffer,
  atob: s => Buffer.from(s, 'base64').toString('latin1'),
  btoa: s => Buffer.from(s, 'latin1').toString('base64'),
  setTimeout: (f) => setImmediate(f),
  localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } },
  document: { getElementById: () => ({ style: {}, textContent: 'Update from WTT/ITTF', disabled: false }) },
  alert: m => alerts.push(m), renderAll: () => {},
  showSyncStatus: (msg, color, sticky) => { status.push({ msg, color, sticky: !!sticky }); },
  fetch: async (url, opt = {}) => {
    const method = opt.method || 'GET';
    const resp = (status, body) => ({ ok: status < 300, status, json: async () => body, text: async () => body });
    if (url.endsWith('/contents/data.json') && method === 'GET') {
      gh.reads.push(opt.headers);
      if (gh.readFails) return resp(500, {});
      if (opt.headers.Accept.includes('raw')) return resp(200, JSON.stringify(gh.data));
      if (gh.overOneMB) return resp(200, { sha: String(gh.sha), content: '' });  // GitHub omits content over 1 MB
      return resp(200, { sha: String(gh.sha), content: b64(JSON.stringify(gh.data)).replace(/(.{60})/g, '$1\n') });
    }
    if (url.endsWith('/contents/data.json') && method === 'PUT') {
      const body = JSON.parse(opt.body);
      if (gh.conflictOnce) { gh.conflictOnce = false; gh.data = [{ id: 999, player: 'Late', score: '1–0' }, ...gh.data]; gh.sha++; return resp(409, {}); }
      if (body.sha !== String(gh.sha)) return resp(409, {});
      gh.data = JSON.parse(Buffer.from(body.content, 'base64').toString('utf8')); gh.sha++; gh.puts++;
      return resp(200, {});
    }
    if (url.endsWith('/dispatches')) { gh.dispatched++; if (gh.onDispatch) gh.onDispatch(); return resp(gh.dispatchStatus, {}); }
    if (url.includes('/runs?')) {
      const st = gh.runStatus.shift() || 'completed';
      return resp(200, { workflow_runs: [{ created_at: new Date().toISOString(), status: st, conclusion: gh.conclusion || 'success', html_url: 'https://run' }] });
    }
    throw new Error('unexpected ' + method + ' ' + url);
  },
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const api = ctx.api;
let fails = 0;
const eq = (a, b, msg) => { const ok = JSON.stringify(a) === JSON.stringify(b); if (!ok) fails++; console.log((ok ? 'ok   ' : 'FAIL ') + msg + (ok ? '' : `\n   got ${JSON.stringify(a)}\n   want ${JSON.stringify(b)}`)); };
const ids = l => l.map(m => m.id);

// 1. Load: repairs \x13 scores, writes them back once; base = what GitHub had.
gh.data = [{ id: 1, player: 'A', score: '3\x131' }, { id: 2, player: 'B', score: '2–3' }, { id: 3, player: 'C', score: '3–0' }];
await api.loadFromGitHub(); await new Promise(r => setImmediate(r)); await new Promise(r => setTimeout(r, 20));
eq(gh.data.map(m => m.score), ['3–1', '2–3', '3–0'], 'load repairs broken dash and writes it back');
eq(gh.puts, 1, 'exactly one save after load (only because a score was repaired)');
gh.puts = 0;
await api.loadFromGitHub(); await new Promise(r => setTimeout(r, 20));
eq(gh.puts, 0, 'no save on load when nothing needed repair');

// 2. Tab adds 10, edits 2, deletes 3 while the Update job adds 50 and fills stats on 1.
let local = JSON.parse(JSON.stringify(api.matches));
local = [{ id: 10, player: 'New', score: '3–0' }, ...local.filter(m => m.id !== 3)];
local.find(m => m.id === 2).score = '3–2';
gh.data = [{ id: 50, player: 'Synced', score: '3–1' }, ...gh.data.map(m => m.id === 1 ? { ...m, ptsWon: '33' } : m)]; gh.sha++;
const merged = await api.githubSave(local);
eq(ids(merged), [10, 50, 1, 2], 'save keeps job-added row 50, adds 10, drops deleted 3');
eq(merged.find(m => m.id === 1).ptsWon, '33', "save keeps the job's stats on a row this tab didn't touch");
eq(merged.find(m => m.id === 2).score, '3–2', "save keeps this tab's edit");
eq(ids(gh.data), [10, 50, 1, 2], 'GitHub now holds the merged list');

// 3. Conflict while saving (someone saved in between): retries and keeps their row too.
gh.conflictOnce = true;
const local2 = [{ id: 11, player: 'Another', score: '3–0' }, ...merged];
const merged2 = await api.githubSave(local2);
eq(ids(merged2), [11, 999, 10, 50, 1, 2], 'after a 409 it re-reads, merges again and keeps the concurrent row');

// 4. No base yet (save before any load): union, nothing deleted.
ctx.api; vm.runInContext('_syncBase = null', ctx);
gh.data = [{ id: 7, player: 'X' }]; gh.sha++;
eq(ids(await api.githubSave([{ id: 8, player: 'Y' }])), [8, 7], 'without a base nothing on GitHub is deleted');

// 5. Update button: dispatch, wait, reload, report counts.
gh.data = [{ id: 1, player: 'A', score: '3–1', ptsWon: '' }, { id: 2, player: 'B', score: '3–0', ptsWon: '' }]; gh.sha++;
await api.loadFromGitHub();
gh.runStatus = ['queued', 'in_progress', 'completed']; gh.conclusion = 'success';
gh.onDispatch = () => { gh.data = [{ id: 5, player: 'N', score: '3–0', ptsWon: '' }, { id: 6, player: 'M', score: '3–1', ptsWon: '30' },
                                  { id: 1, player: 'A', score: '3–1', ptsWon: '33' }, { id: 2, player: 'B', score: '3–0', ptsWon: '' }]; gh.sha++; };
alerts.length = 0;
await api.runResultsUpdate();
eq(alerts[0], 'Update finished: 2 match(es) added, Match Stats added to 1 existing match(es).\nDetails: https://run', 'button reports added and filled counts');
eq(api.matches.length, 4, 'button reloads the new data');

// 6. Token without Actions permission: clear message, nothing else happens.
gh.dispatchStatus = 403; gh.onDispatch = null; alerts.length = 0;
await api.runResultsUpdate();
eq(alerts[0].startsWith('This GitHub token may not start the update.'), true, 'missing permission explained');

// 7. Failed run: points to the log.
gh.dispatchStatus = 204; gh.runStatus = ['completed']; gh.conclusion = 'failure'; alerts.length = 0;
await api.runResultsUpdate();
eq(alerts[0], 'The update failed. Details: https://run', 'failed run reported with link');

// 8. No token: shows GitHub's data read-only, without sending a token, leaving this browser's copy alone.
delete store.pat; gh.readFails = false; gh.reads.length = 0; status.length = 0;
gh.data = [{ id: 1, player: 'A', score: '3\x131' }, { id: 2, player: 'B', score: '3–0' }]; gh.sha++;
store.k = JSON.stringify([{ id: 42, player: 'Starter', score: '3–0' }, { id: 777, player: 'Local only', score: '3–0' }]);
const storedBefore = store.k, putsStart = gh.puts;
await api.loadShared();
eq(gh.reads.every(h => !('Authorization' in h)), true, 'no token: reads GitHub without sending a token');
eq(api.matches.map(m => [m.id, m.score]), [[1, '3–1'], [2, '3–0']], "no token: shows GitHub's data (dash repaired for display)");
eq(store.k, storedBefore, "no token: this browser's saved copy is left untouched");
eq(/also has 1 older match/.test(status.at(-1).msg) && status.at(-1).sticky, true, 'no token: matches only on this device are reported (starter ones ignored)');
eq(gh.puts, putsStart, 'no token: nothing is written to GitHub');

// 9. No token and GitHub unreachable: says so, keeps what is on screen.
gh.readFails = true; status.length = 0; api.matches = [{ id: 9, player: 'Old' }];
await api.loadShared();
eq(api.matches.map(m => m.id), [9], 'load failure: keeps showing the saved copy');
eq(status[0].color === 'var(--red)' && status[0].sticky && /Couldn't load the shared data/.test(status[0].msg), true,
   'load failure: red message that stays up');

// 10. No token: add/edit/delete is refused and undone; nothing sent.
gh.readFails = false; alerts.length = 0; const putsBefore = gh.puts;
await api.loadShared();
api.matches = [{ id: 99, player: 'Typed on phone' }, ...api.matches];
api.save(); await new Promise(r => setTimeout(r, 20));
eq(alerts[0].startsWith('View only'), true, 'no token: save explains it is view only');
eq(api.matches.map(m => m.id), [1, 2], 'no token: the unsaved change is undone on screen');
eq([gh.puts, store.k], [putsBefore, storedBefore], 'no token: nothing written to GitHub or this browser');

// 11b. data.json over 1 MB (GitHub then omits the inline content): read falls back to the raw file.
delete store.pat; gh.overOneMB = true; gh.reads.length = 0;
await api.loadShared();
eq([api.matches.map(m => m.id), gh.reads.length, gh.reads[1].Accept], [[1, 2], 2, 'application/vnd.github.v3.raw'],
   'over 1 MB: second request fetches the raw file');
gh.overOneMB = false;

// 11. With a token, save still goes to GitHub.
store.pat = 'tok'; await api.loadFromGitHub(); await new Promise(r => setTimeout(r, 20));
const putsWithToken = gh.puts;
api.matches = [{ id: 100, player: 'New', score: '3–0' }, ...api.matches];
api.save(); await new Promise(r => setTimeout(r, 20));
eq(gh.puts, putsWithToken + 1, 'with a token: save writes to GitHub');
eq(gh.data.map(m => m.id), [100, 1, 2], 'with a token: the new match is on GitHub');

console.log(fails ? `${fails} FAILED` : 'all passed');
process.exit(fails ? 1 : 0);
