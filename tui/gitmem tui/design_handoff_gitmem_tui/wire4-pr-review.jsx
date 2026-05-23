// Wireframe 4 — PR review + conflict resolution (the human-in-the-loop screen)
// Top: PR header & diff; Bottom-left: conflict inbox; Bottom-right: resolver

function Wire4PRReview() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— PR review · conflict resolver</span></span>
        <span style={{color: 'var(--term-fg-dim)'}}>drill-in</span>
      </div>

      <div className="term-body" style={{padding: 0}}>

        <div style={{padding: '4px 12px', borderBottom: '1px solid var(--term-line)', display: 'flex', gap: 16, fontSize: 12}}>
          <span className="fg-dim">F2 Facts</span>
          <span className="fg-dim">F3 Sessions</span>
          <span className="fg-dim">F4 Dream</span>
          <span className="fg-dim">[<span className="fg-bright bold">F5 PRs</span>]</span>
          <span className="fg-dim">F6 Search</span>
          <span style={{marginLeft: 'auto'}} className="fg-dim">home › PRs › <span className="fg-yellow">#42</span></span>
        </div>

        {/* PR header */}
        <div style={{padding: '10px 14px', borderBottom: '1px solid var(--term-line)', display: 'flex', gap: 20}}>
          <div style={{flex: 1}}>
            <div>
              <span className="fg-yellow bold">#42</span>
              <span className="fg-dim"> </span>
              <span className="fg-magenta">[dream/L1]</span>
              <span className="fg-bright bold"> &nbsp;12 facts about auth flow</span>
            </div>
            <div className="fg-dim">
              opened <span className="fg-bright">12m</span> ago by <span className="fg-magenta">dream-bot[haiku-4.5]</span>
              {' · base '}<span className="fg-cyan">main</span>
              {' ← '}<span className="fg-cyan">dream/l1/auth-0427</span>
              {' · '}<span className="fg-yellow">◐ L2 review requested</span>
            </div>
          </div>
          <div style={{textAlign: 'right'}}>
            <div><span className="fg-green">+12</span><span className="fg-dim"> added</span></div>
            <div><span className="fg-yellow">△ 3</span><span className="fg-dim"> updated</span></div>
            <div><span className="fg-red">⚑ 1</span><span className="fg-dim"> conflict</span></div>
          </div>
        </div>

        {/* Body: diff on left, conflict resolver on right */}
        <div style={{display: 'grid', gridTemplateColumns: '1fr 420px', background: 'var(--term-line)', gap: 1}}>

          {/* Diff of proposed facts */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px', minHeight: 540}}>
            <div className="fg-dim">memory/project/acme-api/auth.md</div>
            <div className="fg-dim">──────────────────────────────────────────────────────────────</div>

            <div><span className="fg-dim">  @@ auth flow @@</span></div>
            <div><span className="fg-green">+ </span><span className="fg-green">F · auth middleware skips /health and /metrics</span></div>
            <div><span className="fg-green">+ </span><span className="fg-dim">  </span><Strength value={3}/><span className="fg-dim"> src: auth/mw.py:34 · 2 sessions</span></div>
            <div style={{height: 4}}/>
            <div><span className="fg-green">+ </span><span className="fg-green">F · JWT expiry 15m, refresh 7d</span></div>
            <div><span className="fg-green">+ </span><span className="fg-dim">  </span><Strength value={4}/><span className="fg-dim"> src: auth/jwt.py:8 · README confirmed</span></div>
            <div style={{height: 4}}/>
            <div><span className="fg-green">+ </span><span className="fg-green">F · rate limit bypassed for admins</span></div>
            <div><span className="fg-green">+ </span><span className="fg-dim">  </span><Strength value={2}/><span className="fg-dim"> src: 1 session (claude) · needs confirm</span></div>
            <div style={{height: 4}}/>

            <div><span className="fg-dim">  @@ pg config @@</span></div>
            <div><span className="fg-yellow">± </span><span className="fg-yellow">U · pg pool timeout 30s</span><span className="fg-dim"> (was S:3, now S:4)</span></div>
            <div><span className="fg-yellow">± </span><span className="fg-dim">  promotion: AST-confirmed, 3 sessions</span></div>
            <div style={{height: 4}}/>

            <div><span className="fg-red">⚑ </span><span className="fg-red bold">C · postgres port</span></div>
            <div><span className="fg-red">⚑ </span><span className="fg-dim">  </span><span className="fg-bright">5432</span><span className="fg-dim"> (README) </span><Strength value={4}/></div>
            <div><span className="fg-red">⚑ </span><span className="fg-dim">  </span><span className="fg-bright">5433</span><span className="fg-dim"> (codex session) </span><Strength value={3}/></div>
            <div><span className="fg-red">⚑ </span><span className="fg-dim">  → needs human resolution. see right pane.</span></div>

            <div className="fg-dim" style={{marginTop: 10}}>──────────────────────────────────────────────────────────────</div>
            <div className="fg-dim">  8 more facts below · ↓ to scroll</div>

            <div style={{marginTop: 14}}>
              <div className="fg-dim">reviewer (L2) notes — <span className="fg-magenta">sonnet-4.5</span></div>
              <div style={{padding: '4px 10px', background: '#0a0f1a', borderLeft: '2px solid var(--ansi-blue)', marginTop: 4}}>
                <div className="fg-bright">All 12 facts look extractable from sources.</div>
                <div>Three caveats:</div>
                <div className="fg-dim">  1. rate-limit-admin fact is S:2 — single session. Approve as</div>
                <div className="fg-dim">     <span className="fg-bright">tentative</span> or require another confirmation.</div>
                <div className="fg-dim">  2. pg port conflict is a <span className="fg-bright">genuine contradiction</span>; either the</div>
                <div className="fg-dim">     README is stale or dev uses a non-default port. Ask human.</div>
                <div className="fg-dim">  3. JWT expiry may already exist at user-scope — check dedup.</div>
              </div>
            </div>
          </div>

          {/* Conflict resolver */}
          <div style={{background: 'var(--term-bg)', padding: '10px 12px', minHeight: 540, position: 'relative'}}>
            <div className="fg-bright bold">CONFLICT RESOLVER</div>
            <div className="fg-dim">──────────────────────────────────</div>

            <div style={{marginTop: 6}}>
              <div className="fg-red bold">⚑ postgres port</div>
              <div className="fg-dim">  "which port does pg run on?"</div>
            </div>

            {/* Option A */}
            <div style={{marginTop: 10, padding: '6px 10px', border: '1px solid var(--term-line)', borderRadius: 2}}>
              <div><span className="fg-cyan bold">( ) A</span><span className="fg-dim"> </span><span className="fg-bright">port 5432</span></div>
              <div className="fg-dim">  <Strength value={4}/> README.md · ops section</div>
              <div className="fg-dim">  last confirmed: 14d ago</div>
              <div><span className="fg-dim">  composite </span><Bar value={0.72} width={16} color="var(--ansi-green)"/><span className="fg-dim"> 0.72</span></div>
            </div>

            {/* Option B */}
            <div style={{marginTop: 6, padding: '6px 10px', border: '1.5px solid var(--ansi-cyan)', borderRadius: 2, background: '#07131a'}}>
              <div><span className="fg-cyan bold">(●) B</span><span className="fg-dim"> </span><span className="fg-bright">port 5433</span></div>
              <div className="fg-dim">  <Strength value={3}/> codex session · docker-compose.yml</div>
              <div className="fg-dim">  last confirmed: 2h ago</div>
              <div><span className="fg-dim">  composite </span><Bar value={0.68} width={16} color="var(--ansi-yellow)"/><span className="fg-dim"> 0.68</span></div>
            </div>

            {/* Option C */}
            <div style={{marginTop: 6, padding: '6px 10px', border: '1px solid var(--term-line)', borderRadius: 2}}>
              <div><span className="fg-cyan bold">( ) C</span><span className="fg-dim"> </span>keep both, scope-split</div>
              <div className="fg-dim">  prod → 5432, dev → 5433</div>
              <div className="fg-dim">  split at scope: folder/</div>
            </div>

            {/* Option D */}
            <div style={{marginTop: 6, padding: '6px 10px', border: '1px solid var(--term-line)', borderRadius: 2}}>
              <div><span className="fg-cyan bold">( ) D</span><span className="fg-dim"> </span>tombstone both, ask again</div>
            </div>

            <div className="fg-dim" style={{marginTop: 10}}>──────────────────────────────────</div>
            <div className="fg-bright">action</div>
            <div><span className="inv"> [ ] </span><span> resolve as </span><span className="fg-cyan bold">B</span><span> · supersede A</span></div>
            <div><span className="fg-dim">     A → superseded_by:fct_new_b</span></div>
            <div><span className="fg-dim">     A → tombstone after 30d</span></div>

            <div style={{marginTop: 10, padding: 6, border: '1px solid var(--ansi-green)', borderRadius: 2, background: '#0a140a'}}>
              <div><span className="fg-green bold">[ ↵ apply & merge PR #42 ]</span></div>
              <div className="fg-dim">  squash · signed · 1 of 1 conflict resolved</div>
            </div>

            <Annot x={-260} y={220} tone="orange" rotate={-3} w={240}>
              the whole point: one screen to <br/>
              decide what the agent "knows"
            </Annot>
          </div>
        </div>

        <Hints items={[
          ['↑↓','option '], ['a/b/c/d','pick '], ['↵','apply '],
          ['e','edit text '], ['o','open on GitHub '], ['x','reject PR '], ['Esc','back']
        ]}/>
      </div>
    </div>
  );
}

window.Wire4PRReview = Wire4PRReview;
