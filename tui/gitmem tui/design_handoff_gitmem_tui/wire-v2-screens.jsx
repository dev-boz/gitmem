// V2 drill-in screens — Facts / Sessions / Dream / PRs
// All use NavBar + SubNav + Link components

// ─── FACTS (F3) ────────────────────────────────────────────
function Wire2FactsV2() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— facts</span></span>
      </div>
      <NavBar active="F3"/>
      <SubNav items={[
        ['all', '1,284', null, true],
        ['conflicted', 3, 'red', false],
        ['tombstoned', 14, null, false],
        ['superseded', 28, null, false],
        ['procedures', 6, 'cyan', false],
      ]} right={<span className="fg-dim">scope: <span className="fg-cyan">project/acme-api</span></span>}/>

      <div className="term-body" style={{padding: 0, display: 'grid', gridTemplateColumns: '200px 340px 1fr', background: 'var(--term-line)', gap: 1}}>

        <div style={{background: 'var(--term-bg)', padding: '8px 10px', minHeight: 540}}>
          <div className="fg-bright bold">FILTERS</div>
          <div className="fg-dim">────────────</div>
          <div style={{marginTop: 4}} className="fg-bright">scope</div>
          <div><span className="fg-dim">[x] </span>project/acme</div>
          <div><span className="fg-dim">[ ] </span>user</div>
          <div><span className="fg-dim">[ ] </span>tool</div>
          <div><span className="fg-dim">[x] </span>folder/*</div>
          <div style={{marginTop: 6}} className="fg-bright">strength</div>
          <div><span className="fg-dim">min </span><span className="fg-yellow">2</span><span className="fg-dim"> max </span><span className="fg-green">5</span></div>
          <div style={{marginTop: 6}} className="fg-bright">source</div>
          <div><span className="fg-dim">[x] </span>claude-code</div>
          <div><span className="fg-dim">[x] </span>codex</div>
          <div><span className="fg-dim">[ ] </span>copilot</div>
          <div className="fg-dim" style={{marginTop: 10}}>────────────</div>
          <div><span className="fg-dim">matches </span><span className="fg-bright bold">684</span></div>
        </div>

        <div style={{background: 'var(--term-bg)', padding: '8px 10px', minHeight: 540}}>
          <div className="fg-dim">/pool timeout</div>
          <div className="fg-dim">──────────────────────────────────</div>
          <div className="bg-sel-active" style={{padding: '1px 0'}}>
            <div><span style={{color: 'var(--s4)'}}>▮4</span><span className="fg-dim"> </span><span className="fg-bright">pg pool timeout 30s</span></div>
            <div className="fg-dim">   <Link href="#">db/pool.py</Link>   2m</div>
          </div>
          <div style={{padding: '1px 0'}}>
            <div><span style={{color: 'var(--s5)'}}>▮5</span><span className="fg-dim"> </span>postgres on :5433 dev <span className="fg-red">⚑</span></div>
            <div className="fg-dim">   project/acme-api  1h</div>
          </div>
          <div style={{padding: '1px 0'}}>
            <div><span style={{color: 'var(--s3)'}}>▮3</span><span className="fg-dim"> </span>auth mw ignores /health</div>
            <div className="fg-dim">   <Link href="#">auth/mw.py</Link>   3h</div>
          </div>
          <div style={{padding: '1px 0'}}>
            <div><span style={{color: 'var(--s5)'}}>▮5</span><span className="fg-dim"> </span>rate limit 100/min /search</div>
            <div className="fg-dim">   folder/api       1d</div>
          </div>
          <div style={{padding: '1px 0'}}>
            <div><span style={{color: 'var(--s4)'}}>▮4</span><span className="fg-dim"> </span>tests run via uv run pytest <span className="fg-red">⚑</span></div>
            <div className="fg-dim">   project/acme-api  1d</div>
          </div>
          <div style={{padding: '1px 0'}}>
            <div><span style={{color: 'var(--s3)'}}>▮3</span><span className="fg-dim"> </span>redis for session cache</div>
            <div className="fg-dim">   folder/cache     2d</div>
          </div>
        </div>

        <div style={{background: 'var(--term-bg)', padding: '10px 14px', minHeight: 540}}>
          <div>
            <span className="fg-dim">id </span><span className="fg-magenta">fct_8f3c2a</span>
            <span className="fg-dim"> · </span><span style={{color: 'var(--s4)'}}>S:4</span>
            <span className="fg-dim"> · </span><Link href="#">file/db/pool.py</Link>
          </div>
          <div style={{marginTop: 10, padding: '8px 12px', background: '#0a0f1a', borderLeft: '2px solid var(--ansi-green)'}}>
            <div className="fg-bright bold">pg pool timeout is 30s</div>
            <div className="fg-dim" style={{marginTop: 4}}>
              Connection pool sets command_timeout=30 in <Link href="#">src/db/pool.py:12</Link>.
              Applies to dev and prod. Staging uses 60s — see <Link href="#">fct_1b4e</Link>.
            </div>
          </div>

          <div style={{marginTop: 10}}>
            <div className="fg-dim">composite score</div>
            <div><span className="fg-dim">trust     </span><Bar value={0.8} width={24} color="var(--ansi-green)"/><span className="fg-dim"> 0.80</span></div>
            <div><span className="fg-dim">relevance </span><Bar value={0.92} width={24} color="var(--ansi-cyan)"/><span className="fg-dim"> 0.92</span></div>
            <div><span className="fg-dim">retention </span><Bar value={0.68} width={24} color="var(--ansi-yellow)"/><span className="fg-dim"> 0.68</span></div>
            <div><span className="fg-dim">          </span><span className="fg-bright bold">= 0.81</span></div>
          </div>

          <div style={{marginTop: 12}}>
            <div className="fg-bright">┌─ provenance ────────────────────────────────────────────────┐</div>
            <div><span className="fg-bright">│</span><span className="fg-dim"> extracted </span><span className="fg-magenta">haiku-4.5</span><span className="fg-dim"> from </span><Link href="#">sess_4d21</Link><span className="fg-dim"> (codex)     </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│</span><span className="fg-dim"> approved  </span><span className="fg-magenta">sonnet-4.5</span><span className="fg-dim"> in </span><Link kind="pr" href="#">PR #38</Link><span className="fg-dim"> · merged 2d ago         </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│</span><span className="fg-dim"> sources   </span><span className="fg-green">▸</span><span className="fg-dim"> </span><Link href="#">src/db/pool.py:12</Link><span className="fg-dim">  (AST)                 </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│</span><span className="fg-dim">           </span><span className="fg-green">▸</span><span className="fg-dim"> </span><Link href="#">README.md#operations</Link><span className="fg-dim">                       </span><span className="fg-bright">│</span></div>
            <div className="fg-bright">└─────────────────────────────────────────────────────────────┘</div>
          </div>

          <div style={{marginTop: 10}}>
            <div className="fg-bright">┌─ history ──────────────────────────────────────────── git ─┐</div>
            <div><span className="fg-bright">│</span><span className="fg-green"> ● </span><Link kind="gh" href="#">a3f2b1c</Link><span className="fg-dim"> 2d  tighten wording              </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│</span><span className="fg-green"> ● </span><Link kind="gh" href="#">1c9d04e</Link><span className="fg-dim"> 9d  promoted S:3 → S:4           </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│</span><span className="fg-dim"> ● </span><Link kind="gh" href="#">d71a0be</Link><span className="fg-dim"> 14d initial extraction           </span><span className="fg-bright">│</span></div>
            <div className="fg-bright">└────────────────────────────────────────────────────────────┘</div>
          </div>
        </div>
      </div>

      <Hints items={[
        ['←→','panes '], ['↑↓','nav '], ['↵','open '], ['e','edit '], ['t','tombstone '],
        ['o','open on GitHub '], ['f','open file '], ['Esc','back']
      ]}/>
    </div>
  );
}

// ─── SESSIONS (F4) ────────────────────────────────────────────
function Wire3SessionsV2() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— sessions</span></span>
      </div>
      <NavBar active="F4"/>
      <SubNav items={[
        ['all', 47, null, true],
        ['codex', 14, null, false],
        ['claude', 22, null, false],
        ['copilot', 7, null, false],
        ['gemini', 3, null, false],
        ['opencode', 1, null, false],
        ['live', 1, 'yellow', false],
      ]} right={<span className="fg-dim">sort <span className="fg-bright">recent</span> · 1–10 of 47</span>}/>

      <div className="term-body" style={{padding: 0, display: 'grid', gridTemplateColumns: '280px 1fr 320px', background: 'var(--term-line)', gap: 1}}>

        <div style={{background: 'var(--term-bg)', padding: '10px 12px', minHeight: 540}}>
          <div className="fg-dim">today</div>
          <div className="bg-sel-active" style={{padding: 2}}>
            <div><span className="fg-yellow">◉</span> <span className="fg-cyan">codex</span><span className="fg-dim"> · live</span></div>
            <div className="fg-dim">  fix pg pool timeout</div>
            <div><Spark values={[0.3,0.5,0.8,0.7,0.9,0.6,0.4,0.5,0.7,0.8]} color="var(--ansi-yellow)"/></div>
          </div>
          <div style={{padding: 2, marginTop: 4}}>
            <div><span className="fg-green">●</span> <span className="fg-cyan">claude-code</span><span className="fg-dim"> · 2m</span></div>
            <div className="fg-dim">  refactor auth/ mw</div>
          </div>
          <div style={{padding: 2}}>
            <div><span className="fg-dim">○</span> <span className="fg-cyan">copilot</span><span className="fg-dim"> · 14m</span></div>
            <div className="fg-dim">  rate-limit /search</div>
          </div>
          <div className="fg-dim" style={{marginTop: 8}}>yesterday</div>
          <div style={{padding: 2}}>
            <div><span className="fg-dim">○</span> <span className="fg-cyan">gemini-cli</span></div>
            <div className="fg-dim">  memory leak</div>
          </div>
          <div style={{padding: 2}}>
            <div><span className="fg-dim">○</span> <span className="fg-cyan">opencode</span></div>
            <div className="fg-dim">  doc sweep</div>
          </div>
        </div>

        <div style={{background: 'var(--term-bg)', padding: '10px 14px', minHeight: 540}}>
          <div style={{display: 'flex', alignItems: 'baseline'}}>
            <span className="fg-bright bold">sess_4d21</span>
            <span className="fg-dim"> · codex · </span>
            <span className="fg-yellow">● recording</span>
            <span style={{marginLeft: 'auto'}}>
              <Link kind="gh" href="#">view raw</Link>
            </span>
          </div>
          <div className="fg-dim">──────────────────────────────────────────────────────────</div>

          <div style={{marginTop: 6}}>
            <div><span className="fg-faint">00:01 </span><span className="fg-magenta">user</span><span className="fg-dim"> › </span>pg connections keep dying after 30s. what's set?</div>
            <div><span className="fg-faint">00:02 </span><span className="fg-green">codex</span><span className="fg-dim"> › </span>checking the pool config…</div>
            <div><span className="fg-faint">00:02 </span><span className="fg-cyan">tool </span><span className="fg-dim">· </span><span className="fg-yellow">read_file</span><span className="fg-dim"> </span><Link href="#">src/db/pool.py</Link></div>
            <div><span className="fg-faint">00:03 </span><span className="fg-green">codex</span><span className="fg-dim"> › </span>found it. command_timeout=30 on line 12.</div>

            <div style={{marginTop: 6, padding: '4px 8px', background: '#0a140a', borderLeft: '2px solid var(--ansi-green)'}}>
              <div><span className="fg-green">▸ extracted</span></div>
              <div><Strength value={4}/><span className="fg-dim"> </span><span className="fg-bright">pg pool timeout is 30s</span></div>
              <div className="fg-dim">  source: <Link href="#">src/db/pool.py:12</Link> (AST)</div>
            </div>

            <div style={{marginTop: 6}}><span className="fg-faint">00:14 </span><span className="fg-magenta">user</span><span className="fg-dim"> › </span>bump to 60 for staging only</div>
            <div><span className="fg-faint">00:15 </span><span className="fg-cyan">tool </span><span className="fg-dim">· </span><span className="fg-yellow">edit_file</span><span className="fg-dim"> </span><Link href="#">config/staging.toml</Link><span className="fg-dim"> +3 −1</span></div>

            <div style={{marginTop: 6, padding: '4px 8px', background: '#0a140a', borderLeft: '2px solid var(--ansi-green)'}}>
              <div><span className="fg-green">▸ extracted</span></div>
              <div><Strength value={3}/><span className="fg-dim"> </span>staging pg timeout is 60s</div>
            </div>

            <div style={{marginTop: 6, padding: '4px 8px', background: '#1a140a', borderLeft: '2px solid var(--ansi-yellow)'}}>
              <div><span className="fg-yellow">▸ low confidence</span></div>
              <div><Strength value={2}/><span className="fg-dim"> </span>CORS warnings safe in dev</div>
            </div>
          </div>
        </div>

        <div style={{background: 'var(--term-bg)', padding: '10px 12px', minHeight: 540}}>
          <div className="fg-bright bold">CONTRIBUTIONS</div>
          <div className="fg-dim">──────────────────</div>
          <div className="fg-dim" style={{marginTop: 6}}>proposed</div>
          <div><Strength value={4}/><span className="fg-dim"> </span>pg pool timeout 30s</div>
          <div className="fg-dim">  → <Link kind="pr" href="#">PR #42</Link></div>
          <div style={{marginTop: 4}}><Strength value={3}/><span className="fg-dim"> </span>staging timeout 60s</div>
          <div className="fg-dim">  → <Link kind="pr" href="#">PR #42</Link></div>
          <div style={{marginTop: 4}}><Strength value={2}/><span className="fg-dim"> </span>CORS safe in dev</div>
          <div className="fg-dim">  → needs evidence</div>

          <div className="fg-dim" style={{marginTop: 10}}>retrieved (injected)</div>
          <div>▸ asyncpg version pin</div>
          <div>▸ pool size = 10</div>
          <div>▸ db schema v14</div>

          <div className="fg-dim" style={{marginTop: 10}}>stats</div>
          <div><span className="fg-dim">files read   </span><span className="fg-bright">12</span></div>
          <div><span className="fg-dim">files edited </span><span className="fg-bright">3</span></div>
          <div><span className="fg-dim">duration     </span><span className="fg-bright">42m</span></div>

          <div style={{marginTop: 12, padding: 6, border: '1px solid var(--term-line)'}}>
            <div className="fg-dim">on session end →</div>
            <div><span className="fg-cyan">umx dream --live</span></div>
          </div>
        </div>
      </div>

      <Hints items={[
        ['←→','panes '], ['↑↓','nav '], ['↵','expand '], ['f','open file '],
        ['o','raw log '], ['r','replay '], ['Esc','back']
      ]}/>
    </div>
  );
}

// ─── DREAM (F5) ────────────────────────────────────────────
function Wire5DreamV2() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— dream pipeline</span></span>
      </div>
      <NavBar active="F5"/>
      <SubNav items={[
        ['live', null, 'yellow', true],
        ['history', 18, null, false],
        ['schedule', null, null, false],
        ['config', null, null, false],
      ]} right={<span><span className="fg-dim">extractor </span><span className="fg-magenta">haiku-4.5</span><span className="fg-dim"> · reviewer </span><span className="fg-magenta">sonnet-4.5</span></span>}/>

      <div className="term-body" style={{padding: 14}}>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24}}>
          <div>
            <div className="fg-bright bold">┌─ pipeline · t+00:42 ──────────────────────────┐</div>
            <div><span className="fg-bright">│ </span><span className="fg-green">✓ orient      </span><Bar value={1} width={22} color="var(--ansi-green)"/><span className="fg-dim"> 100%  </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│ </span><span className="fg-green">✓ gather      </span><Bar value={1} width={22} color="var(--ansi-green)"/><span className="fg-dim"> 100%  </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│ </span><span className="fg-yellow">◉ consolidate </span><Bar value={0.64} width={22} color="var(--ansi-yellow)"/><span className="fg-dim">  64%  </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│ </span><span className="fg-dim">· lint        </span><Bar value={0} width={22} color="var(--term-line)"/><span className="fg-dim">   -   </span><span className="fg-bright">│</span></div>
            <div><span className="fg-bright">│ </span><span className="fg-dim">· prune       </span><Bar value={0} width={22} color="var(--term-line)"/><span className="fg-dim">   -   </span><span className="fg-bright">│</span></div>
            <div className="fg-bright">└───────────────────────────────────────────────┘</div>

            <div style={{marginTop: 12}}>
              <div className="fg-dim">throughput (facts/min)</div>
              <div><Spark values={[0.2,0.4,0.35,0.6,0.5,0.75,0.7,0.85,0.9,0.72]} color="var(--ansi-cyan)"/><span className="fg-dim"> peak </span><span className="fg-cyan">18/min</span></div>
            </div>
          </div>

          <div>
            <div className="fg-bright bold">results so far</div>
            <div className="fg-dim">─────────────────────────────────</div>
            <div><Health state="ok" label="proposed" count={24}/></div>
            <div><Health state="ok" label="auto-merged" count={9}/></div>
            <div><Health state="warn" label="deduped" count={7}/></div>
            <div><Health state="bad" label="conflicts" count={3}/></div>

            <div style={{marginTop: 12}}>
              <div className="fg-dim">opened PRs</div>
              <div><Link kind="pr" href="#">PR #42</Link><span className="fg-dim"> — 12 auth facts</span></div>
              <div><Link kind="pr" href="#">PR #43</Link><span className="fg-dim"> — 5 pg-index facts</span></div>
            </div>

            <div style={{marginTop: 12}}>
              <div className="fg-dim">sessions consumed</div>
              <div><Link href="#">sess_4d21</Link><span className="fg-dim">, </span><Link href="#">sess_4d19</Link><span className="fg-dim">, +12 more</span></div>
            </div>
          </div>
        </div>

        <div style={{marginTop: 20, padding: 10, border: '1px solid var(--term-line)', display: 'flex', gap: 16}}>
          <div style={{flex: 1}}>
            <div className="fg-dim">next auto-run</div>
            <div className="fg-bright">in 4h 17m · cron 0 */6</div>
          </div>
          <div style={{flex: 1}}>
            <div className="fg-dim">last 7 runs</div>
            <div><Spark values={[0.3,0.5,0.4,0.6,0.7,0.5,0.9]} color="var(--ansi-green)"/><span className="fg-dim"> avg 4m 12s</span></div>
          </div>
          <div>
            <div className="fg-dim">&nbsp;</div>
            <div style={{padding: '2px 10px', border: '1px solid var(--ansi-cyan)', color: 'var(--ansi-cyan)', fontWeight: 700}}>[ d  run now ]</div>
          </div>
        </div>
      </div>

      <Hints items={[
        ['d','run now '], ['l','logs '], ['s','schedule '], ['c','config '], ['Esc','back']
      ]}/>
    </div>
  );
}

