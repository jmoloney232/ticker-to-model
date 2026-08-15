/* Minimal markdown renderer for the committed audit guide
   (docs/financial-assumptions.md, served by /api/audit-guide). Covers the
   subset that document actually uses — headings, tables, lists, bold,
   inline code, rules, blockquotes — as React nodes only: no raw HTML ever
   reaches the DOM, and no dependency is added. */

import type { ReactNode } from "react";

function inline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  // split on **bold** and `code`, keeping delimiters' content
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**"))
      out.push(<b key={`${key}-${i++}`}>{tok.slice(2, -2)}</b>);
    else out.push(<code key={`${key}-${i++}`}>{tok.slice(1, -1)}</code>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function table(lines: string[], key: number): ReactNode {
  const cells = (row: string) =>
    row.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const head = cells(lines[0]);
  const body = lines.slice(2).map(cells);
  return (
    <div className="md-tablewrap" key={key}>
      <table>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i}>{inline(h, i)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, r) => (
            <tr key={r}>
              {row.map((c, i) => (
                <td key={i}>{inline(c, i)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MiniMarkdown({ source }: { source: string }) {
  const lines = source.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      const Tag = (`h${Math.min(level + 1, 5)}`) as "h2" | "h3" | "h4" | "h5";
      out.push(<Tag key={key++}>{inline(h[2], key)}</Tag>);
      i++;
      continue;
    }
    if (/^(-{3,}|_{3,}|\*{3,})\s*$/.test(line)) {
      out.push(<hr key={key++} />);
      i++;
      continue;
    }
    if (line.startsWith("|")) {
      const block: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) block.push(lines[i++]);
      if (block.length >= 2 && /^\|[\s:|-]+\|?$/.test(block[1]))
        out.push(table(block, key++));
      else
        block.forEach((b) => out.push(<p key={key++}>{inline(b, key)}</p>));
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const items: string[] = [];
      const ordered = /^\s*\d+\./.test(line);
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i]))
        items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const L = ordered ? "ol" : "ul";
      out.push(
        <L key={key++}>
          {items.map((it, j) => (
            <li key={j}>{inline(it, j)}</li>
          ))}
        </L>,
      );
      continue;
    }
    if (line.startsWith(">")) {
      const block: string[] = [];
      while (i < lines.length && lines[i].startsWith(">"))
        block.push(lines[i++].replace(/^>\s?/, ""));
      out.push(<blockquote key={key++}>{inline(block.join(" "), key)}</blockquote>);
      continue;
    }
    const para: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,4}\s|[-*]\s|\d+\.\s|\||>|-{3,})/.test(lines[i])
    )
      para.push(lines[i++]);
    out.push(<p key={key++}>{inline(para.join(" "), key)}</p>);
  }
  return <div className="md">{out}</div>;
}
