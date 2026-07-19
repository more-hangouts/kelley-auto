/**
 * Minimal Lexical rich text → HTML converter for simple formatted text.
 * Handles paragraphs, bold, italic, underline, links.
 * Safe for server components — no Payload imports.
 */

type LexicalNode = {
  type?: string;
  tag?: string;
  text?: string;
  format?: number | string;
  children?: LexicalNode[];
  fields?: { url?: string; newTab?: boolean };
  url?: string;
  newTab?: boolean;
};

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderNode(node: LexicalNode): string {
  // Text node
  if (node.type === "text" && typeof node.text === "string") {
    let html = escapeHtml(node.text);
    const fmt = typeof node.format === "number" ? node.format : 0;
    if (fmt & 1) html = `<strong>${html}</strong>`; // bold
    if (fmt & 2) html = `<em>${html}</em>`; // italic
    if (fmt & 8) html = `<u>${html}</u>`; // underline
    return html;
  }

  // Linebreak
  if (node.type === "linebreak") return "<br/>";

  // Recursive children
  const inner = (node.children || []).map(renderNode).join("");

  // Link
  if (node.type === "link") {
    const url = node.fields?.url || node.url || "#";
    const target = (node.fields?.newTab || node.newTab) ? ' target="_blank" rel="noopener"' : "";
    return `<a href="${escapeHtml(url)}"${target}>${inner}</a>`;
  }

  // Heading
  if (node.type === "heading" && node.tag) {
    return `<${node.tag}>${inner}</${node.tag}>`;
  }

  // Paragraph
  if (node.type === "paragraph") return `<p>${inner}</p>`;

  // List
  if (node.type === "list") {
    const tag = node.tag === "ol" ? "ol" : "ul";
    return `<${tag}>${inner}</${tag}>`;
  }
  if (node.type === "listitem") return `<li>${inner}</li>`;

  // Fallback — just return inner content
  return inner;
}

/**
 * Convert a Payload/Lexical rich text value to an HTML string.
 * Returns empty string if the value is empty/null.
 */
export function lexicalToHtml(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const root = (value as { root?: LexicalNode }).root;
  if (!root || !Array.isArray(root.children)) return "";
  return root.children.map(renderNode).join("");
}