// ─── PRs (F6) ────────────────────────────────────────────
function Wire6PRsV2() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— PRs</span></span>
      </div>
      <NavBar active="F6"/>
      <SubNav items={[
        ['pending', 5, 'yellow', true],
        ['conflicts', 3, 'red', false],
        ['merged', 128, 'green', false],
        ['closed', 12, null, false],
        ['all', 148, null, false],
      ]} right={<span><Link kind="gh" href="#">github.com/acme/memory/pulls</Link></span>}/>

      <div className="term-body" style={{padding: 0, display: 'grid', gridTemplateColumns: '1fr 420px', background: 'var(--term-line)', gap: 1}}>

        <div style={{background: 'var(--term-bg)', padding: '10px 14px', minHeight: 540}}>
          <div style={{display: 'flex'}}>
            <span className="fg-yellow bold">#42</span>
            <span className="fg-dim"> </span>
            <span className="fg-magenta">[dream/L1]</span>
            <span className="fg-bright bold"> &nbsp;12 auth facts</span>
            <span style={{marginLeft: 'auto'}}><Link kind="pr" href="#">github ↗</Link></span>
          </div>
          <div className="fg-dim">
            opened <span className="fg-bright">12m</span> ago · <span className="fg-magenta">dream-bot[haiku-4.5]</span>
            {' · '}<span className="fg-yellow">◐ L2 review requested</span>
          </div>
          <div className="fg-dim">──────────────────────────────────────────────────────────────</div>

          <div className="fg-dim">memory/project/acme-api/auth.md</div>
          <div><span className="fg-green">+ </span><span className="fg-green">F · auth mw skips /health and /metrics</span></div>
          <div><span className="fg-green">+ </span><span className="fg-dim">  </span><Strength value={3}/><span className="fg-dim"> src: </span><Link href="#">auth/mw.py:34</Link></div>
          <div><span className="fg-green">+ </span><span className="fg-green">F · JWT expiry 15m, refresh 7d</span></div>
          <div><span className="fg-green">+ </span><span className="fg-dim">  </span><Strength value={4}/><span className="fg-dim"> src: </span><Link href="#">auth/jwt.py:8</Link></div>
          <div><span className="fg-yellow">± </span><span className="fg-yellow">U · pg pool timeout 30s</span><span className="fg-dim"> (S:3 → S:4)</span></div>
          <div style={{marginTop: 4}}><span className="fg-red">⚑ </span><span className="fg-red bold">C · postgres port 5432 vs 5433</span></div>
          <div><span className="fg-red">⚑ </span><span className="fg-dim">  needs human — see right pane</span></div>

          <div className="fg-dim" style={{marginTop: 10}}>──────────────────────────────────────────────────────────────</div>
          <div className="fg-dim">reviewer (L2) <span className="fg-magenta">sonnet-4.5</span></div>
          <div style={{padding: '4px 10px', background: '#0a0f1a', borderLeft: '2px solid var(--ansi-blue)'}}>
            <div className="fg-bright">All 12 facts extractable from sources.</div>
            <div className="fg-dim">1. rate-limit-admin is S:2 — single session. Mark tentative.</div>
            <div className="fg-dim">2. pg port is a <span className="fg-bright">genuine contradiction</span>; ask human.</div>
            <div className="fg-dim">3. JWT expiry may dedup with user-scope fact.</div>
          </div>
        </div>

        <div style={{background: 'var(--term-bg)', padding: '10px 12px', minHeight: 540}}>
          <div className="fg-red bold">⚑ conflict: postgres port</div>
          <div className="fg-dim">────────────────────────────────</div>

          <div style={{marginTop: 8, padding: 6, border: '1px solid var(--term-line)'}}>
            <div><span className="fg-cyan">( ) A</span><span className="fg-bright"> port 5432</span></div>
            <div className="fg-dim">  <Strength value={4}/> <Link href="#">README.md</Link> · ops</div>
            <div><span className="fg-dim">  composite </span><Bar value={0.72} width={14} color="var(--ansi-green)"/><span className="fg-dim"> 0.72</span></div>
          </div>
          <div style={{marginTop: 4, padding: 6, border: '1.5px solid var(--ansi-cyan)', background: '#07131a'}}>
            <div><span className="fg-cyan">(●) B</span><span className="fg-bright"> port 5433</span></div>
            <div className="fg-dim">  <Strength value={3}/> <Link href="#">docker-compose.yml</Link></div>
            <div><span className="fg-dim">  composite </span><Bar value={0.68} width={14} color="var(--ansi-yellow)"/><span className="fg-dim"> 0.68</span></div>
          </div>
          <div style={{marginTop: 4, padding: 6, border: '1px solid var(--term-line)'}}>
            <div><span className="fg-cyan">( ) C</span><span> scope-split (prod/dev)</span></div>
          </div>
          <div style={{marginTop: 4, padding: 6, border: '1px solid var(--term-line)'}}>
            <div><span className="fg-cyan">( ) D</span><span> tombstone both</span></div>
          </div>

          <div style={{marginTop: 10, padding: 6, border: '1px solid var(--ansi-green)', background: '#0a140a'}}>
            <div className="fg-green bold">[ ↵ apply & merge PR #42 ]</div>
            <div className="fg-dim">  squash · signed · 1/1 resolved</div>
          </div>
        </div>
      </div>

      <Hints items={[
        ['↑↓','PR '], ['a/b/c/d','pick '], ['↵','apply '],
        ['o','github '], ['x','reject '], ['Esc','back']
      ]}/>
    </div>
  );
}

Object.assign(window, { Wire2FactsV2, Wire3SessionsV2, Wire5DreamV2, Wire6PRsV2 });
