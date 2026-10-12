export function parseMarkdownSections(text: string): Record<string, string> {
  const result = Object.create(null) as Record<string, string>;
  for (const part of text.split(/^## /m).slice(1)) {
    const nl = part.indexOf("\n");
    const heading = nl === -1 ? part.trim() : part.slice(0, nl).trim();
    const body = nl === -1 ? "" : part.slice(nl + 1).trim();
    if (heading && body) result[heading] = body;
  }
  return result;
}
