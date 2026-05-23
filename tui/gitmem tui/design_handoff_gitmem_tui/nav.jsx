// Shared nav components for v2 — top menu + sub-nav + link glyphs

// NavBar — persistent F-key primary menu, always visible
// active: the F-key that's currently selected (e.g. "F2")
function NavBar({ active }) {
  const items = [
    ['F2', 'Overview'],
    ['F3', 'Facts'],
    ['F4', 'Sessions'],
    ['F5', 'Dream'],
    ['F6', 'PRs'],
    ['F7', 'Search'],
  ];
  return (
    <div style={{
      padding: '5px 12px',
      borderBottom: '1px solid var(--term-line)',
      display: 'flex',
      gap: 4,
      fontSize: 12,
      background: 'var(--term-bg-panel)',
    }}>
      {items.map(([k, label]) => {
        const isActive = k === active;
        return (
          <span key={k} style={{
            padding: '1px 8px',
            borderRadius: 2,
            background: isActive ? 'var(--term-fg)' : 'transparent',
            color: isActive ? 'var(--term-bg)' : 'var(--term-fg-dim)',
            fontWeight: isActive ? 700 : 400,
          }}>
            <span style={{opacity: isActive ? 1 : 0.7}}>{k}</span>
            {' '}
            <span style={{color: isActive ? 'var(--term-bg)' : 'var(--term-fg)'}}>{label}</span>
          </span>
        );
      })}
      <span style={{marginLeft: 'auto', color: 'var(--term-fg-dim)'}}>
        <span className="fg-cyan">local</span> · acme-api
      </span>
    </div>
  );
}

// SubNav — context bar for the active page. items is [[label, count?, tone?, active?]]
function SubNav({ items, right }) {
  return (
    <div style={{
      padding: '4px 12px',
      borderBottom: '1px solid var(--term-line)',
      display: 'flex',
      gap: 14,
      fontSize: 12,
      background: 'var(--term-bg-alt)',
      color: 'var(--term-fg-dim)',
    }}>
      {items.map((it, i) => {
        const [label, count, tone, active] = it;
        const toneColor = tone === 'red' ? 'var(--ansi-red)' :
                          tone === 'green' ? 'var(--ansi-green)' :
                          tone === 'yellow' ? 'var(--ansi-yellow)' :
                          tone === 'cyan' ? 'var(--ansi-cyan)' : null;
        return (
          <span key={i} style={{
            color: active ? 'var(--term-fg-bright)' : (toneColor || 'var(--term-fg-dim)'),
            fontWeight: active ? 700 : 400,
            textDecoration: active ? 'underline' : 'none',
            textUnderlineOffset: 3,
          }}>
            {label}
            {count != null && <span style={{color: toneColor || 'var(--term-fg-faint)', marginLeft: 4}}>{count}</span>}
          </span>
        );
      })}
      {right && <span style={{marginLeft: 'auto'}}>{right}</span>}
    </div>
  );
}

// Link — clickable resource reference. kind drives the trailing glyph
//   file   → ↗  (open in editor)
//   gh     → ⎋  (open on github)
//   pr     → ⎋  (open PR on github)
function Link({ children, kind = 'file', href = '#', color }) {
  const glyph = kind === 'file' ? '↗' : '⎋';
  const defaultColor = kind === 'pr' ? 'var(--ansi-yellow)' : 'var(--ansi-cyan)';
  return (
    <a href={href} target="_blank" rel="noopener"
       style={{
         color: color || defaultColor,
         textDecoration: 'underline',
         textUnderlineOffset: 2,
         textDecorationStyle: 'dotted',
         textDecorationColor: 'var(--term-line-bright)',
       }}>
      {children}
      <span style={{color: 'var(--term-fg-faint)', marginLeft: 2, fontSize: '0.9em'}}>{glyph}</span>
    </a>
  );
}

// Health pill — single word + colored dot + optional count
function Health({ state, label, count }) {
  const tone = state === 'ok' ? { c: 'var(--ansi-green)', g: '●' } :
               state === 'warn' ? { c: 'var(--ansi-yellow)', g: '◐' } :
               state === 'bad' ? { c: 'var(--ansi-red)', g: '✗' } :
               { c: 'var(--term-fg-dim)', g: '○' };
  return (
    <span>
      <span style={{color: tone.c}}>{tone.g}</span>
      <span style={{color: 'var(--term-fg)', marginLeft: 6}}>{label}</span>
      {count != null && <span style={{color: tone.c, marginLeft: 6, fontWeight: 700}}>{count}</span>}
    </span>
  );
}

Object.assign(window, { NavBar, SubNav, Link, Health });
