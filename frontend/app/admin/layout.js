import AdminSidebar from "../components/AdminSidebar";
import StaffAuthGate from "../components/StaffAuthGate";

export const metadata = {
  title: "Tech Admin — Support Team",
  description: "System administration and technical maintenance dashboard.",
};

export default function AdminLayout({ children }) {
  return (
    <StaffAuthGate adminOnly>
      <div className="flex min-h-screen">
        <AdminSidebar />
        <main className="flex-1 ml-64 min-h-screen bg-[var(--bg-primary)]">
          {children}
        </main>
      </div>
    </StaffAuthGate>
  );
}
