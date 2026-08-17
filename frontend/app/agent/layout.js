import AgentSidebar from "../components/Sidebar";

export const metadata = {
  title: "Agent Dashboard — Project Bestie",
  description: "Internal support team dashboard for monitoring, tickets, and analytics.",
};

export default function AgentLayout({ children }) {
  return (
    <div className="flex min-h-screen">
      <AgentSidebar />
      <main className="flex-1 ml-64 min-h-screen">{children}</main>
    </div>
  );
}
