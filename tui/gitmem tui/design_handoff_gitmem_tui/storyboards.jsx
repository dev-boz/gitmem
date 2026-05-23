// Storyboards — 3 flows, each a row of 4 frames
// 1. Review a dream PR
// 2. Resolve a conflict
// 3. Run dream pipeline

function MiniFrame({ n, cap, children, w = 260, h = 220 }) {
  return (
    <div style={{width: w}}>
      <div className="frame-cap">
        <span className="frame-num">{n}</span>
        <span>{cap}</span>
      </div>
      <div className="term" style={{width: w}}>
        <div className="term-chrome" style={{padding: '3px 6px'}}>
          <span className="dot r" style={{width: 7, height: 7}}/>
          <span className="dot y" style={{width: 7, height: 7}}/>
          <span className="dot g" style={{width: 7, height: 7}}/>
          <span className="title" style={{fontSize: 9}}>umx</span>
        </div>
        <div className="term-body" style={{padding: '6px 8px', minHeight: h, fontSize: 10, lineHeight: '13px'}}>
          {children}
        </div>
      </div>
    </div>
  );
}

function Story1ReviewPR() {
  return (
    <div style={{display: 'flex', gap: 24, alignItems: 'flex-start'}}>
      <MiniFrame n="1" cap="open TUI, land on cockpit">
        <div className="fg-magenta bold">umx</div>
        <div className="fg-dim">─────────────────</div>
        <div><span className="fg-dim">facts</span> 1,284</div>
        <div><span className="fg-dim">conflicts </span><span className="fg-red bold">3</span></div>
        <div><span className="fg-dim">PRs pending </span><span className="fg-yellow bold">5</span></div>
        <div className="fg-dim">─────────────────</div>
        <div style={{marginTop: 4}}><span className="bg-sel-active">▸ F5 PRs</span></div>
        <div className="fg-dim">  F2 Facts</div>
        <div className="fg-dim">  F3 Sessions</div>
        <div className="fg-dim">  F4 Dream</div>
        <div style={{marginTop: 10}} className="fg-faint">press F5 →</div>
      </MiniFrame>

      <MiniFrame n="2" cap="PR queue list">
        <div className="fg-bright">PENDING PRs</div>
        <div className="fg-dim">─────────────────</div>
        <div className="bg-sel-active"><span className="fg-yellow">#42</span> auth · 12 facts</div>
        <div><span className="fg-yellow">#41</span> pg indexes · 5</div>
        <div><span className="fg-green">#40</span> lint drift · 3</div>
        <div><span className="fg-green">#39</span> prune · 8</div>
        <div><span className="fg-yellow">#38</span> README sweep</div>
        <div className="fg-dim" style={{marginTop: 10}}>↵ to open</div>
      </MiniFrame>

      <MiniFrame n="3" cap="diff + L2 review notes">
        <div><span className="fg-yellow">#42</span> <span className="fg-magenta">[L1]</span></div>
        <div className="fg-green">+ auth skips /health</div>
        <div className="fg-green">+ JWT 15m / 7d</div>
        <div className="fg-yellow">± pg timeout 30s</div>
        <div className="fg-red">⚑ pg port 5432/5433</div>
        <div className="fg-dim">───────────────</div>
        <div className="fg-blue">sonnet-4.5 notes:</div>
        <div className="fg-dim">ok to merge if port</div>
        <div className="fg-dim">resolved by human</div>
      </MiniFrame>

      <MiniFrame n="4" cap="resolve & merge">
        <div className="fg-red">⚑ port 5432 vs 5433</div>
        <div style={{marginTop: 2}}><span className="fg-cyan">(●) B · 5433</span></div>
        <div className="fg-dim">    supersede A</div>
        <div style={{padding: 3, border: '1px solid var(--ansi-green)', marginTop: 4}}>
          <div className="fg-green bold">[↵ apply & merge]</div>
        </div>
        <div style={{marginTop: 6}} className="fg-green">✓ merged to main</div>
        <div className="fg-dim">  12 facts committed</div>
        <div className="fg-dim">  1 conflict resolved</div>
      </MiniFrame>
    </div>
  );
}

