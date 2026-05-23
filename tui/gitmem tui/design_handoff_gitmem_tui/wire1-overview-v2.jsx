// Wireframe v2 — Overview (landing) with all-green-or-red health hero
// Primary nav at top, sub-nav for overview, then hero + cockpit grid

function Wire1OverviewV2({ health = 'ok' }) {
  // health: 'ok' | 'warn' | 'bad'  — toggle to see the "good day" vs "stuff to do"
  const isOk = health === 'ok';
  const isWarn = health === 'warn';

  const heroColor = isOk ? 'var(--ansi-green)' : isWarn ? 'var(--ansi-yellow)' : 'var(--ansi-red)';
  const heroBg    = isOk ? '#0a1f0e' : isWarn ? '#1f1a08' : '#1f0a0a';
  const heroGlyph = isOk ? '✓' : isWarn ? '◐' : '✗';
  const heroTitle = isOk ? 'ALL GREEN' : isWarn ? 'NEEDS ATTENTION' : 'ACTION REQUIRED';
  const heroSub   = isOk ? 'nothing to review · memory is healthy · you can close this window'
                  : isWarn ? '2 low-confidence facts need another confirmation · no blockers'
                  : '3 conflicts block merges · 5 PRs awaiting L2 review';

  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— gitmem cockpit · ~/work/acme-api</span></span>
        <span style={{color: 'var(--term-fg-dim)'}}>80×24 · zsh</span>
      </div>

      <NavBar active="F2"/>
      <SubNav items={[
        ['health', null, null, true],
        ['activity 24h', null, null, false],
        ['activity 7d', null, null, false],
        ['activity 30d', null, null, false],
      ]} right={<span><span className="fg-dim">last sync </span><span className="fg-bright">12s ago</span></span>}/>

      <div className="term-body" style={{padding: 0}}>

        {/* ─── HEALTH HERO (single line) ─── */}
        <div style={{
          padding: '8px 14px',
          background: heroBg,
          borderBottom: '1px solid var(--term-line)',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          fontSize: 13,
        }}>
          <span style={{
            color: heroColor,
            fontWeight: 700,
            textShadow: `0 0 8px ${heroColor}`,
          }}>{heroGlyph}</span>
          <span style={{color: heroColor, fontWeight: 700, letterSpacing: 1}}>{heroTitle}</span>
          <span className="fg-dim" style={{flex: 1}}>— {heroSub}</span>
          <span>
            <Health state={isOk ? 'ok' : 'bad'} label="conflicts" count={isOk ? 0 : 3}/>
            <span className="fg-dim" style={{margin: '0 10px'}}>·</span>
            <Health state={isOk ? 'ok' : 'warn'} label="PRs" count={isOk ? 0 : 5}/>
          </span>
        </div>

        {/* ─── Status strip ─── */}
        <div style={{padding: '6px 12px', borderBottom: '1px solid var(--term-line)', display: 'flex', justifyContent: 'space-between', fontSize: 12}}>
          <span>
            <span className="fg-magenta bold">umx</span>
            <span className="fg-dim"> v0.9.2  </span>
            <span className="fg-dim">mode </span><span className="fg-cyan">local</span>
            <span className="fg-dim">  repo </span>
            <Link kind="gh" href="https://github.com/acme/api">acme/api</Link>
            <span className="fg-dim">  memory </span>
            <Link kind="gh" href="https://github.com/acme/memory">acme/memory</Link>
          </span>
          <span>
            <span className="fg-dim">facts </span><span className="fg-bright bold">1,284</span>
            <span className="fg-dim">  sessions </span><span className="fg-bright">47</span>
            <span className="fg-dim">  last dream </span><span className="fg-bright">3h ago</span>
          </span>
        </div>

        {/* Quad grid — kept but condensed under the hero */}
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--term-line)'}}>

          {/* Memory */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px'}}>
            <div className="fg-bright bold">┌─ memory ──────────────────────── 1,284 facts ───────┐</div>
            <div className="fg-dim">│                                                      │</div>
            <div><span className="fg-dim">│   </span><span style={{color: 'var(--s5)'}}>S:5 </span><Bar value={0.32} width={22} color="var(--s5)"/><span className="fg-dim"> 412   code-parsed </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><span style={{color: 'var(--s4)'}}>S:4 </span><Bar value={0.26} width={22} color="var(--s4)"/><span className="fg-dim"> 334   doc-confirmed</span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><span style={{color: 'var(--s3)'}}>S:3 </span><Bar value={0.22} width={22} color="var(--s3)"/><span className="fg-dim"> 281   multi-source </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><span style={{color: 'var(--s2)'}}>S:2 </span><Bar value={0.14} width={22} color="var(--s2)"/><span className="fg-dim"> 178   inferred     </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><span style={{color: 'var(--s1)'}}>S:1 </span><Bar value={0.06} width={22} color="var(--s1)"/><span className="fg-dim">  79   incidental   </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">│                                                      │</div>
            <div><span className="fg-dim">│   top scope: </span><Link href="#">project/acme-api</Link><span className="fg-dim"> · 687 facts   </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">└──────────────────────────────────────────────────────┘</div>
          </div>

          {/* Dream status */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px'}}>
            <div className="fg-bright bold">┌─ dream pipeline ─────────────── idle · 3h ago ──────┐</div>
            <div className="fg-dim">│                                                      │</div>
            <div><span className="fg-dim">│   </span><Health state="ok" label="last run"/><span className="fg-dim">  4m 12s · 24 facts proposed </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><Health state="ok" label="merged     "/><span className="fg-green"> 9</span><span className="fg-dim">   via lint/prune PRs        </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><Health state="warn" label="pending   "/><span className="fg-yellow"> 5</span><span className="fg-dim">   need L2 review            </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><Health state="bad" label="conflicts "/><span className="fg-red"> 3</span><span className="fg-dim">   need human decision       </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">│                                                      │</div>
            <div><span className="fg-dim">│   next auto-run in </span><span className="fg-bright">4h 17m</span><span className="fg-dim">   or press </span><span className="fg-cyan">d</span><span className="fg-dim"> now      </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">│                                                      │</div>
            <div><span className="fg-dim">│   throughput </span><Spark values={[0.2,0.4,0.35,0.6,0.5,0.75,0.7,0.85,0.9,0.72]} color="var(--ansi-cyan)"/><span className="fg-dim">   peak 18/min          </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">└──────────────────────────────────────────────────────┘</div>
          </div>

          {/* Sessions */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px'}}>
            <div className="fg-bright bold">┌─ sessions ─────────────────── 5 tools · 47 total ──┐</div>
            <div className="fg-dim">│                                                     │</div>
            <div><span className="fg-dim">│ </span><span className="fg-green">●</span><span className="fg-dim"> </span><Link href="#session">claude-code</Link><span className="fg-dim">  2m   </span><span>refactor auth mw</span><span className="fg-dim">         </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-yellow">◉</span><span className="fg-dim"> </span><Link href="#session">codex</Link><span className="fg-dim">        live </span><span className="fg-bright">fix pg pool timeout</span><span className="fg-dim">       </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-dim">○</span><span className="fg-dim"> </span><Link href="#session">copilot</Link><span className="fg-dim">      14m  </span><span>rate-limit /search</span><span className="fg-dim">        </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-dim">○</span><span className="fg-dim"> </span><Link href="#session">gemini-cli</Link><span className="fg-dim">   1h   </span><span>memory leak hunt</span><span className="fg-dim">          </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-dim">○</span><span className="fg-dim"> </span><Link href="#session">opencode</Link><span className="fg-dim">     3h   </span><span>docstring sweep</span><span className="fg-dim">           </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">│                                                     │</div>
            <div className="fg-dim">│   activity (last 24h) by tool                       │</div>
            <div><span className="fg-dim">│   claude  </span><Spark values={[0.1,0.3,0.2,0.5,0.8,0.9,0.6,0.4,0.3,0.2,0.5,0.7]} color="var(--ansi-green)"/><span className="fg-dim">  22    </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   codex   </span><Spark values={[0.4,0.5,0.3,0.2,0.3,0.6,0.7,0.5,0.8,0.9,0.7,0.6]} color="var(--ansi-cyan)"/><span className="fg-dim">  14    </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   copilot </span><Spark values={[0.2,0.1,0.2,0.1,0.3,0.2,0.4,0.3,0.5,0.3,0.2,0.1]} color="var(--ansi-yellow)"/><span className="fg-dim">   7    </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">└─────────────────────────────────────────────────────┘</div>
          </div>

          {/* Governance — THE action list */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px', position: 'relative'}}>
            <div className="fg-bright bold">┌─ what needs you ────────────────────────────────────┐</div>
            <div className="fg-dim">│                                                     │</div>

            {isOk ? (
              <>
                <div><span className="fg-dim">│   </span><Health state="ok" label="nothing · inbox zero"/><span className="fg-dim">                   </span><span className="fg-dim">│</span></div>
                <div className="fg-dim">│                                                     │</div>
                <div><span className="fg-dim">│   last cleared by </span><span className="fg-bright">you</span><span className="fg-dim"> · </span><span className="fg-bright">2h ago</span><span className="fg-dim">                  </span><span className="fg-dim">│</span></div>
                <div className="fg-dim">│                                                     │</div>
                <div><span className="fg-dim">│   recent: merged </span><Link kind="pr" href="#">PR #40</Link><span className="fg-dim"> · lint              </span><span className="fg-dim">│</span></div>
                <div><span className="fg-dim">│           merged </span><Link kind="pr" href="#">PR #39</Link><span className="fg-dim"> · prune             </span><span className="fg-dim">│</span></div>
                <div><span className="fg-dim">│           resolved conflict · </span><Link href="#">test runner</Link><span className="fg-dim">     </span><span className="fg-dim">│</span></div>
              </>
            ) : (
              <>
                <div><span className="fg-dim">│ </span><span className="fg-red bold">✗</span><span className="fg-dim"> </span><Link kind="pr" href="#">PR #42</Link><span className="fg-dim"> 12 auth facts · 1 conflict          </span><span className="fg-dim">│</span></div>
                <div><span className="fg-dim">│ </span><span className="fg-red bold">✗</span><span className="fg-dim"> conflict: pg port </span><span className="fg-bright">5432 vs 5433</span><span className="fg-dim">           </span><span className="fg-dim">│</span></div>
                <div><span className="fg-dim">│ </span><span className="fg-red bold">✗</span><span className="fg-dim"> conflict: rate limit enabled/disabled       </span><span className="fg-dim">│</span></div>
                <div><span className="fg-dim">│ </span><span className="fg-red bold">✗</span><span className="fg-dim"> conflict: pytest vs uv run pytest           </span><span className="fg-dim">│</span></div>
                <div><span className="fg-dim">│ </span><span className="fg-yellow bold">◐</span><span className="fg-dim"> </span><Link kind="pr" href="#">PR #41</Link><span className="fg-dim"> 5 pg-index facts · ready to merge   </span><span className="fg-dim">│</span></div>
                <div className="fg-dim">│                                                     │</div>
                <div><span className="fg-dim">│   press </span><span className="fg-cyan">F6</span><span className="fg-dim"> for PRs · </span><span className="fg-cyan">c</span><span className="fg-dim"> for conflict resolver     </span><span className="fg-dim">│</span></div>
              </>
            )}
            <div className="fg-dim">└─────────────────────────────────────────────────────┘</div>

            <Annot x={-280} y={20} tone={isOk ? 'green' : 'orange'} rotate={-3} w={260}>
              {isOk
                ? 'good day: nothing to do. close the terminal.'
                : 'bad day: every red line is a one-keypress fix.'}
            </Annot>
          </div>
        </div>

        <Hints items={[
          ['F2','overview '], ['F3','facts '], ['F4','sessions '], ['F5','dream '], ['F6','PRs '], ['F7','search '],
          ['↵','open '], ['c','fix conflict '], ['d','run dream '], ['q','quit']
        ]}/>
      </div>
    </div>
  );
}

window.Wire1OverviewV2 = Wire1OverviewV2;
