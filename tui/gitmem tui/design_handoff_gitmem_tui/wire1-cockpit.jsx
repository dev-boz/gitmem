// Wireframe 1 — btop-style cockpit: quad grid dashboard, general health
// Top row: Facts overview + Dream pipeline live
// Bottom row: Sessions stream + PR queue / conflicts
// Status strip at top and bottom.

function Wire1Cockpit() {
  return (
    <div className="term" style={{width: 1120}}>
      <div className="term-chrome">
        <span className="dot r"/><span className="dot y"/><span className="dot g"/>
        <span className="title"><b>umx</b> <span style={{color: 'var(--term-fg-faint)'}}>— gitmem cockpit · ~/work/acme-api</span></span>
        <span style={{color: 'var(--term-fg-dim)'}}>80×24 · zsh</span>
      </div>

      <div className="term-body" style={{padding: 0}}>

        {/* Top status line — like btop's process summary */}
        <div style={{padding: '6px 12px', borderBottom: '1px solid var(--term-line)', display: 'flex', justifyContent: 'space-between'}}>
          <span>
            <span className="fg-magenta bold">umx</span>
            <span className="fg-dim"> v0.9.2-alpha  </span>
            <span className="fg-dim">mode </span><span className="fg-cyan">local</span>
            <span className="fg-dim">  repo </span><span className="fg-bright">acme-api</span>
            <span className="fg-dim">  scope </span><span className="fg-yellow">project/acme-api</span>
          </span>
          <span>
            <span className="fg-dim">facts </span><span className="fg-bright bold">1,284</span>
            <span className="fg-dim">  sessions </span><span className="fg-bright">47</span>
            <span className="fg-dim">  conflicts </span><span className="fg-red bold">3</span>
            <span className="fg-dim">  PRs </span><span className="fg-yellow bold">5</span>
            <span className="fg-dim">  </span><span className="fg-green">● online</span>
          </span>
        </div>

        {/* Quad grid */}
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--term-line)'}}>

          {/* ── Cell 1: Memory at a glance ─────────────── */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px'}}>
            <div className="fg-bright bold">┌─ memory ──────────────────────── facts by strength ─┐</div>
            <div className="fg-dim">│                                                      │</div>
            <div>
              <span className="fg-dim">│   </span>
              <span style={{color: 'var(--s5)'}}>S:5  code-parsed   </span>
              <Bar value={0.32} width={22} color="var(--s5)"/>
              <span className="fg-dim">   412  </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span style={{color: 'var(--s4)'}}>S:4  doc-confirmed </span>
              <Bar value={0.26} width={22} color="var(--s4)"/>
              <span className="fg-dim">   334  </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span style={{color: 'var(--s3)'}}>S:3  multi-source  </span>
              <Bar value={0.22} width={22} color="var(--s3)"/>
              <span className="fg-dim">   281  </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span style={{color: 'var(--s2)'}}>S:2  inferred      </span>
              <Bar value={0.14} width={22} color="var(--s2)"/>
              <span className="fg-dim">   178  </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span style={{color: 'var(--s1)'}}>S:1  incidental    </span>
              <Bar value={0.06} width={22} color="var(--s1)"/>
              <span className="fg-dim">    79  </span>
              <span className="fg-dim">│</span>
            </div>
            <div className="fg-dim">│                                                      │</div>
            <div>
              <span className="fg-dim">│   scope tree                                         │</span>
            </div>
            <div><span className="fg-dim">│   </span><span className="fg-cyan">▸ user/</span><span className="fg-dim">.............. 42   </span><span className="fg-faint">└ stable prefs</span><span className="fg-dim">      │</span></div>
            <div><span className="fg-dim">│   </span><span className="fg-cyan">▸ tool/</span><span className="fg-dim">............. 118   </span><span className="fg-faint">└ per-agent</span><span className="fg-dim">         │</span></div>
            <div><span className="fg-dim">│   </span><span className="fg-cyan">▾ project/acme-api</span><span className="fg-dim">  687                                │</span></div>
            <div><span className="fg-dim">│     </span><span className="fg-cyan">▸ folder/</span><span className="fg-dim">......... 298                            │</span></div>
            <div><span className="fg-dim">│     </span><span className="fg-cyan">▸ file/</span><span className="fg-dim">........... 389                            │</span></div>
            <div className="fg-dim">└──────────────────────────────────────────────────────┘</div>
          </div>

          {/* ── Cell 2: Dream pipeline live ─────────────── */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px', position: 'relative'}}>
            <div className="fg-bright bold">┌─ dream pipeline ──────────────── running · t+00:42 ─┐</div>
            <div className="fg-dim">│                                                      │</div>
            <div>
              <span className="fg-dim">│   </span>
              <span className="fg-green">✓ </span><span>orient        </span>
              <Bar value={1} width={22} color="var(--ansi-green)"/>
              <span className="fg-dim">  100%  </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span className="fg-green">✓ </span><span>gather        </span>
              <Bar value={1} width={22} color="var(--ansi-green)"/>
              <span className="fg-dim">  100%  </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span className="fg-yellow">◉ </span><span className="fg-bright bold">consolidate   </span>
              <Bar value={0.64} width={22} color="var(--ansi-yellow)"/>
              <span className="fg-dim">   64%  </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span className="fg-dim">· </span><span className="fg-dim">lint          </span>
              <Bar value={0} width={22} color="var(--term-line)"/>
              <span className="fg-dim">    -   </span>
              <span className="fg-dim">│</span>
            </div>
            <div>
              <span className="fg-dim">│   </span>
              <span className="fg-dim">· </span><span className="fg-dim">prune         </span>
              <Bar value={0} width={22} color="var(--term-line)"/>
              <span className="fg-dim">    -   </span>
              <span className="fg-dim">│</span>
            </div>
            <div className="fg-dim">│                                                      │</div>
            <div className="fg-dim">│   throughput (facts/min, last 10)                    │</div>
            <div><span className="fg-dim">│   </span><Spark values={[0.2,0.4,0.35,0.6,0.5,0.75,0.7,0.85,0.9,0.72]} color="var(--ansi-cyan)"/><span className="fg-dim">          peak </span><span className="fg-cyan">18/min</span><span className="fg-dim">          │</span></div>
            <div className="fg-dim">│                                                      │</div>
            <div><span className="fg-dim">│   proposed </span><span className="fg-bright">24</span><span className="fg-dim">   merged </span><span className="fg-green">9</span><span className="fg-dim">   deduped </span><span className="fg-yellow">7</span><span className="fg-dim">   conflicts </span><span className="fg-red">3</span><span className="fg-dim">  │</span></div>
            <div className="fg-dim">│                                                      │</div>
            <div><span className="fg-dim">│   extractor </span><span className="fg-magenta">claude-haiku-4.5</span><span className="fg-dim">   reviewer </span><span className="fg-magenta">sonnet-4.5</span><span className="fg-dim">   │</span></div>
            <div className="fg-dim">└──────────────────────────────────────────────────────┘</div>

            <Annot x={500} y={20} tone="orange" rotate={4}>btop-ish live meters — update every 500ms</Annot>
          </div>

          {/* ── Cell 3: Sessions stream ─────────────── */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px'}}>
            <div className="fg-bright bold">┌─ sessions ─────────────────── recent · all tools ──┐</div>
            <div className="fg-dim">│                                                     │</div>
            <div><span className="fg-dim">│ </span><span className="fg-green">● </span><span className="fg-cyan">claude-code</span><span className="fg-dim">  2m ago  </span><span>refactor auth/ middleware</span><span className="fg-dim">        </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-yellow">◉ </span><span className="fg-cyan">codex      </span><span className="fg-dim">  live    </span><span className="fg-bright">fix postgres pool timeout</span><span className="fg-dim">       </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-dim">○ </span><span className="fg-cyan">copilot    </span><span className="fg-dim">  14m     </span><span>add rate limit to /search</span><span className="fg-dim">        </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-dim">○ </span><span className="fg-cyan">gemini-cli </span><span className="fg-dim">  1h      </span><span>investigate memory leak</span><span className="fg-dim">          </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-dim">○ </span><span className="fg-cyan">opencode   </span><span className="fg-dim">  3h      </span><span>readme + docstrings sweep</span><span className="fg-dim">        </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">│                                                     │</div>
            <div className="fg-dim">│   activity (last 24h)                               │</div>
            <div><span className="fg-dim">│   claude  </span><Spark values={[0.1,0.3,0.2,0.5,0.8,0.9,0.6,0.4,0.3,0.2,0.5,0.7]} color="var(--ansi-green)"/><span className="fg-dim">  22 sess  </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   codex   </span><Spark values={[0.4,0.5,0.3,0.2,0.3,0.6,0.7,0.5,0.8,0.9,0.7,0.6]} color="var(--ansi-cyan)"/><span className="fg-dim">  14 sess  </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   copilot </span><Spark values={[0.2,0.1,0.2,0.1,0.3,0.2,0.4,0.3,0.5,0.3,0.2,0.1]} color="var(--ansi-yellow)"/><span className="fg-dim">   7 sess  </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   gemini  </span><Spark values={[0,0,0.1,0,0,0.2,0.1,0,0.3,0.1,0,0.2]} color="var(--ansi-magenta)"/><span className="fg-dim">   3 sess  </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   opencode</span><Spark values={[0,0,0,0,0.1,0.1,0,0,0.2,0,0,0]} color="var(--ansi-blue)"/><span className="fg-dim">   1 sess  </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">└─────────────────────────────────────────────────────┘</div>
          </div>

          {/* ── Cell 4: Governance — PRs + conflicts ─────────────── */}
          <div style={{background: 'var(--term-bg)', padding: '10px 14px', position: 'relative'}}>
            <div className="fg-bright bold">┌─ governance ───────────────── PRs · conflicts ─────┐</div>
            <div className="fg-dim">│                                                     │</div>
            <div><span className="fg-dim">│ </span><span className="fg-yellow bold">#42</span><span className="fg-dim"> </span><span className="fg-magenta">[dream/L1]</span><span> 12 facts about auth flow        </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│     </span><span className="fg-dim">proposed by </span><span className="fg-cyan">haiku-4.5</span><span className="fg-dim"> · needs L2 review       </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-yellow">#41</span><span className="fg-dim"> </span><span className="fg-magenta">[dream/L1]</span><span>  5 facts about pg indexes       </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-green">#40</span><span className="fg-dim"> </span><span className="fg-magenta">[lint]    </span><span>  3 drift corrections            </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│ </span><span className="fg-green">#39</span><span className="fg-dim"> </span><span className="fg-magenta">[prune]   </span><span>  8 stale facts → tombstone      </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">│                                                     │</div>
            <div><span className="fg-dim">│ </span><span className="fg-red bold">✗ conflicts</span><span className="fg-dim"> (need human)                        </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><span className="fg-red">⚑</span><span className="fg-dim"> postgres port </span><span className="fg-bright">5432 vs 5433</span><span className="fg-dim">        </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│     </span><Strength value={3}/><span className="fg-dim"> vs </span><Strength value={4}/><span className="fg-dim">  codex:2025-04 vs README      </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><span className="fg-red">⚑</span><span className="fg-dim"> rate limit handler </span><span className="fg-bright">enabled/disabled</span><span className="fg-dim">   </span><span className="fg-dim">│</span></div>
            <div><span className="fg-dim">│   </span><span className="fg-red">⚑</span><span className="fg-dim"> test runner is </span><span className="fg-bright">pytest vs uv run</span><span className="fg-dim">     </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">│                                                     │</div>
            <div><span className="fg-dim">│ </span><span className="fg-green">tombstones</span><span className="fg-dim"> 14     </span><span className="fg-green">procedures</span><span className="fg-dim"> 6 matched today  </span><span className="fg-dim">│</span></div>
            <div className="fg-dim">└─────────────────────────────────────────────────────┘</div>

            <Annot x={12} y={170} tone="orange" rotate={-3}>conflicts bubble up — <br/>only thing a human *must* touch</Annot>
          </div>
        </div>

        <Hints items={[
          ['F1','help '], ['F2','facts '], ['F3','sessions '], ['F4','dream '], ['F5','PRs '], ['F6','search '],
          ['↵','open '], ['/','filter '], ['q','quit']
        ]}/>
      </div>
    </div>
  );
}

window.Wire1Cockpit = Wire1Cockpit;
