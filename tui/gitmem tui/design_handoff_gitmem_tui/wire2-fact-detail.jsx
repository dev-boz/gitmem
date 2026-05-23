// Wireframe 2 — Drill-down: Facts list + Fact detail (provenance & history)
// Lazygit-ish split: narrow filter rail | facts list | detail pane

function Wire2FactDetail() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— facts / detail</span></span>
        <span style={{color: 'var(--term-fg-dim)'}}>drill-in</span>
      </div>

      <div className="term-body" style={{padding: 0}}>

        {/* Breadcrumb / tab strip */}
        <div style={{padding: '4px 12px', borderBottom: '1px solid var(--term-line)', display: 'flex', gap: 16, fontSize: 12}}>
          <span className="fg-dim">[<span className="fg-bright bold">F2 Facts</span>]</span>
          <span className="fg-dim">F3 Sessions</span>
          <span className="fg-dim">F4 Dream</span>
          <span className="fg-dim">F5 PRs</span>
          <span className="fg-dim">F6 Search</span>
          <span style={{marginLeft: 'auto'}} className="fg-dim">home › <span className="fg-cyan">facts</span> › <span className="fg-bright">pg_pool_timeout</span></span>
        </div>

        <div style={{display: 'grid', gridTemplateColumns: '200px 340px 1fr', background: 'var(--term-line)', gap: 1}}>

          {/* ── Filter rail ─────────────── */}
          <div style={{background: 'var(--term-bg)', padding: '8px 10px', minHeight: 560}}>
            <div className="fg-dim bold">FILTERS</div>
            <div className="fg-dim">─────────────</div>
            <div style={{marginTop: 6}}>
              <div className="fg-bright">scope</div>
              <div><span className="fg-dim">  [x] </span>project/acme</div>
              <div><span className="fg-dim">  [ ] </span>user</div>
              <div><span className="fg-dim">  [ ] </span>tool</div>
              <div><span className="fg-dim">  [x] </span>folder/*</div>
              <div><span className="fg-dim">  [x] </span>file/*</div>
            </div>
            <div style={{marginTop: 8}}>
              <div className="fg-bright">strength</div>
              <div><span className="fg-dim">  </span>min <span className="fg-yellow">2</span></div>
              <div><span className="fg-dim">  </span>max <span className="fg-green">5</span></div>
              <div><span className="fg-dim">  </span><Strength value={5}/></div>
            </div>
            <div style={{marginTop: 8}}>
              <div className="fg-bright">status</div>
              <div><span className="fg-dim">  [x] </span>active</div>
              <div><span className="fg-dim">  [ ] </span>tombstoned</div>
              <div><span className="fg-dim">  [x] </span>conflicted</div>
              <div><span className="fg-dim">  [ ] </span>superseded</div>
            </div>
            <div style={{marginTop: 8}}>
              <div className="fg-bright">source</div>
              <div><span className="fg-dim">  [x] </span>claude-code</div>
              <div><span className="fg-dim">  [x] </span>codex</div>
              <div><span className="fg-dim">  [ ] </span>copilot</div>
              <div><span className="fg-dim">  [ ] </span>gemini</div>
              <div><span className="fg-dim">  [ ] </span>opencode</div>
            </div>
            <div style={{marginTop: 10, color: 'var(--term-fg-dim)'}}>─────────────</div>
            <div><span className="fg-dim">matches </span><span className="fg-bright bold">684</span><span className="fg-dim"> / 1,284</span></div>
          </div>

          {/* ── Facts list ─────────────── */}
          <div style={{background: 'var(--term-bg)', padding: '8px 10px', minHeight: 560}}>
            <div style={{display: 'flex'}}>
              <span className="fg-dim">/</span>
              <span className="fg-bright bold">pool timeout</span>
              <span className="fg-dim" style={{marginLeft: 'auto'}}>↑↓ sort</span>
            </div>
            <div className="fg-dim">──────────────────────────────────</div>

            {/* column headers */}
            <div className="fg-dim" style={{marginTop: 4}}>S  scope               updated</div>

            <div className="bg-sel-active" style={{padding: '1px 0', marginTop: 2}}>
              <div><span style={{color: 'var(--s4)'}}>▮4</span><span className="fg-dim"> </span><span className="fg-bright">pg pool timeout 30s</span></div>
              <div className="fg-dim">   file/db/pool.py          2m</div>
            </div>
            <div style={{padding: '1px 0'}}>
              <div><span style={{color: 'var(--s5)'}}>▮5</span><span className="fg-dim"> </span>postgres on :5433 dev</div>
              <div className="fg-dim">   project/acme-api        1h  </div><span className="fg-red">⚑ conflict</span>
            </div>
            <div style={{padding: '1px 0'}}>
              <div><span style={{color: 'var(--s3)'}}>▮3</span><span className="fg-dim"> </span>auth mw ignores /health</div>
              <div className="fg-dim">   file/auth/mw.py          3h</div>
            </div>
            <div style={{padding: '1px 0'}}>
              <div><span style={{color: 'var(--s2)'}}>▮2</span><span className="fg-dim"> </span>CORS warnings safe-ish</div>
              <div className="fg-dim">   project/acme-api        6h</div>
            </div>
            <div style={{padding: '1px 0'}}>
              <div><span style={{color: 'var(--s5)'}}>▮5</span><span className="fg-dim"> </span>rate limit 100/min /search</div>
              <div className="fg-dim">   folder/api              1d</div>
            </div>
            <div style={{padding: '1px 0'}}>
              <div><span style={{color: 'var(--s4)'}}>▮4</span><span className="fg-dim"> </span>tests run via uv run pytest</div>
              <div className="fg-dim">   project/acme-api        1d  </div><span className="fg-red">⚑</span>
            </div>
            <div style={{padding: '1px 0'}}>
              <div><span style={{color: 'var(--s3)'}}>▮3</span><span className="fg-dim"> </span>redis for session cache</div>
              <div className="fg-dim">   folder/cache            2d</div>
            </div>
            <div style={{padding: '1px 0', opacity: 0.45}}>
              <div><span style={{color: 'var(--s1)'}}>▮1</span><span className="fg-dim"> </span>possibly uses protobuf</div>
              <div className="fg-dim">   project/acme-api        5d  </div><span className="fg-dim">tombstoned</span>
            </div>
            <div style={{padding: '1px 0'}}>
              <div><span style={{color: 'var(--s4)'}}>▮4</span><span className="fg-dim"> </span>pytest-xdist -n auto CI only</div>
              <div className="fg-dim">   tool/codex              7d</div>
            </div>

            <div className="fg-dim" style={{marginTop: 8}}>──────────────────────────────────</div>
            <div className="fg-dim">1 / 684 &nbsp;&nbsp;&nbsp; ↑↓ nav &nbsp; ↵ open &nbsp; t tombstone</div>
          </div>

          {/* ── Detail pane ─────────────── */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px', minHeight: 560, position: 'relative'}}>

            <div>
              <span className="fg-dim">id </span><span className="fg-magenta">fct_8f3c2a</span>
              <span className="fg-dim"> · </span><span style={{color: 'var(--s4)'}}>S:4</span>
              <span className="fg-dim"> · </span><span className="fg-cyan">file/db/pool.py</span>
              <span className="fg-dim" style={{marginLeft: 12}}>updated 2m ago by </span><span className="fg-magenta">dream/L2</span>
            </div>

            <div style={{marginTop: 10, padding: '8px 12px', background: '#0a0f1a', borderLeft: '2px solid var(--ansi-green)'}}>
              <div className="fg-bright bold">pg pool timeout is 30s</div>
              <div className="fg-dim" style={{marginTop: 4}}>
                Connection pool for Postgres (asyncpg) sets statement_timeout=30s
                {'\n'}via `create_pool(..., command_timeout=30)`. Applies to dev and
                {'\n'}prod. Staging uses 60s — see related fact fct_1b4e.
              </div>
            </div>

            {/* composite score bar */}
            <div style={{marginTop: 10}}>
              <div className="fg-dim">composite score</div>
              <div>
                <span className="fg-dim">trust     </span><Bar value={0.8} width={28} color="var(--ansi-green)"/><span className="fg-dim"> 0.80</span>
              </div>
              <div>
                <span className="fg-dim">relevance </span><Bar value={0.92} width={28} color="var(--ansi-cyan)"/><span className="fg-dim"> 0.92</span>
              </div>
              <div>
                <span className="fg-dim">retention </span><Bar value={0.68} width={28} color="var(--ansi-yellow)"/><span className="fg-dim"> 0.68</span>
              </div>
              <div>
                <span className="fg-dim">          </span><span className="fg-bright bold">= 0.81</span><span className="fg-dim"> (top 12%)</span>
              </div>
            </div>

            {/* Provenance */}
            <div style={{marginTop: 12}}>
              <div className="fg-bright">┌─ provenance ─────────────────────────────────────────────────┐</div>
              <div><span className="fg-bright">│</span><span className="fg-dim"> extracted  </span><span className="fg-magenta">haiku-4.5</span><span className="fg-dim"> from session </span><span className="fg-cyan">sess_4d21</span><span className="fg-dim"> (codex)     </span><span className="fg-bright">│</span></div>
              <div><span className="fg-bright">│</span><span className="fg-dim"> approved   </span><span className="fg-magenta">sonnet-4.5</span><span className="fg-dim"> in </span><span className="fg-yellow">PR #38</span><span className="fg-dim"> · merged 2d ago             </span><span className="fg-bright">│</span></div>
              <div><span className="fg-bright">│</span><span className="fg-dim"> sources    </span><span className="fg-green">▸</span><span className="fg-dim"> src/db/pool.py:12  (AST parse)                  </span><span className="fg-bright">│</span></div>
              <div><span className="fg-bright">│</span><span className="fg-dim">            </span><span className="fg-green">▸</span><span className="fg-dim"> README.md#operations                             </span><span className="fg-bright">│</span></div>
              <div><span className="fg-bright">│</span><span className="fg-dim"> confirmed  </span><span className="fg-green">●</span><span className="fg-dim"> 3 sessions, 2 tools (claude, codex)              </span><span className="fg-bright">│</span></div>
              <div className="fg-bright">└──────────────────────────────────────────────────────────────┘</div>
            </div>

            {/* History */}
            <div style={{marginTop: 10}}>
              <div className="fg-bright">┌─ history ─────────────────────────────────────────────── git ┐</div>
              <div><span className="fg-bright">│</span><span className="fg-green"> ● </span><span className="fg-yellow">a3f2b1c</span><span className="fg-dim"> 2d  </span><span>tighten wording, add staging note      </span><span className="fg-bright">│</span></div>
              <div><span className="fg-bright">│</span><span className="fg-dim"> │</span></div>
              <div><span className="fg-bright">│</span><span className="fg-green"> ● </span><span className="fg-yellow">1c9d04e</span><span className="fg-dim"> 9d  </span><span>promoted S:3 → S:4 (ast-confirmed)     </span><span className="fg-bright">│</span></div>
              <div><span className="fg-bright">│</span><span className="fg-dim"> │</span></div>
              <div><span className="fg-bright">│</span><span className="fg-dim"> ● </span><span className="fg-yellow">d71a0be</span><span className="fg-dim"> 14d </span><span>initial extraction from sess_2a13      </span><span className="fg-bright">│</span></div>
              <div className="fg-bright">└──────────────────────────────────────────────────────────────┘</div>
            </div>

            {/* Related / conflicts */}
            <div style={{marginTop: 10}}>
              <span className="fg-dim">related: </span>
              <span className="fg-cyan">fct_1b4e</span><span className="fg-dim"> staging timeout 60s · </span>
              <span className="fg-cyan">fct_9aa0</span><span className="fg-dim"> asyncpg version pin</span>
            </div>

            <Annot x={640} y={32} tone="blue" rotate={3} w={220}>
              every fact is a git commit — click to open the diff on GitHub
            </Annot>
          </div>
        </div>

        <Hints items={[
          ['←→','panes '], ['↑↓','nav '], ['↵','expand '], ['e','edit '], ['t','tombstone '],
          ['c','resolve conflict '], ['o','open PR '], ['Esc','back']
        ]}/>
      </div>
    </div>
  );
}

window.Wire2FactDetail = Wire2FactDetail;
