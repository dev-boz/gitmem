// Terminal primitives — box-drawing helpers for TUI wireframes
// Everything here renders into monospace text using real box chars.

// Repeat a char N times
const rep = (ch, n) => ch.repeat(Math.max(0, n));

// Raw line helper: yields a <span> so we can colorize segments.
function Line({ children, style }) {
  return <div style={{whiteSpace: 'pre', ...style}}>{children}</div>;
}

// A single horizontal rule inside a panel, padding-aware.
// Usage: <Hr w={80}/>  →  ─────...
function Hr({ w = 60, char = '─', className }) {
  return <span className={className}>{rep(char, w)}</span>;
}

// Panel: a bordered rectangle with a title in the top border.
//   ┌─ title ──────────┐
//   │ children         │
//   └──────────────────┘
// width is in characters (monospace cells). Height auto from children.
function Panel({ title, right, w = 60, children, accent = '--term-line-bright', pad = 1, style, dim }) {
  const border = `var(${accent})`;
  const inner = w - 2;
  // top border composition: ┌─ title ── right ─┐
  const titleStr = title ? ` ${title} ` : '';
  const rightStr = right ? ` ${right} ` : '';
  const fillLen = Math.max(0, inner - titleStr.length - rightStr.length);
  return (
    <div style={{color: border, whiteSpace: 'pre', ...style}}>
      <div>
        <span>┌</span>
        <span style={{color: 'var(--term-fg-bright)'}}>{titleStr}</span>
        <span>{rep('─', fillLen)}</span>
        <span style={{color: dim ? 'var(--term-fg-dim)' : 'var(--term-fg)'}}>{rightStr}</span>
        <span>┐</span>
      </div>
      <div style={{display: 'flex'}}>
        <div style={{color: border}}>{Array.from({length: React.Children.count(children) || 1}).map((_,i)=><div key={i}>│</div>)}</div>
        <div style={{flex: 1, color: 'var(--term-fg)', padding: `0 ${pad * 8}px`}}>
          {children}
        </div>
        <div style={{color: border}}>{Array.from({length: React.Children.count(children) || 1}).map((_,i)=><div key={i}>│</div>)}</div>
      </div>
      <div>└{rep('─', inner)}┘</div>
    </div>
  );
}

// Simpler: top/bottom borders with free children in the middle.
// Use when the panel body needs full control of layout.
function Box({ title, right, w = 60, children, color = 'var(--term-line-bright)', titleColor = 'var(--term-fg-bright)', rightColor = 'var(--term-fg-dim)', style }) {
  const inner = w - 2;
  const titleStr = title ? ` ${title} ` : '';
  const rightStr = right ? ` ${right} ` : '';
  const fillLen = Math.max(0, inner - titleStr.length - rightStr.length);
  return (
    <div style={{whiteSpace: 'pre', ...style}}>
      <div style={{color}}>
        <span>┌</span>
        <span style={{color: titleColor}}>{titleStr}</span>
        <span>{rep('─', fillLen)}</span>
        <span style={{color: rightColor}}>{rightStr}</span>
        <span>┐</span>
      </div>
      <div style={{display: 'grid', gridTemplateColumns: 'auto 1fr auto'}}>
        <div style={{color}}>
          {/* left border column — computed by children row count via CSS; we render a column of │ that stretches */}
          <div style={{height: '100%', display: 'flex', flexDirection: 'column'}}>
            <div style={{flex: 1, background: `linear-gradient(${color}, ${color}) center/1ch 100% no-repeat`, color, whiteSpace: 'pre', padding: '0 0px'}}>
              {/* We rely on repeated │ chars — use a pseudo via many lines */}
            </div>
          </div>
        </div>
        <div style={{padding: '0 8px', color: 'var(--term-fg)'}}>{children}</div>
        <div style={{color}} />
      </div>
      <div style={{color}}>└{rep('─', inner)}┘</div>
    </div>
  );
}