function Story2Conflict() {
  return (
    <div style={{display: 'flex', gap: 24, alignItems: 'flex-start'}}>
      <MiniFrame n="1" cap="red flag on cockpit">
        <div className="fg-bright">governance</div>
        <div className="fg-dim">──────────────</div>
        <div className="fg-red bold">✗ conflicts 3</div>
        <div style={{marginTop: 2}}>
          <div className="fg-red">⚑ pg port</div>
          <div className="fg-red">⚑ rate limit</div>
          <div className="fg-red">⚑ test runner</div>
        </div>
        <div className="fg-dim" style={{marginTop: 10}}>c to focus</div>
      </MiniFrame>

      <MiniFrame n="2" cap="side-by-side evidence">
        <div className="fg-red bold">⚑ pg port</div>
        <div className="fg-dim">──────────────</div>
        <div style={{padding: 3, border: '1px solid var(--term-line)', marginTop: 4}}>
          <div>A · 5432</div>
          <div className="fg-dim"><Strength value={4}/></div>
          <div className="fg-dim">README · 14d</div>
        </div>
        <div style={{padding: 3, border: '1px solid var(--term-line)', marginTop: 4}}>
          <div>B · 5433</div>
          <div className="fg-dim"><Strength value={3}/></div>
          <div className="fg-dim">codex · 2h</div>
        </div>
      </MiniFrame>

      <MiniFrame n="3" cap="pick strategy">
        <div className="fg-dim">choose:</div>
        <div className="fg-cyan">( ) A win</div>
        <div className="fg-cyan">(●) B win</div>
        <div className="fg-cyan">( ) scope-split</div>
        <div className="fg-cyan">( ) ask again</div>
        <div className="fg-dim" style={{marginTop: 4}}>───────────────</div>
        <div>B supersedes A</div>
        <div className="fg-dim">A → tombstone 30d</div>
      </MiniFrame>

      <MiniFrame n="4" cap="applied, inbox drops to 2">
        <div className="fg-green bold">✓ resolved</div>
        <div className="fg-dim">──────────────</div>
        <div>pg port → 5433</div>
        <div className="fg-dim">committed to main</div>
        <div className="fg-dim" style={{marginTop: 6}}>remaining:</div>
        <div className="fg-red">⚑ rate limit</div>
        <div className="fg-red">⚑ test runner</div>
        <div className="fg-dim" style={{marginTop: 6}}>conflicts <span className="fg-red">2</span></div>
      </MiniFrame>
    </div>
  );
}

function Story3Dream() {
  return (
    <div style={{display: 'flex', gap: 24, alignItems: 'flex-start'}}>
      <MiniFrame n="1" cap="press F4 · 47 sessions to process">
        <div className="fg-bright">dream pipeline</div>
        <div className="fg-dim">──────────────</div>
        <div className="fg-dim">unprocessed  </div>
        <div><span className="fg-bright">47 sessions</span></div>
        <div className="fg-dim">last run 3d ago</div>
        <div style={{marginTop: 6, padding: 3, border: '1px solid var(--ansi-cyan)'}}>
          <div className="fg-cyan bold">[↵ run dream]</div>
        </div>
      </MiniFrame>

      <MiniFrame n="2" cap="live meters tick across stages">
        <div className="fg-green">✓ orient</div>
        <div className="fg-green">✓ gather</div>
        <div className="fg-yellow">◉ consolidate</div>
        <div><Bar value={0.64} width={18} color="var(--ansi-yellow)"/></div>
        <div className="fg-dim">· lint</div>
        <div className="fg-dim">· prune</div>
        <div style={{marginTop: 4}}>
          <Spark values={[0.2,0.4,0.6,0.5,0.7,0.9,0.8,0.9]} color="var(--ansi-cyan)"/>
        </div>
        <div className="fg-dim">18 facts/min</div>
      </MiniFrame>

      <MiniFrame n="3" cap="proposals & flags surface">
        <div>proposed <span className="fg-bright">24</span></div>
        <div>merged   <span className="fg-green">9</span></div>
        <div>deduped  <span className="fg-yellow">7</span></div>
        <div>conflict <span className="fg-red">3</span></div>
        <div className="fg-dim">──────────────</div>
        <div className="fg-yellow">PR #42 opened</div>
        <div className="fg-yellow">PR #43 opened</div>
        <div className="fg-red">⚑ port conflict</div>
      </MiniFrame>

      <MiniFrame n="4" cap="done · summary">
        <div className="fg-green bold">✓ dream done</div>
        <div className="fg-dim">──────────────</div>
        <div className="fg-dim">duration    4m 12s</div>
        <div className="fg-dim">PRs opened  2</div>
        <div className="fg-dim">to review   3 conflicts</div>
        <div className="fg-dim" style={{marginTop: 6}}>next:</div>
        <div><span className="fg-cyan">F5</span> <span className="fg-dim">review PRs</span></div>
        <div><span className="fg-cyan">c</span>  <span className="fg-dim">fix conflicts</span></div>
      </MiniFrame>
    </div>
  );
}

window.Story1ReviewPR = Story1ReviewPR;
window.Story2Conflict = Story2Conflict;
window.Story3Dream = Story3Dream;
