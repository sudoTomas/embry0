import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Element styles for an issue description. react-markdown builds React
// elements from the Markdown tree and never parses raw HTML (no rehype-raw),
// so a <script> or <img onerror> in a body stays inert text, and its default
// URL transform drops javascript: links.
const components: Components = {
  h1: ({ children }) => <h3 className="mt-4 mb-2 text-section font-semibold first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-4 mb-2 text-section font-semibold first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h4>,
  h4: ({ children }) => <h4 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h4>,
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc pl-5 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal pl-5 space-y-1">{children}</ol>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground">
      {children}
    </a>
  ),
  code: ({ children }) => <code className="rounded bg-overlay px-1 py-0.5 font-mono text-xs">{children}</code>,
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-md border border-border bg-overlay p-3 font-mono text-xs [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-border pl-3 text-fg-secondary">{children}</blockquote>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="data-table">{children}</table>
    </div>
  ),
};

/** An issue's description, rendered as GitHub-flavoured Markdown. */
export function IssueBody({ body }: { body: string }) {
  return (
    <div className="text-sm text-foreground/90 break-words leading-relaxed">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {body}
      </Markdown>
    </div>
  );
}