// BoxFrame: cleanest pattern — caller provides exact child rows, we wrap
// each row with │ … │ and pad to width. This is what we'll actually use.
function Frame({ title, right, w = 80, h, color = 'var(--term-line)', titleColor = 'var(--term-fg-bright)', rightColor = 'var(--term-fg-dim)', children, style, className }) {
  const inner = w - 2;
  const titleStr = title ? ` ${title} ` : '';
  const rightStr = right ? ` ${right} ` : '';
  const fillLen = Math.max(0, inner - titleStr.length - rightStr.length);

  const rows = React.Children.toArray(children);
  const padRows = h ? Math.max(0, h - rows.length - 2) : 0;

  return (
    <div className={className} style={{whiteSpace: 'pre', fontFamily: 'var(--mono)', ...style}}>
      <div style={{color}}>
        <span>┌</span>
        <span style={{color: titleColor}}>{titleStr}</span>
        <span>{rep('─', fillLen)}</span>
        <span style={{color: rightColor}}>{rightStr}</span>
        <span>┐</span>
      </div>
      {rows.map((r, i) => (
        <div key={i} style={{display: 'flex'}}>
          <span style={{color}}>│</span>
          <span style={{flex: 1, overflow: 'hidden', paddingLeft: 4, paddingRight: 4}}>{r}</span>
          <span style={{color}}>│</span>
        </div>
      ))}
      {Array.from({length: padRows}).map((_, i) => (
        <div key={'p' + i} style={{display: 'flex'}}>
          <span style={{color}}>│</span>
          <span style={{flex: 1}}> </span>
          <span style={{color}}>│</span>
        </div>
      ))}
      <div style={{color}}>└{rep('─', inner)}┘</div>
    </div>
  );
}

// Strength dots 1..5
function Strength({ value }) {
  return (
    <span>
      {[1,2,3,4,5].map(i => (
        <span key={i} className={'s-dot ' + (i <= value ? 's-' + value : 's-off')} />
      ))}
    </span>
  );
}

// Braille/block progress bar — 20 cells like btop
const BLOCKS = [' ', '▏','▎','▍','▌','▋','▊','▉','█'];
function Bar({ value = 0.5, width = 20, color = 'var(--ansi-green)', bg = 'var(--term-line)' }) {
  const filled = value * width;
  const whole = Math.floor(filled);
  const frac = filled - whole;
  const partial = BLOCKS[Math.round(frac * 8)];
  return (
    <span className="bar">
      <span style={{color}}>{rep('█', whole)}{whole < width ? partial : ''}</span>
      <span style={{color: bg}}>{rep('░', Math.max(0, width - whole - (whole < width ? 1 : 0)))}</span>
    </span>
  );
}

// Spark — line of braille blocks drawn from values 0..1
function Spark({ values, color = 'var(--ansi-cyan)' }) {
  const chars = ['▁','▂','▃','▄','▅','▆','▇','█'];
  return (
    <span className="spark" style={{color}}>
      {values.map((v, i) => chars[Math.min(7, Math.max(0, Math.floor(v * 8)))]).join('')}
    </span>
  );
}

// Hint bar
function Hints({ items }) {
  return (
    <div className="hintbar">
      {items.map((it, i) => (
        <span key={i}><b>{it[0]}</b>{it[1]}</span>
      ))}
    </div>
  );
}

// Small inline tag
function Tag({ children, color = 'var(--term-fg-dim)', bg }) {
  return <span style={{color, background: bg, padding: bg ? '0 4px' : 0, borderRadius: 2}}>{children}</span>;
}

// Annotation with arrow — for sketchy margin notes
function Annot({ x, y, w, children, tone = 'orange', rotate = -2, arrow }) {
  const cls = 'annot' + (tone === 'blue' ? ' blue' : tone === 'green' ? ' green' : '');
  return (
    <div className={cls} style={{left: x, top: y, width: w, transform: `rotate(${rotate}deg)`}}>
      {children}
      {arrow && (
        <svg width="80" height="40" style={{position: 'absolute', ...arrow.pos}}>
          <path d={arrow.d} stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
          <path d={arrow.head} stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
        </svg>
      )}
    </div>
  );
}

Object.assign(window, {
  rep, Line, Hr, Frame, Box, Panel, Strength, Bar, Spark, Hints, Tag, Annot
});
