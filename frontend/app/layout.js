import "./globals.css";

export const metadata = {
  title: "Support Team — Multi-Agent Customer Support",
  description:
    "Intelligent multi-agent customer support system powered by LangGraph, MCP, and RAG.",
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col bg-[var(--bg-primary)]">
        {children}
      </body>
    </html>
  );
}
