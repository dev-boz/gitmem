// Wireframe 3 — Sessions view + Dream pipeline detail
// Left: session list with inline spark activity
// Middle: session transcript (raw log preview with extraction markers)
// Right: what this session contributed (facts added/updated/tombstoned)

function Wire3Sessions() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— sessions / transcript / contributed facts</span></span>
        <span style={{color: 'var(--term-fg-dim)'}}>drill-in</span>
      </div>

      <div className="term-body" style={{padding: 0}}>

        <div style={{padding: '4px 12px', borderBottom: '1px solid var(--term-line)', display: 'flex', gap: 16, fontSize: 12}}>
          <span className="fg-dim">F2 Facts</span>
          <span className="fg-dim">[<span className="fg-bright bold">F3 Sessions</span>]</span>
          <span className="fg-dim">F4 Dream</span>
          <span className="fg-dim">F5 PRs</span>
          <span className="fg-dim">F6 Search</span>
          <span style={{marginLeft: 'auto'}} className="fg-dim">home › sessions › <span className="fg-cyan">codex</span> › <span className="fg-bright">sess_4d21</span></span>
        </div>

        <div style={{display: 'grid', gridTemplateColumns: '280px 1fr 320px', background: 'var(--term-line)', gap: 1}}>

          {/* Session list */}
          <div style={{background: 'var(--term-bg)', padding: '10px 12px', minHeight: 580}}>
            <div className="fg-bright bold">SESSIONS</div>
            <div className="fg-dim">──────────────────────────</div>
            <div className="fg-dim" style={{marginTop: 4}}>today</div>

            <div className="bg-sel-active" style={{padding: 2}}>
              <div><span className="fg-yellow">◉</span> <span className="fg-cyan">codex</span><span className="fg-dim"> · live</span></div>
              <div className="fg-dim">  fix pg pool timeout</div>
              <div><span className="fg-dim">  </span><Spark values={[0.3,0.5,0.8,0.7,0.9,0.6,0.4,0.5,0.7,0.8]} color="var(--ansi-yellow)"/></div>
            </div>

            <div style={{marginTop: 4, padding: 2}}>
              <div><span className="fg-green">●</span> <span className="fg-cyan">claude-code</span><span className="fg-dim"> · 2m</span></div>
              <div className="fg-dim">  refactor auth/ mw</div>
              <div><span className="fg-dim">  </span><Spark values={[0.2,0.4,0.3,0.6,0.8,0.5,0.3,0.2,0.1,0.1]} color="var(--ansi-green)"/></div>
            </div>

            <div style={{marginTop: 4, padding: 2}}>
              <div><span className="fg-dim">○</span> <span className="fg-cyan">copilot</span><span className="fg-dim"> · 14m</span></div>
              <div className="fg-dim">  rate-limit /search</div>
              <div><span className="fg-dim">  </span><Spark values={[0.1,0.3,0.2,0.4,0.3,0.2,0.1,0,0,0]} color="var(--ansi-blue)"/></div>
            </div>

            <div className="fg-dim" style={{marginTop: 10}}>yesterday</div>

            <div style={{padding: 2}}>
              <div><span className="fg-dim">○</span> <span className="fg-cyan">gemini-cli</span></div>
              <div className="fg-dim">  memory leak hunt</div>
            </div>
            <div style={{padding: 2}}>
              <div><span className="fg-dim">○</span> <span className="fg-cyan">opencode</span></div>
              <div className="fg-dim">  doc sweep</div>
            </div>
            <div style={{padding: 2}}>
              <div><span className="fg-dim">○</span> <span className="fg-cyan">codex</span></div>
              <div className="fg-dim">  add /metrics endpoint</div>
            </div>

            <div className="fg-dim" style={{marginTop: 10}}>this week</div>
            <div className="fg-faint" style={{padding: 2}}>○ claude-code · 41 more…</div>
          </div>

          {/* Transcript */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px', minHeight: 580, position: 'relative'}}>

            <div style={{display: 'flex', alignItems: 'baseline'}}>
              <span className="fg-bright bold">sess_4d21</span>
              <span className="fg-dim"> · codex · started 00:42 ago · </span>
              <span className="fg-yellow">● recording</span>
              <span className="fg-dim" style={{marginLeft: 'auto'}}>14,208 tokens</span>
            </div>

            <div className="fg-dim">──────────────────────────────────────────────────────────────────────</div>

            <div style={{marginTop: 6}}>
              <div><span className="fg-faint">00:01 </span><span className="fg-magenta">user</span><span className="fg-dim"> › </span><span>pg connections keep dying after 30s. what's set?</span></div>
              <div><span className="fg-faint">00:02 </span><span className="fg-green">codex</span><span className="fg-dim"> › </span><span>let me check the pool config…</span></div>
              <div><span className="fg-faint">00:02 </span><span className="fg-cyan">tool </span><span className="fg-dim">· </span><span className="fg-yellow">read_file</span><span className="fg-dim">  src/db/pool.py</span></div>
              <div><span className="fg-faint">00:03 </span><span className="fg-green">codex</span><span className="fg-dim"> › </span><span>found it. `command_timeout=30` on line 12.</span></div>

              {/* Extraction marker */}
              <div style={{marginTop: 6, padding: '4px 8px', background: '#0a140a', borderLeft: '2px solid var(--ansi-green)'}}>
                <div><span className="fg-green">▸ extracted </span><span className="fg-dim">(live)</span></div>
                <div><span className="fg-dim">  </span><Strength value={4}/><span className="fg-dim"> </span><span className="fg-bright">pg pool timeout is 30s</span></div>
                <div><span className="fg-dim">  source: src/db/pool.py:12 (AST) · will propose on session end</span></div>
              </div>

              <div style={{marginTop: 6}}><span className="fg-faint">00:14 </span><span className="fg-magenta">user</span><span className="fg-dim"> › </span><span>bump to 60 for staging only</span></div>
              <div><span className="fg-faint">00:15 </span><span className="fg-green">codex</span><span className="fg-dim"> › </span><span>editing config/staging.toml…</span></div>
              <div><span className="fg-faint">00:15 </span><span className="fg-cyan">tool </span><span className="fg-dim">· </span><span className="fg-yellow">edit_file</span><span className="fg-dim"> config/staging.toml +3 −1</span></div>

              <div style={{marginTop: 6, padding: '4px 8px', background: '#0a140a', borderLeft: '2px solid var(--ansi-green)'}}>
                <div><span className="fg-green">▸ extracted</span></div>
                <div><span className="fg-dim">  </span><Strength value={3}/><span className="fg-dim"> </span>staging pg timeout is 60s</div>
                <div><span className="fg-dim">  supersedes candidate: fct_9aa0 (would conflict with fct_8f3c2a)</span></div>
              </div>

              <div style={{marginTop: 6}}><span className="fg-faint">00:28 </span><span className="fg-magenta">user</span><span className="fg-dim"> › </span><span>also ignore CORS warnings in dev, they're safe</span></div>
              <div><span className="fg-faint">00:28 </span><span className="fg-green">codex</span><span className="fg-dim"> › </span><span>noted.</span></div>

              <div style={{marginTop: 6, padding: '4px 8px', background: '#1a140a', borderLeft: '2px solid var(--ansi-yellow)'}}>
                <div><span className="fg-yellow">▸ extracted (low confidence)</span></div>
                <div><span className="fg-dim">  </span><Strength value={2}/><span className="fg-dim"> </span>CORS warnings safe in dev</div>
                <div><span className="fg-dim">  → needs code-level confirmation before S ≥ 3</span></div>
              </div>

              <div style={{marginTop: 6}}><span className="fg-faint">00:42 </span><span className="fg-dim">… stream continues</span></div>
            </div>

            <Annot x={540} y={60} tone="green" rotate={-2} w={220}>
              live extraction markers — <br/>agent annotates the stream as it runs
            </Annot>
          </div>

          {/* Contributed facts */}
          <div style={{background: 'var(--term-bg)', padding: '10px 12px', minHeight: 580}}>
            <div className="fg-bright bold">CONTRIBUTIONS</div>
            <div className="fg-dim">──────────────────────</div>

            <div className="fg-dim" style={{marginTop: 6}}>proposed this session</div>
            <div><Strength value={4}/><span className="fg-dim"> </span>pg pool timeout 30s</div>
            <div className="fg-dim">  → PR #42 (pending)</div>
            <div style={{marginTop: 4}}><Strength value={3}/><span className="fg-dim"> </span>staging timeout 60s</div>
            <div className="fg-dim">  → PR #42 (pending)</div>
            <div style={{marginTop: 4}}><Strength value={2}/><span className="fg-dim"> </span>CORS safe in dev</div>
            <div className="fg-dim">  → needs evidence</div>

            <div className="fg-dim" style={{marginTop: 10}}>──────────────────────</div>
            <div className="fg-dim" style={{marginTop: 4}}>retrieved (injected)</div>
            <div><span className="fg-green">▸</span><span className="fg-dim"> </span>asyncpg version pin</div>
            <div><span className="fg-green">▸</span><span className="fg-dim"> </span>pool size = 10</div>
            <div><span className="fg-green">▸</span><span className="fg-dim"> </span>db schema v14</div>
            <div className="fg-dim">  5 facts, 312 tokens</div>

            <div className="fg-dim" style={{marginTop: 10}}>──────────────────────</div>
            <div className="fg-dim" style={{marginTop: 4}}>stats</div>
            <div><span className="fg-dim">tools used   </span><span className="fg-bright">4</span></div>
            <div><span className="fg-dim">files read   </span><span className="fg-bright">12</span></div>
            <div><span className="fg-dim">files edited </span><span className="fg-bright">3</span></div>
            <div><span className="fg-dim">duration     </span><span className="fg-bright">42m</span></div>

            <div style={{marginTop: 12, padding: 6, border: '1px solid var(--term-line)'}}>
              <div className="fg-dim">on session end →</div>
              <div className="fg-cyan">  umx dream --live</div>
              <div className="fg-dim">  auto-runs in 5m</div>
            </div>
          </div>
        </div>

        <Hints items={[
          ['←→','panes '], ['↑↓','nav '], ['↵','expand '], ['d','run dream '],
          ['i','inject preview '], ['r','replay '], ['Esc','back']
        ]}/>
      </div>
    </div>
  );
}

window.Wire3Sessions = Wire3Sessions;
