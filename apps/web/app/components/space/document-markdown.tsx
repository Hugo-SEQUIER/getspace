import { Fragment } from "react";
import {
  documentBlocks,
  documentInline,
  MAX_MARKDOWN_NODES,
} from "../../../lib/space/document-content";

export function DocumentMarkdown({
  text,
  anchors = false,
}: {
  text: string;
  anchors?: boolean;
}) {
  let remaining = MAX_MARKDOWN_NODES;
  const inline = (value: string) => {
    const parts = documentInline(value, Math.max(0, remaining));
    if (!parts) {
      remaining = -1;
      return null;
    }
    remaining -= parts.length;
    return parts.map((part, index) => {
      if (part.type === "code") return <code key={index}>{part.text}</code>;
      if (part.type === "strong") return <strong key={index}>{part.text}</strong>;
      if (part.type === "em") return <em key={index}>{part.text}</em>;
      if (part.type === "image")
        return <img key={index} className="sp-document-image" src={part.src} alt={part.text} loading="lazy" decoding="async" />;
      if (part.type === "link")
        return (
          <a key={index} href={part.href}>
            {part.text}
          </a>
        );
      return <Fragment key={index}>{part.text}</Fragment>;
    });
  };
  const content = documentBlocks(text).map((block, index) => {
    if (--remaining < 0 || block.type === "plain") {
      remaining = -1;
      return null;
    }
    if (block.type === "heading") {
      const Heading = block.level > 2 ? "h3" : "h2";
      return (
        <Heading key={index} id={anchors ? block.id : undefined}>
          {inline(block.text)}
        </Heading>
      );
    }
    if (block.type === "code")
      return (
        <pre key={index}>
          <code>{block.text}</code>
        </pre>
      );
    if (block.type === "quote")
      return <blockquote key={index}>{inline(block.text)}</blockquote>;
    if (block.type === "rule") return <hr key={index} />;
    if (block.type === "table") {
      remaining -= 3 + block.header.length
        + (block.rows.length ? 1 : 0)
        + block.rows.length * (1 + block.header.length);
      return (
        <div className="sp-markdown-table-scroll" key={index} role="region" aria-label="Table" tabIndex={0}>
          <table>
            <thead>
              <tr>
                {block.header.map((cell, column) => (
                  <th key={column} scope="col" style={{ textAlign: block.align[column] ?? undefined }}>
                    {inline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            {block.rows.length > 0 && (
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, column) => (
                      <td key={column} style={{ textAlign: block.align[column] ?? undefined }}>
                        {inline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            )}
          </table>
        </div>
      );
    }
    if (block.type === "list") {
      remaining -= block.items.length;
      const List = block.ordered ? "ol" : "ul";
      return (
        <List key={index}>
          {block.items.map((item, i) => (
            <li key={i}>{inline(item)}</li>
          ))}
        </List>
      );
    }
    return <p key={index}>{inline(block.text)}</p>;
  });
  return (
    <div className="sp-prose sp-document-prose">
      {remaining < 0 ? (
        <>
          <p>Shown as plain text because this content has extensive formatting.</p>
          <pre>{text}</pre>
        </>
      ) : (
        content
      )}
    </div>
  );
}
