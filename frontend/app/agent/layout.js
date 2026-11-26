import AgentSidebar from "../components/Sidebar";
import StaffAuthGate from "../components/StaffAuthGate";

export const metadata = {
  title: "Agent Dashboard — Support Team",
  description: "Internal support team dashboard for monitoring, tickets, and analytics.",
};

export default function AgentLayout({ children }) {
  return (
    <StaffAuthGate>
      <div className="flex min-h-screen">
        <AgentSidebar />
        <main className="flex-1 ml-64 min-h-screen">{children}</main>
      </div>
    </StaffAuthGate>
  );
}
